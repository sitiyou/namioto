# SPDX-License-Identifier: AGPL-3.0-only
"""Forced alignment of known lyrics against a separated vocal: wav2vec2 CTC posteriors + Viterbi.

A port of `whisperx.align()` (BSD-2-Clause, see NOTICE) to numpy and ONNX Runtime, with the parts
the alignment itself does not need left out: torch, pandas and transformers go, sentence splitting
goes with them (the caller supplies one window per line), and the model arrives as a converted ONNX
graph. What stays is the algorithm: `get_trellis` scores every (frame, character) pair of a line,
`backtrack` walks the text as the only path it may take, and `merge_repeats` turns each character's
frames into one span. A character the model's dictionary lacks still has to sit somewhere, so it
gets a wildcard column scored by the best non-blank frame.

The caller passes a window per line; the aligner can only refine a window, never find one, and a
window that does not hold the line's voice still comes back with times, from a path the model
barely supports - which is what `problems()` is for. Only `Char.start` is worth reading: a CTC
character's end is the next character's onset, so the last one before a rest reaches into the rest.

The model is a wav2vec2 CTC graph (`model.onnx`, input `input_values`, output `logits`) beside the
dictionary it was trained with (`vocab.json`), both produced by `scripts/export_align_model.py` and
read from a directory given by `$NAMIOTO_ALIGN_MODEL` or the data directory.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

import librosa
import numpy as np
import onnxruntime as ort
import platformdirs

SAMPLE_RATE = 16000
# wav2vec2's convolutional front end rejects anything shorter than 400 samples
MIN_SAMPLES = 400
# characters this close together in a row are one character's frames spread over several, not a
# fast line; a single close pair is a zero-mora symbol or a punctuation mark and means nothing
COLLAPSE_SECONDS = 0.04
COLLAPSE_RUN = 3
# a line whose median error against reference times is past this is worth running again, not keeping
DIVERGE_SECONDS = 0.1
MODEL_FILE = "model.onnx"
VOCAB_FILE = "vocab.json"
MODEL_ENV = "NAMIOTO_ALIGN_MODEL"
LANGUAGES = ("ja",)
PROVIDERS = {
    "cpu": ("CPUExecutionProvider",),
    "cuda": ("CUDAExecutionProvider", "CPUExecutionProvider"),
}


@dataclass(frozen=True)
class Segment:
    """One line to align: its rough start and end in seconds, and the text between them."""

    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Char:
    """One input character. `start`, `end` and `score` are None where it could not be aligned."""

    char: str
    start: float | None = None
    end: float | None = None
    score: float | None = None


@dataclass(frozen=True)
class AlignedSegment:
    """A line with one `Char` per input character, spaces and unaligned characters included."""

    text: str
    start: float
    end: float
    chars: tuple[Char, ...]


def models_root() -> pathlib.Path:
    """Where a converted aligner lives: the platform's data directory, beside GAME's models."""
    return pathlib.Path(platformdirs.user_data_dir("namioto")) / "models" / "wav2vec2"


def model_dir(language: str = "ja") -> pathlib.Path:
    if language not in LANGUAGES:
        raise ValueError(f"no aligner for {language!r}, expected one of {', '.join(LANGUAGES)}")
    return models_root() / language


def is_installed(language: str = "ja") -> bool:
    directory = model_dir(language)
    return (directory / MODEL_FILE).is_file() and (directory / VOCAB_FILE).is_file()


def resolve_model(path: str | pathlib.Path | None = None, language: str = "ja") -> pathlib.Path:
    """The directory holding `model.onnx` and `vocab.json`.

    An explicit path wins, then `$NAMIOTO_ALIGN_MODEL`, then the installed language; a file is read
    as living in the directory beside its dictionary.
    """
    if path is not None:
        candidate = pathlib.Path(path)
        return candidate if candidate.is_dir() else candidate.parent
    from_env = os.environ.get(MODEL_ENV)
    if from_env:
        return pathlib.Path(from_env)
    if is_installed(language):
        return model_dir(language)
    raise FileNotFoundError(
        f"no aligner in {model_dir(language)}; convert one with scripts/export_align_model.py "
        f"or point {MODEL_ENV} at a directory holding {MODEL_FILE} and {VOCAB_FILE}"
    )


def load_dictionary(path: str | pathlib.Path) -> tuple[dict[str, int], int]:
    """The character codes of a `vocab.json`, lower-cased as the lookup expects, and the blank id."""
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        raise ValueError(f"{path} is not a character dictionary")
    dictionary = {str(char).lower(): int(code) for char, code in data.items()}
    return dictionary, dictionary.get("<pad>", dictionary.get("[pad]", 0))


def load_audio(path: str | pathlib.Path) -> np.ndarray:
    """A file as mono float32 at `SAMPLE_RATE`, the rate the aligner's model was trained on."""
    return librosa.load(path, sr=SAMPLE_RATE, mono=True)[0]


class Backend(Protocol):
    """What alignment needs of a CTC model: one window in, `[frames, vocabulary]` out."""

    def logits(self, waveform: np.ndarray) -> np.ndarray: ...


class OnnxBackend:
    """A converted CTC model: `logits(waveform)` is `[frames, vocabulary]` for one window."""

    def __init__(self, path: str | pathlib.Path, provider: str = "cpu"):
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider {provider!r}, expected one of {', '.join(PROVIDERS)}")
        self.session = ort.InferenceSession(str(path), providers=list(PROVIDERS[provider]))
        self.input = self.session.get_inputs()[0].name

    def logits(self, waveform: np.ndarray) -> np.ndarray:
        values = np.asarray(waveform, dtype=np.float32)[None, :]
        return self.session.run(None, {self.input: values})[0][0]


def log_softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - values.max(axis=-1, keepdims=True)
    return shifted - np.log(np.exp(shifted).sum(axis=-1, keepdims=True))


def get_trellis(emission: np.ndarray, tokens: Sequence[int], blank_id: int = 0) -> np.ndarray:
    """The best score of reaching (frame, token) by staying on a token or moving to the next.

    Row `t + 1` is frame `t` and column `j + 1` is token `j`, with column 0 the blank the path runs
    on between characters. The blank column's last frames are set to infinity, which is the force
    that is meant to have spent every character by the time the window ends.
    """
    frames, count = emission.shape[0], len(tokens)
    trellis = np.empty((frames + 1, count + 1), dtype=emission.dtype)
    trellis[0, 0] = 0
    trellis[1:, 0] = np.cumsum(emission[:, blank_id], 0)
    trellis[0, -count:] = -np.inf
    trellis[-count:, 0] = np.inf
    for t in range(frames):
        trellis[t + 1, 1:] = np.maximum(
            trellis[t, 1:] + emission[t, blank_id],
            trellis[t, :-1] + emission[t, tokens],
        )
    return trellis


def backtrack(
    trellis: np.ndarray, emission: np.ndarray, tokens: Sequence[int], blank_id: int = 0
) -> list[tuple[int, int, float]] | None:
    """The best path as `(token, frame, probability)` from the first token to the last, or None.

    None means no path reaches the first token, which takes a window the model scored as impossible.
    """
    j = trellis.shape[1] - 1
    start = int(np.argmax(trellis[:, j]))
    path: list[tuple[int, int, float]] = []
    for t in range(start, 0, -1):
        stayed = trellis[t - 1, j] + emission[t - 1, blank_id]
        changed = trellis[t - 1, j - 1] + emission[t - 1, tokens[j - 1]]
        prob = emission[t - 1, tokens[j - 1] if changed > stayed else blank_id]
        path.append((j - 1, t - 1, float(np.exp(prob))))
        if changed > stayed:
            j -= 1
            if j == 0:
                break
    else:
        return None
    return path[::-1]


def merge_repeats(path: Sequence[tuple[int, int, float]], transcript: str) -> list[tuple[str, int, int, float]]:
    """One `(character, first frame, last frame + 1, mean probability)` per run of a token."""
    i1, i2 = 0, 0
    segments = []
    while i1 < len(path):
        while i2 < len(path) and path[i1][0] == path[i2][0]:
            i2 += 1
        score = sum(path[k][2] for k in range(i1, i2)) / (i2 - i1)
        segments.append((transcript[path[i1][0]], path[i1][1], path[i2 - 1][1] + 1, score))
        i1 = i2
    return segments


def align(
    segments: Iterable[Segment],
    backend: Backend,
    dictionary: Mapping[str, int],
    audio: np.ndarray,
    *,
    blank_id: int = 0,
    progress: Callable[[int, int], None] | None = None,
) -> list[AlignedSegment]:
    """Align every line's characters to `audio` and give each one its span.

    `dictionary` maps lower-cased characters to their codes; a character outside it is aligned
    through a wildcard column, an error `problems()` reports rather than hides.
    """
    lines = list(segments)
    aligned = []
    for index, segment in enumerate(lines):
        aligned.append(_align_one(segment, backend, dictionary, audio, blank_id))
        if progress is not None:
            progress(index + 1, len(lines))
    return aligned


def _align_one(
    segment: Segment,
    backend: Backend,
    dictionary: Mapping[str, int],
    audio: np.ndarray,
    blank_id: int,
) -> AlignedSegment:
    chars = [Char(char) for char in segment.text]
    index = [cdx for cdx, char in enumerate(segment.text) if not char.isspace()]
    if index:
        waveform, offset, duration = _window(segment, audio)
        if duration > 0:
            emission = log_softmax(backend.logits(waveform))
            text_clean = "".join(segment.text[cdx].lower() for cdx in index)
            if any(char not in dictionary for char in text_clean):
                non_blank = np.ones(emission.shape[1], dtype=bool)
                non_blank[blank_id] = False
                emission = np.concatenate([emission, emission[:, non_blank].max(axis=1)[:, None]], axis=1)
            tokens = [dictionary.get(char, emission.shape[1] - 1) for char in text_clean]
            trellis = get_trellis(emission, tokens, blank_id)
            path = backtrack(trellis, emission, tokens, blank_id)
            if path is not None:
                ratio = duration / (trellis.shape[0] - 1)
                spans = merge_repeats(path, text_clean)
                for cdx, (_, start, end, score) in zip(index, spans, strict=True):
                    chars[cdx] = Char(
                        segment.text[cdx],
                        round(start * ratio + offset, 3),
                        round(end * ratio + offset, 3),
                        round(score, 3),
                    )
    return AlignedSegment(segment.text, segment.start, segment.end, tuple(chars))


def _window(segment: Segment, audio: np.ndarray) -> tuple[np.ndarray, float, float]:
    """The audio of one line, padded up to the model's minimum, with its own start and duration."""
    first = min(max(int(segment.start * SAMPLE_RATE), 0), audio.size)
    last = min(max(int(segment.end * SAMPLE_RATE), first), audio.size)
    window = audio[first:last]
    if window.size < MIN_SAMPLES:
        window = np.pad(window, (0, MIN_SAMPLES - window.size))
    return window, first / SAMPLE_RATE, (last - first) / SAMPLE_RATE


def problems(segment: AlignedSegment, reference: Sequence[float | None] | None = None) -> tuple[str, ...]:
    """The failure shapes of one aligned line: `empty`, `nonmonotonic`, `collapsed`, `diverged`.

    `reference` is the onset already known for each of the line's characters, None where there is
    none; the times an existing MIDI or subtitle carries are what makes `diverged` possible, and a
    line wearing it is the one to run again with another window before believing it.
    """
    starts = [char.start for char in segment.chars if char.start is not None]
    if not starts:
        return ("empty",)
    found = []
    if any(later <= earlier for earlier, later in zip(starts, starts[1:], strict=False)):
        found.append("nonmonotonic")
    run = 0
    for earlier, later in zip(starts, starts[1:], strict=False):
        run = run + 1 if later - earlier < COLLAPSE_SECONDS else 0
        if run >= COLLAPSE_RUN:
            found.append("collapsed")
            break
    if reference is not None:
        errors = [
            char.start - expected
            for char, expected in zip(segment.chars, reference, strict=False)
            if char.start is not None and expected is not None
        ]
        if errors and float(np.median(np.abs(errors))) > DIVERGE_SECONDS:
            found.append("diverged")
    return tuple(found)


def read_segments(path: str | pathlib.Path) -> list[Segment]:
    """The lines of a JSON file: a list of `{start, end, text}`, or an object holding one."""
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("segments")
    if not isinstance(data, list):
        raise ValueError(f"{path} holds no list of segments")
    return [Segment(float(item["start"]), float(item["end"]), str(item["text"])) for item in data]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", help="the separated vocal to align against")
    parser.add_argument("segments", type=pathlib.Path, help="JSON list of {start, end, text}, one entry per line")
    parser.add_argument(
        "--model", type=pathlib.Path, help=f"model directory (default: ${MODEL_ENV} or the data directory)"
    )
    parser.add_argument("--language", choices=LANGUAGES, default="ja", help="which aligner to use")
    parser.add_argument("--provider", choices=tuple(PROVIDERS), default="cpu", help="where the model runs")
    parser.add_argument("--out", type=pathlib.Path, help="write the alignment here instead of to stdout")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        directory = resolve_model(args.model, args.language)
        backend = OnnxBackend(directory / MODEL_FILE, provider=args.provider)
        dictionary, blank_id = load_dictionary(directory / VOCAB_FILE)
        segments = read_segments(args.segments)
    except (OSError, ValueError, KeyError) as error:
        print(f"cannot align: {error}", file=sys.stderr)
        return 2

    aligned = align(
        segments,
        backend,
        dictionary,
        load_audio(args.audio),
        blank_id=blank_id,
        progress=lambda done, total: print(f"  lines {done}/{total}", file=sys.stderr),
    )
    for segment in aligned:
        found = problems(segment)
        if found:
            print(f"  {', '.join(found)}: {segment.text}", file=sys.stderr)

    payload = {
        "audio": str(args.audio),
        "segments": [
            {
                "start": segment.start,
                "end": segment.end,
                "text": segment.text,
                "problems": list(problems(segment)),
                "chars": [
                    {"char": char.char, "start": char.start, "end": char.end, "score": char.score}
                    for char in segment.chars
                ],
            }
            for segment in aligned
        ],
    }
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"written {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
