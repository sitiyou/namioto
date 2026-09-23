# SPDX-License-Identifier: AGPL-3.0-only
"""Forced alignment of known lyrics against a separated vocal: CTC posteriors + Viterbi.

The core is FA-Kara's (MIT, see NOTICE): a line's tokens are forced onto the frames of a wav2vec2
CTC model, and each token gets the span of frames it won. The model never recognises anything, it
only says where the tokens it is given fall, and it arrives as a converted ONNX graph, so torch and
torchaudio stay out of the program. Two models can be exported: `mms` (Meta's MMS forced-alignment
checkpoint) and `yohane` (`NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn`). Both read a romanised
lyrics; `namioto.utils.kana_tokens` turns a `.krc` line's kana into those tokens.

The alignment itself is a numpy port of torchaudio's `forced_align`/`merge_tokens` (BSD-2-Clause,
see NOTICE). A line's tokens are flattened into their characters, each character is one CTC target,
and a target's frames are merged into its span; the spans are grouped back into one per token. Only
a token's start is worth reading: a CTC target's end is the next target's onset, so the last one
before a rest reaches into the rest.

The caller passes a window per line; the aligner can only refine a window, never find one, and a
window that does not hold the line's voice still comes back with times, from a path the model
barely supports - which is what `problems()` is for.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import time
import warnings
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import librosa
import numpy as np
import onnxruntime as ort
import platformdirs

from namioto import settings as store
from namioto.settings import Field

SAMPLE_RATE = 16000
# the convolutional front end strides 320 samples into one frame in both models, so a frame is
# always 20 ms of the window it came from
FRAME_SAMPLES = 320
FRAME_SECONDS = FRAME_SAMPLES / SAMPLE_RATE
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
MODELS = ("mms", "yohane")
DEFAULT_MODEL = "mms"
PROVIDERS = {
    "cpu": ("CPUExecutionProvider",),
    "cuda": ("CUDAExecutionProvider", "CPUExecutionProvider"),
}
# the cells per quarter note the window offers to snap with; a label is the note that cell is
QUANTIZE_CELLS = (0, 1, 2, 4, 8, 16, 32)
QUANTIZE_LABELS = ("Off", "1/4", "1/8", "1/16", "1/32", "1/64", "1/128")

# What the alignment window remembers between runs: the last choices, in a file of their own beside
# settings.json. Nothing else is kept - the times live in the project, the runs in the data side.
PARAMETER_FILE = "align.json"
PARAMETERS: tuple[Field, ...] = (
    Field("model", "choice", DEFAULT_MODEL, "Model", "Which forced-alignment model to use", choices=MODELS),
    Field("provider", "choice", "cpu", "Device", "Where the model runs", choices=tuple(PROVIDERS)),
    Field(
        "quantize",
        "choice",
        0,
        "Quantize",
        "Snap the times to the beat grid, this many cells per quarter note",
        choices=QUANTIZE_CELLS,
        labels=QUANTIZE_LABELS,
    ),
)


@dataclass(frozen=True)
class Segment:
    """One line to align: its rough start and end in seconds, and its tokens."""

    start: float
    end: float
    tokens: tuple[str, ...]


@dataclass(frozen=True)
class Token:
    """One input token. `start`, `end` and `score` are None where it could not be aligned."""

    text: str
    start: float | None = None
    end: float | None = None
    score: float | None = None


@dataclass(frozen=True)
class AlignedSegment:
    """A line with one `Token` per input token, in input order."""

    start: float
    end: float
    tokens: tuple[Token, ...]


def models_root() -> pathlib.Path:
    """Where a converted aligner lives: the platform's data directory, beside GAME's models."""
    return pathlib.Path(platformdirs.user_data_dir("namioto")) / "models"


def model_dir(model: str = DEFAULT_MODEL, language: str = "ja") -> pathlib.Path:
    if model not in MODELS:
        raise ValueError(f"no aligner named {model!r}, expected one of {', '.join(MODELS)}")
    if language not in LANGUAGES:
        raise ValueError(f"no aligner for {language!r}, expected one of {', '.join(LANGUAGES)}")
    return models_root() / model / language


def is_installed(model: str = DEFAULT_MODEL, language: str = "ja") -> bool:
    directory = model_dir(model, language)
    return (directory / MODEL_FILE).is_file() and (directory / VOCAB_FILE).is_file()


def _require(directory: pathlib.Path) -> pathlib.Path:
    """`directory` once it holds a converted model, else FileNotFoundError naming what is missing."""
    if not (directory / MODEL_FILE).is_file() or not (directory / VOCAB_FILE).is_file():
        raise FileNotFoundError(f"{directory} does not hold {MODEL_FILE} and {VOCAB_FILE}")
    return directory


def resolve_model(
    path: str | pathlib.Path | None = None, model: str = DEFAULT_MODEL, language: str = "ja"
) -> pathlib.Path:
    """The directory holding `model.onnx` and `vocab.json`, which must exist.

    An explicit path wins, then `$NAMIOTO_ALIGN_MODEL`, then the installed model; a file is read as
    living in the directory beside its dictionary.
    """
    if path is not None:
        candidate = pathlib.Path(path)
        return _require(candidate if candidate.is_dir() else candidate.parent)
    from_env = os.environ.get(MODEL_ENV)
    if from_env:
        return _require(pathlib.Path(from_env))
    directory = model_dir(model, language)
    if not is_installed(model, language):
        raise FileNotFoundError(
            f"no {model} aligner in {directory}; convert one with scripts/export_align_model.py "
            f"or point {MODEL_ENV} at a directory holding {MODEL_FILE} and {VOCAB_FILE}"
        )
    return directory


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


# An alignment is a derived result, not a preference: the store is a cache the next run may re-make
# from the model, so a file that is missing or unreadable simply means there is nothing to reuse.
ALIGNMENTS_DIR = "alignments"
ALIGNMENT_STORE_VERSION = 1


def parameter_path() -> pathlib.Path:
    """The preferences side of the tree: the window's remembered choices, beside settings.json."""
    return pathlib.Path(platformdirs.user_config_dir("namioto")) / PARAMETER_FILE


def default_parameters() -> dict[str, Any]:
    return {item.name: item.default for item in PARAMETERS}


def coerce_parameters(values: Any) -> dict[str, Any]:
    """A file may hold anything at all, so every value goes through its field's own check."""
    if not isinstance(values, dict):
        return default_parameters()
    return {item.name: store.coerce(item, values.get(item.name, item.default)) for item in PARAMETERS}


def load_parameters() -> dict[str, Any]:
    """What the window opens with: the last run's choices, or the defaults for anything unreadable."""
    try:
        data = json.loads(parameter_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default_parameters()
    except (OSError, ValueError) as error:
        warnings.warn(f"{parameter_path()} could not be read ({error}); defaults are in use", stacklevel=2)
        return default_parameters()
    return coerce_parameters(data)


def save_parameters(values: Any) -> pathlib.Path:
    return store.write_json(coerce_parameters(values), parameter_path())


def alignments_root() -> pathlib.Path:
    """Where a cached alignment lives: the platform's data directory, beside the models."""
    return pathlib.Path(platformdirs.user_data_dir("namioto")) / ALIGNMENTS_DIR


def _audio_stamp(path: str | pathlib.Path) -> tuple[int, int]:
    """Size and mtime, so the same path holding different audio is not the same audio."""
    try:
        info = pathlib.Path(path).expanduser().resolve().stat()
    except OSError:
        return (0, 0)
    return (info.st_size, info.st_mtime_ns)


def _store_path(audio: str | pathlib.Path) -> pathlib.Path:
    digest = hashlib.sha1(str(pathlib.Path(audio).expanduser().resolve()).encode("utf-8")).hexdigest()
    return alignments_root() / f"{digest}.json"


def alignment_key(audio: str | pathlib.Path, model: str, provider: str, text: str) -> str:
    """What makes two alignments the same one: the audio, the model, where it runs, and the lyrics.

    The tempo and the grid offset are not part of it: they only snap the result, so changing either
    re-snaps a stored alignment instead of running the model again.
    """
    identity = {"audio": list(_audio_stamp(audio)), "model": model, "provider": provider, "text": text}
    return hashlib.sha1(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()


def _stored_rows(raw) -> list[list[tuple[float | None, float | None]]] | None:
    try:
        return [
            [(None if start is None else float(start), None if end is None else float(end)) for start, end in row]
            for row in raw
        ]
    except (TypeError, ValueError):
        return None


def load_alignments(audio: str | pathlib.Path) -> dict[str, dict]:
    """Every alignment saved for one audio file, keyed by alignment key; nothing usable means none."""
    try:
        data = json.loads(_store_path(audio).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != ALIGNMENT_STORE_VERSION:
        return {}
    entries = data.get("alignments")
    if not isinstance(entries, dict):
        return {}
    return {key: entry for key, entry in entries.items() if isinstance(entry, dict)}


def find_alignment(audio: str | pathlib.Path, model: str, provider: str, text: str):
    """The lines and doubts an alignment with these exact inputs already found, or None when there is none."""
    entry = load_alignments(audio).get(alignment_key(audio, model, provider, text))
    if not entry:
        return None
    rows = _stored_rows(entry.get("rows"))
    if rows is None:
        return None
    problems = entry.get("problems")
    return rows, [str(problem) for problem in problems] if isinstance(problems, list) else []


def save_alignment(audio: str | pathlib.Path, model: str, provider: str, text: str, rows, problems) -> pathlib.Path:
    """Keep one entry per alignment key: running the same one again replaces what it found last time."""
    entries = load_alignments(audio)
    entries[alignment_key(audio, model, provider, text)] = {
        "at": int(time.time()),
        "model": model,
        "rows": [[[start, end] for start, end in row] for row in rows],
        "problems": [str(problem) for problem in problems],
    }
    target = _store_path(audio)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps({"version": ALIGNMENT_STORE_VERSION, "audio": str(audio), "alignments": entries}, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


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


def forced_align(
    emission: np.ndarray, tokens: Sequence[int], blank_id: int = 0
) -> tuple[np.ndarray, np.ndarray] | None:
    """The best frame path through the CTC graph, as a token id and a log-probability per frame.

    The graph alternates the blank with each target token; a token may follow itself only across a
    blank. The path is forced to end on the last token (or the blank after it), so every target is
    visited. None means the window has fewer frames than the targets and their repeats need.
    """
    frames, count = emission.shape[0], len(tokens)
    if count == 0:
        return np.full(frames, blank_id, dtype=np.int64), emission[:, blank_id].copy()
    repeats = sum(1 for earlier, later in zip(tokens, tokens[1:], strict=False) if earlier == later)
    if frames < count + repeats:
        return None

    states = 2 * count + 1
    labels = np.full(states, blank_id, dtype=np.int64)
    labels[1::2] = tokens
    skip = np.zeros(states, dtype=bool)
    skip[2:] = (labels[2:] != blank_id) & (labels[2:] != labels[:-2])

    alpha = np.full((frames, states), -np.inf, dtype=np.float64)
    back = np.zeros((frames, states), dtype=np.int8)
    alpha[0, 0] = emission[0, blank_id]
    alpha[0, 1] = emission[0, labels[1]]
    positions = np.arange(states)
    for t in range(1, frames):
        previous = alpha[t - 1]
        stay = previous
        step = np.concatenate(([-np.inf], previous[:-1]))
        over = np.full(states, -np.inf)
        over[skip] = previous[positions[skip] - 2]
        choice = np.where(over > np.maximum(stay, step), 2, np.where(step > np.maximum(stay, over), 1, 0))
        best = np.where(choice == 2, over, np.where(choice == 1, step, stay))
        alpha[t] = best + emission[t, labels]
        back[t] = choice

    state = states - 1 if alpha[-1, states - 1] > alpha[-1, states - 2] else states - 2
    path = np.empty(frames, dtype=np.int64)
    for t in range(frames - 1, -1, -1):
        path[t] = labels[state]
        state -= int(back[t, state])
    return path, emission[np.arange(frames), path]


def merge_tokens(path: np.ndarray, scores: np.ndarray, blank_id: int = 0) -> list[tuple[int, int, int, float]]:
    """One `(token id, first frame, last frame + 1, mean score)` per run of a non-blank token."""
    spans = []
    start = 0
    frames = len(path)
    while start < frames:
        if path[start] == blank_id:
            start += 1
            continue
        end = start + 1
        while end < frames and path[end] == path[start]:
            end += 1
        spans.append((int(path[start]), start, end, float(scores[start:end].mean())))
        start = end
    return spans


def align(
    segments: Iterable[Segment],
    backend: Backend,
    dictionary: Mapping[str, int],
    audio: np.ndarray,
    *,
    blank_id: int = 0,
    progress: Callable[[int, int], None] | None = None,
) -> list[AlignedSegment]:
    """Align every line's tokens to `audio` and give each token its span.

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
    lengths = [len(token) for token in segment.tokens]
    codes = [dictionary.get(char.lower(), -1) for token in segment.tokens for char in token]
    tokens: list[Token] = [Token(text) for text in segment.tokens]
    if any(length for length in lengths):
        waveform, offset = _window(segment, audio)
        if waveform is not None:
            emission = log_softmax(backend.logits(waveform))
            unknown = [code < 0 or code == blank_id for code in codes]
            if any(unknown):
                non_blank = np.ones(emission.shape[1], dtype=bool)
                non_blank[blank_id] = False
                emission = np.concatenate([emission, emission[:, non_blank].max(axis=1)[:, None]], axis=1)
                wildcard = emission.shape[1] - 1
                codes = [wildcard if missing else code for code, missing in zip(codes, unknown, strict=True)]
            found = forced_align(emission, codes, blank_id)
            if found is not None:
                spans = merge_tokens(found[0], found[1], blank_id)
                if len(spans) == len(codes):
                    at = 0
                    for index, length in enumerate(lengths):
                        chunk = spans[at : at + length]
                        at += length
                        if not chunk:
                            continue
                        start = chunk[0][1] * FRAME_SECONDS + offset
                        end = chunk[-1][2] * FRAME_SECONDS + offset
                        score = float(np.exp(sum(span[3] for span in chunk) / len(chunk)))
                        tokens[index] = Token(segment.tokens[index], round(start, 3), round(end, 3), round(score, 3))
    return AlignedSegment(segment.start, segment.end, tuple(tokens))


def _window(segment: Segment, audio: np.ndarray) -> tuple[np.ndarray | None, float]:
    """The audio of one line padded up to the model's minimum, or None for an empty window."""
    first = min(max(int(segment.start * SAMPLE_RATE), 0), audio.size)
    last = min(max(int(segment.end * SAMPLE_RATE), first), audio.size)
    if last <= first:
        return None, first / SAMPLE_RATE
    window = audio[first:last]
    if window.size < MIN_SAMPLES:
        window = np.pad(window, (0, MIN_SAMPLES - window.size))
    return window, first / SAMPLE_RATE


def problems(segment: AlignedSegment, reference: Sequence[float | None] | None = None) -> tuple[str, ...]:
    """The failure shapes of one aligned line: `empty`, `nonmonotonic`, `collapsed`, `diverged`.

    `reference` is the onset already known for each of the line's tokens, None where there is none;
    the times an existing MIDI or subtitle carries are what makes `diverged` possible, and a line
    wearing it is the one to run again with another window before believing it.
    """
    starts = [token.start for token in segment.tokens if token.start is not None]
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
            token.start - expected
            for token, expected in zip(segment.tokens, reference, strict=False)
            if token.start is not None and expected is not None
        ]
        if errors and float(np.median(np.abs(errors))) > DIVERGE_SECONDS:
            found.append("diverged")
    return tuple(found)


def read_segments(path: str | pathlib.Path) -> list[Segment]:
    """The lines of a JSON file: a list of `{start, end, tokens}`, or an object holding one."""
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("segments")
    if not isinstance(data, list):
        raise ValueError(f"{path} holds no list of segments")
    return [
        Segment(float(item["start"]), float(item["end"]), tuple(str(token) for token in item["tokens"]))
        for item in data
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", help="the separated vocal to align against")
    parser.add_argument("segments", type=pathlib.Path, help="JSON list of {start, end, tokens}, one entry per line")
    parser.add_argument(
        "--dir", type=pathlib.Path, help=f"model directory (default: ${MODEL_ENV} or the data directory)"
    )
    parser.add_argument("--model", choices=MODELS, default=DEFAULT_MODEL, help="which aligner to use")
    parser.add_argument("--language", choices=LANGUAGES, default="ja", help="which language model to use")
    parser.add_argument("--provider", choices=tuple(PROVIDERS), default="cpu", help="where the model runs")
    parser.add_argument("--out", type=pathlib.Path, help="write the alignment here instead of to stdout")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        directory = resolve_model(args.dir, args.model, args.language)
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
            print(f"  {', '.join(found)}: {' '.join(token.text for token in segment.tokens)}", file=sys.stderr)

    payload = {
        "audio": str(args.audio),
        "segments": [
            {
                "start": segment.start,
                "end": segment.end,
                "problems": list(problems(segment)),
                "tokens": [
                    {"text": token.text, "start": token.start, "end": token.end, "score": token.score}
                    for token in segment.tokens
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
