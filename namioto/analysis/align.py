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
before a rest reaches into the rest. The UI applies a model-specific onset calibration after this
acoustic alignment, because the CTC start and the musical MIDI onset are different events.

A `Segment` gives the CTC search its lyric tokens and time window. The Align UI currently supplies
one whole-song segment: KRC lines have no timestamps, so the UI has no independent line windows to
pass in. This is one global CTC path, separate from how the model computes emissions.

`ChunkedBackend` bounds the wav2vec2 encoder pass, whose cost grows quadratically with frame count.
It runs overlapping audio chunks and keeps only frames that saw context on both sides, then
concatenates those frames into one emission sequence. The `chunk` parameter picks between one
encoder pass, fixed chunks, and chunks whose seams fall in silences (the default). Emissions are
cached (`save_emissions`/`load_emissions`) by audio contents, model, device and chunk mode, so
each Align run searches the emissions with the current lyric readings. The alignment
result is fitted to the voice the way FA-Kara does (`correct_times`).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import pathlib
import sys
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol

import librosa
import numpy as np

from namioto import params
from namioto.analysis import devices, model_store
from namioto.analysis.slicing import Slicer
from namioto.params import Field
from namioto.utils import config_dir, data_dir, write_bytes

SAMPLE_RATE = 16000
# the convolutional front end strides 320 samples into one frame in both models, so a frame is
# always 20 ms of the window it came from
FRAME_SAMPLES = 320
FRAME_SECONDS = FRAME_SAMPLES / SAMPLE_RATE
# wav2vec2's convolutional front end rejects anything shorter than 400 samples
MIN_SAMPLES = 400
# a wav2vec2 graph attends over its whole input, so one pass over a song costs quadratically in
# frames - minutes of audio need tens of gigabytes. The window is cut into overlapping chunks and
# only the frames that saw context on both sides are kept, which bounds a pass without changing the
# global CTC path.
CHUNK_SECONDS = 60.0
CHUNK_OVERLAP_SECONDS = 4.0
# where the seam of a silence-aware chunk lands: the thresholds and run lengths GAME's `Slicer` takes
SLICE_THRESHOLD_DB = -40.0
SLICE_MIN_LENGTH_MS = 1000
SLICE_MIN_INTERVAL_MS = 200
SLICE_MAX_SIL_KEPT_MS = 100
# the energy window the voice detector reads for line tails; a frame counts as voice above a
# fraction of the loud percentile
TAIL_FRAME_SECONDS = 0.02
SILENCE_PERCENTILE = 10
SILENCE_RATIO = 0.1
MODEL_FILE = "model.onnx"
# the half-precision copy a GPU run prefers, written beside it by `--precision fp16`: the WebGPU
# provider runs it several times faster than the fp32 graph and nearly as exactly
FP16_FILE = "model.fp16.onnx"
# the registry entry that copy is published under
FP16_KEY = "aligner_fp16"
# the WebGPU provider holds a reduction's divisor in the element type, so the mean and the variance
# of a whole utterance - more samples than fp16's 65504 - come back as infinities and normalise the
# audio by the wrong scale. Those reductions run on the CPU instead: exact, and one copy of four
# bytes per sample
FP16_CPU_NODES = "/ReduceMean\n/ReduceMean_1\n/ReduceMean_2"
VOCAB_FILE = "vocab.json"
MODEL_ENV = model_store.MODELS["aligner"].env
LANGUAGES = ("ja",)
MODELS = ("mms", "yohane")
DEFAULT_MODEL = "mms"
# CTC localizes the acoustic start of a token, while the MIDI onset usually marks the musical
# vowel/phoneme boundary. These offsets were estimated with leave-one-song-out comparisons on the
# bundled projects; they are model-specific and deliberately live outside the mapping DP.
ONSET_OFFSETS = {
    "mms": {"consonant": 0.04, "vowel": -0.05},
    "yohane": {"consonant": 0.08, "vowel": 0.06},
}
# the ways the model's pass can be cut up: one pass over the whole song, fixed-length chunks, or
# chunks whose seams are pulled into the silences
SLICE_MODES = ("off", "fixed", "silence")
SLICE_LABELS = ("Off", "Fixed chunks", "Silence-aware chunks")
DEFAULT_MODE = "silence"
# What the alignment window remembers between runs: the last choices, in a file of their own beside
# settings.json. Nothing else is kept - the times live in the project, the runs in the data side.
PARAMETER_FILE = "align.json"
PARAMETERS: tuple[Field, ...] = (
    Field("model", "choice", DEFAULT_MODEL, "Model", "Which forced-alignment model to use", choices=MODELS),
    Field(
        "device",
        "choice",
        "cpu",
        "Device",
        "Run on the CPU, or on the GPU backend the settings name; the settings window says which",
        choices=devices.RUN_CHOICES,
        labels=devices.RUN_LABELS,
    ),
    Field(
        "chunk",
        "choice",
        DEFAULT_MODE,
        "Chunked inference",
        "Run the model in overlapping chunks instead of one pass over the whole song, which bounds memory; "
        "the silence-aware cut puts the seams in the rests, the fixed one every minute",
        choices=SLICE_MODES,
        labels=SLICE_LABELS,
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
    """One input token, with its Viterbi span and a posterior interval for its onset."""

    text: str
    start: float | None = None
    end: float | None = None
    score: float | None = None
    onset_interval: tuple[float, float] | None = None


@dataclass(frozen=True)
class AlignedSegment:
    """A line with one `Token` per input token, in input order."""

    start: float
    end: float
    tokens: tuple[Token, ...]
    ctc_log_probability: float | None = None


def _check(model: str, language: str) -> None:
    """A model or language nobody has is a mistake worth raising before touching the disk."""
    if model not in MODELS:
        raise ValueError(f"no aligner named {model!r}, expected one of {', '.join(MODELS)}")
    if language not in LANGUAGES:
        raise ValueError(f"no aligner for {language!r}, expected one of {', '.join(LANGUAGES)}")


def model_dir(model: str = DEFAULT_MODEL, language: str = "ja") -> pathlib.Path:
    _check(model, language)
    return model_store.path("aligner", model, language)


def is_installed(model: str = DEFAULT_MODEL, language: str = "ja") -> bool:
    _check(model, language)
    return model_store.installed("aligner", model, language)


def resolve_model(
    path: str | pathlib.Path | None = None,
    model: str = DEFAULT_MODEL,
    language: str = "ja",
    progress: Callable[[int, int], None] | None = None,
) -> pathlib.Path:
    """The directory holding `model.onnx` and `vocab.json`, which must exist.

    An explicit path or `$NAMIOTO_ALIGN_MODEL` wins, then an installed model, and only then the
    release it is published in, whose download reports `progress(done, total)` bytes; a file is read
    as living in the directory beside its dictionary.
    """
    _check(model, language)
    return model_store.resolve("aligner", path, parts=(model, language), progress=progress)


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


# the model's own pass over the audio depends on the audio alone, so it is cached apart from the
# lyrics: a changed text re-runs only the cheap CTC search, not the wav2vec2 encoder
EMISSIONS_DIR = "emissions"


def parameter_path() -> pathlib.Path:
    """The preferences side of the tree: the window's remembered choices, beside settings.json."""
    return config_dir(PARAMETER_FILE)


def default_parameters() -> dict[str, Any]:
    return params.defaults(PARAMETERS)


def coerce_parameters(values: Any) -> dict[str, Any]:
    return params.coerce_values(PARAMETERS, values)


def load_parameters() -> dict[str, Any]:
    """What the window opens with: the last run's choices, or the defaults for anything unreadable."""
    return params.load_values(parameter_path(), PARAMETERS)


def save_parameters(values: Any) -> pathlib.Path:
    return params.save_values(parameter_path(), PARAMETERS, values)


def emissions_root() -> pathlib.Path:
    """Where a cached model pass lives: the platform's data directory, beside the alignments."""
    return data_dir(EMISSIONS_DIR)


def _emissions_path(key: str) -> pathlib.Path:
    return emissions_root() / f"{key}.npy"


def emissions_key(audio: np.ndarray, model: str, provider: str, chunk: str = DEFAULT_MODE) -> str:
    """Identify the model pass by the exact mono PCM samples and encoder settings."""
    samples = np.ascontiguousarray(audio, dtype="<f4")
    identity = {
        "audio": hashlib.sha256(samples).hexdigest(),
        "model": model,
        "provider": provider,
        "chunk": chunk,
    }
    return hashlib.sha1(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()


def load_emissions(key: str) -> np.ndarray | None:
    """The model's log-softmax pass over the whole audio, or None when it was never kept."""
    try:
        return np.load(_emissions_path(key))
    except (OSError, EOFError, ValueError):
        return None


def save_emissions(key: str, emission: np.ndarray) -> pathlib.Path:
    """Keep the pass so a changed lyric re-runs only the search, not the model."""
    stream = io.BytesIO()
    np.save(stream, np.asarray(emission, dtype=np.float32))
    return write_bytes(_emissions_path(key), stream.getvalue())


class Backend(Protocol):
    """What alignment needs of a CTC model: one window in, `[frames, vocabulary]` out."""

    def logits(self, waveform: np.ndarray) -> np.ndarray: ...


def model_file(directory: str | pathlib.Path, provider: str) -> pathlib.Path:
    """The model to load from `directory`: the half-precision one on a GPU, the shipped one else."""
    directory = pathlib.Path(directory)
    half = directory / FP16_FILE
    return half if provider != "cpu" and half.is_file() else directory / MODEL_FILE


def fetch_fp16(
    directory: str | pathlib.Path,
    provider: str,
    model: str = DEFAULT_MODEL,
    language: str = "ja",
    progress: Callable[[int, int], None] | None = None,
) -> None:
    """Fetch the half-precision model a GPU run prefers, if the store's copy is not there yet.

    Only the store's own directory is filled: a directory the caller pointed at is used as it stands,
    so a conversion of one's own needs no download. A fetch that fails - offline, or while nothing is
    published - leaves the shipped model in place, which runs slower rather than not at all.
    """
    if provider == "cpu" or pathlib.Path(directory) != model_dir(model, language):
        return
    if model_store.installed(FP16_KEY, model, language):
        return
    with suppress(OSError, ValueError, zipfile.BadZipFile):
        model_store.install(FP16_KEY, (model, language), progress=progress)


class OnnxBackend:
    """A converted CTC model: `logits(waveform)` is `[frames, vocabulary]` for one window."""

    def __init__(self, path: str | pathlib.Path, provider: str = "cpu"):
        path = pathlib.Path(path)
        options = None
        if path.name == FP16_FILE and provider == "webgpu":
            options = {"forceCpuNodeNames": FP16_CPU_NODES}
        self.session = model_store.session(path, provider, options)
        self.input = self.session.get_inputs()[0].name

    def logits(self, waveform: np.ndarray) -> np.ndarray:
        values = np.asarray(waveform, dtype=np.float32)[None, :]
        return self.session.run(None, {self.input: values})[0][0]


class ChunkedBackend:
    """A backend over overlapping chunks, so no pass sees more than `chunk_seconds` of audio.

    The frames of a wav2vec2 pass are a grid of `FRAME_SAMPLES`; a chunk starts on that grid, and
    only its frames with `overlap` of audio on either side are kept, so consecutive chunks tile the
    one long pass's grid exactly and each kept frame still had its neighbours in the window. The
    frames at the seam are the ones a long pass would have seen with less context, which is the
    price of bounding memory. With `silence` a seam is pulled back to the lowest point of a rest the
    `Slicer` found, so a rest is not charged to two windows; a stretch with no rest worth cutting
    still falls back to the fixed `step`.
    """

    def __init__(
        self,
        backend: Backend,
        *,
        chunk_seconds: float = CHUNK_SECONDS,
        overlap_seconds: float = CHUNK_OVERLAP_SECONDS,
        silence: bool = False,
        progress: Callable[[int, int], None] | None = None,
    ):
        self.backend = backend
        self.chunk = max(FRAME_SAMPLES, int(chunk_seconds * SAMPLE_RATE) // FRAME_SAMPLES * FRAME_SAMPLES)
        overlap = max(0, int(overlap_seconds * SAMPLE_RATE) // FRAME_SAMPLES * FRAME_SAMPLES)
        self.overlap = min(overlap, self.chunk - FRAME_SAMPLES)
        self.silence = silence
        self.progress = progress

    def _silence_cuts(self, values: np.ndarray) -> list[int]:
        """The frame-aligned samples a `Slicer` cuts at, ascending; the audio's ends are left out."""
        slicer = Slicer(
            sr=SAMPLE_RATE,
            threshold=SLICE_THRESHOLD_DB,
            min_length=SLICE_MIN_LENGTH_MS,
            min_interval=SLICE_MIN_INTERVAL_MS,
            max_sil_kept=SLICE_MAX_SIL_KEPT_MS,
        )
        cuts = {
            min(int(round(chunk["offset"] * SAMPLE_RATE)), values.size) // FRAME_SAMPLES * FRAME_SAMPLES
            for chunk in slicer.slice(values)
            if chunk["offset"] > 0
        }
        return sorted(cuts)

    def _regions(self, values: np.ndarray) -> list[tuple[int, int]]:
        """The sample range each pass keeps, tiling `[0, values.size)`.

        A region is at most `step` long and carries `pad` of context on either side, so every window
        fits `chunk`; the first region has no audio to its left, so it may keep `pad` more.
        """
        step = self.chunk - self.overlap
        pad = self.overlap // 2
        cuts = self._silence_cuts(values) if self.silence else []
        regions = []
        start, limit = 0, step + pad
        while values.size - start > limit:
            target = start + limit
            end = max((cut for cut in cuts if start < cut <= target), default=target)
            regions.append((start, end))
            start, limit = end, step
        regions.append((start, values.size))
        return regions

    def logits(self, waveform: np.ndarray) -> np.ndarray:
        values = np.asarray(waveform, dtype=np.float32)
        if values.size <= self.chunk:
            return self.backend.logits(values)
        pad = self.overlap // 2
        regions = self._regions(values)
        pieces = []
        for index, (start, end) in enumerate(regions):
            first = max(0, start - pad)
            window = values[first : min(values.size, end + pad)]
            if window.size < MIN_SAMPLES:
                window = np.pad(window, (0, MIN_SAMPLES - window.size))
            found = self.backend.logits(window)
            base = first // FRAME_SAMPLES
            low = start // FRAME_SAMPLES
            high = min(end // FRAME_SAMPLES, base + found.shape[0])
            pieces.append(found[low - base : high - base])
            if self.progress is not None:
                self.progress(index + 1, len(regions))
        return np.concatenate(pieces, axis=0)


def log_softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - values.max(axis=-1, keepdims=True)
    return shifted - np.log(np.exp(shifted).sum(axis=-1, keepdims=True))


def whole_emissions(backend: Backend, audio: np.ndarray) -> np.ndarray:
    """The model's log-softmax pass over the whole audio, which the lyrics never enter."""
    values = np.asarray(audio, dtype=np.float32)
    if values.size < MIN_SAMPLES:
        values = np.pad(values, (0, MIN_SAMPLES - values.size))
    return log_softmax(backend.logits(values))


def forced_align(
    emission: np.ndarray,
    tokens: Sequence[int],
    blank_id: int = 0,
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


def ctc_onset_posteriors(
    emission: np.ndarray, tokens: Sequence[int], blank_id: int = 0
) -> np.ndarray | None:
    """Posterior distribution over the first frame of each target in the CTC state lattice.

    Rows correspond to input tokens and columns to emission frames. Unlike the Viterbi score, this
    sums every valid forced path, so a broad row means the model did not localize that token's onset
    reliably. `None` means no complete path can visit all targets.
    """
    evidence = _ctc_evidence(emission, tokens, blank_id)
    return evidence[0] if evidence is not None else None


def _ctc_evidence(
    emission: np.ndarray, tokens: Sequence[int], blank_id: int
) -> tuple[np.ndarray, float] | None:
    """Return onset posteriors and the log probability of the complete transcript."""
    frames, count = emission.shape[0], len(tokens)
    if count == 0:
        return np.empty((0, frames), dtype=np.float64), float(emission[:, blank_id].sum())
    repeats = sum(earlier == later for earlier, later in zip(tokens, tokens[1:], strict=False))
    if frames < count + repeats:
        return None

    states = 2 * count + 1
    labels = np.full(states, blank_id, dtype=np.int64)
    labels[1::2] = tokens
    skip = np.zeros(states, dtype=bool)
    skip[2:] = (labels[2:] != blank_id) & (labels[2:] != labels[:-2])

    alpha = np.full((frames, states), -np.inf, dtype=np.float64)
    alpha[0, 0] = emission[0, blank_id]
    alpha[0, 1] = emission[0, labels[1]]
    for frame in range(1, frames):
        previous = alpha[frame - 1]
        step = np.concatenate(([-np.inf], previous[:-1]))
        over = np.full(states, -np.inf)
        valid = np.flatnonzero(skip)
        over[valid] = previous[valid - 2]
        alpha[frame] = np.logaddexp(np.logaddexp(previous, step), over) + emission[frame, labels]

    beta = np.full((frames, states), -np.inf, dtype=np.float64)
    beta[-1, -1:] = 0.0
    beta[-1, -2] = 0.0
    for frame in range(frames - 2, -1, -1):
        following = beta[frame + 1] + emission[frame + 1, labels]
        step = np.concatenate((following[1:], [-np.inf]))
        over = np.full(states, -np.inf)
        over[:-2] = beta[frame + 1, 2:] + emission[frame + 1, labels[2:]]
        over[:-2][~skip[2:]] = -np.inf
        beta[frame] = np.logaddexp(np.logaddexp(following, step), over)

    total = np.logaddexp(alpha[-1, -1], alpha[-1, -2])
    if not np.isfinite(total):
        return None
    posterior = np.zeros((count, frames), dtype=np.float64)
    for token in range(count):
        state = 2 * token + 1
        if token == 0:
            posterior[token, 0] = np.exp(emission[0, labels[state]] + beta[0, state] - total)
        entered = alpha[:-1, state - 1]
        if skip[state]:
            entered = np.logaddexp(entered, alpha[:-1, state - 2])
        posterior[token, 1:] = np.exp(
            entered + emission[1:, labels[state]] + beta[1:, state] - total
        )
    return posterior, float(total)


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
    through a wildcard column.
    """
    lines = list(segments)
    aligned = []
    for index, segment in enumerate(lines):
        aligned.append(_align_one(segment, backend, dictionary, audio, blank_id))
        if progress is not None:
            progress(index + 1, len(lines))
    return aligned


def align_whole(
    segment: Segment,
    emission: np.ndarray,
    dictionary: Mapping[str, int],
    *,
    blank_id: int = 0,
) -> AlignedSegment:
    """Align a segment that is the whole audio, from the model's already-computed pass over it."""
    return _align_emission(segment, emission, 0.0, dictionary, blank_id)


def onset_offset(model: str, token: str) -> float:
    """The model's estimated shift from a CTC token onset to a musical onset."""
    kind = "vowel" if token[:1].lower() in "aeiou" else "consonant"
    try:
        return ONSET_OFFSETS[model][kind]
    except KeyError as error:
        raise ValueError(f"no onset calibration for model {model!r}") from error


def calibrate_times(
    rows: Sequence[Sequence[tuple[float | None, float | None]]],
    tokens: Sequence[Token],
    model: str,
) -> list[list[tuple[float | None, float | None]]]:
    """Shift token onsets to the model's musical-onset estimate, preserving durations.

    CTC can give two adjacent tokens the same frame. Calibration must therefore retain global
    monotonicity: only the rare inverted onset is clipped to the previous calibrated onset.
    """
    result: list[list[tuple[float | None, float | None]]] = []
    token_at = 0
    previous = float("-inf")
    for row in rows:
        found = []
        for start, end in row:
            token = tokens[token_at]
            token_at += 1
            if start is None or end is None:
                found.append((start, end))
                continue
            adjusted = min(end, max(previous, max(0.0, start + onset_offset(model, token.text))))
            found.append((adjusted, end))
            previous = adjusted
        result.append(found)
    if token_at != len(tokens):
        raise ValueError("token count does not match alignment rows")
    return result


def _align_one(
    segment: Segment,
    backend: Backend,
    dictionary: Mapping[str, int],
    audio: np.ndarray,
    blank_id: int,
) -> AlignedSegment:
    if not any(len(token) for token in segment.tokens):
        return AlignedSegment(segment.start, segment.end, tuple(Token(text) for text in segment.tokens))
    waveform, offset = _window(segment, audio)
    if waveform is None:
        return AlignedSegment(segment.start, segment.end, tuple(Token(text) for text in segment.tokens))
    return _align_emission(segment, log_softmax(backend.logits(waveform)), offset, dictionary, blank_id)


def _align_emission(
    segment: Segment,
    emission: np.ndarray,
    offset: float,
    dictionary: Mapping[str, int],
    blank_id: int,
) -> AlignedSegment:
    lengths = [len(token) for token in segment.tokens]
    codes = [dictionary.get(char.lower(), -1) for token in segment.tokens for char in token]
    tokens: list[Token] = [Token(text) for text in segment.tokens]
    unknown = [code < 0 or code == blank_id for code in codes]
    if any(unknown):
        non_blank = np.ones(emission.shape[1], dtype=bool)
        non_blank[blank_id] = False
        emission = np.concatenate([emission, emission[:, non_blank].max(axis=1)[:, None]], axis=1)
        wildcard = emission.shape[1] - 1
        codes = [wildcard if missing else code for code, missing in zip(codes, unknown, strict=True)]
    found = forced_align(emission, codes, blank_id)
    probability = None
    if found is not None:
        evidence = _ctc_evidence(emission, codes, blank_id)
        onset = evidence[0] if evidence is not None else None
        if evidence is not None and codes and not any(unknown):
            probability = evidence[1]
        spans = merge_tokens(found[0], found[1], blank_id)
        if len(spans) == len(codes):
            at = 0
            for index, length in enumerate(lengths):
                chunk = spans[at : at + length]
                first_code = at
                at += length
                if not chunk:
                    continue
                start = chunk[0][1] * FRAME_SECONDS + offset
                end = chunk[-1][2] * FRAME_SECONDS + offset
                score = float(np.exp(sum(span[3] for span in chunk) / len(chunk)))
                interval = None
                if onset is not None:
                    cumulative = np.cumsum(onset[first_code])
                    low = int(np.searchsorted(cumulative, 0.05))
                    high = int(np.searchsorted(cumulative, 0.95))
                    interval = (round(low * FRAME_SECONDS + offset, 3), round(high * FRAME_SECONDS + offset, 3))
                tokens[index] = Token(
                    segment.tokens[index], round(start, 3), round(end, 3), round(score, 3), interval
                )
    return AlignedSegment(segment.start, segment.end, tuple(tokens), probability)


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


def voice_segments(audio: np.ndarray, frame_seconds: float = TAIL_FRAME_SECONDS) -> list[tuple[float, float]]:
    """Where the voice is: runs of frames whose energy is above a fraction of the loud percentile.

    A port of FA-Kara's `non_silent_recog`, reading the same mono 16 kHz audio the aligner takes.
    Frames overlap by half, and a run is padded a quarter frame on either side.
    """
    frame = max(1, int(SAMPLE_RATE * frame_seconds))
    hop = max(1, frame // 2)
    energy = librosa.feature.rms(y=audio, frame_length=frame, hop_length=hop)[0]
    if energy.size == 0:
        return []
    threshold = float(np.percentile(energy, 100 - SILENCE_PERCENTILE)) * SILENCE_RATIO
    times = librosa.frames_to_time(np.arange(energy.size), sr=SAMPLE_RATE, hop_length=hop)
    segments = []
    start = None
    for moment, active in zip(times, energy > threshold, strict=True):
        if active and start is None:
            start = max(float(moment) - frame_seconds / 4, 0.0)
        elif not active and start is not None:
            segments.append((start, float(moment) + frame_seconds / 4))
            start = None
    if start is not None:
        segments.append((start, float(times[-1])))
    return segments


def correct_times(
    rows: list[list[tuple[float | None, float | None]]], audio: np.ndarray
) -> list[list[tuple[float | None, float | None]]]:
    """Extend each line's last Sound to the voice ending, as FA-Kara does.

    A line's last Sound reaches the end of the voiced stretch it sits in, or the next line's onset
    when the voice runs on. Keep the CTC onset unchanged: the line-level energy heuristic can move a
    head to a later stretch when the line crosses a rest, while CTC onsets are already more precise
    than that envelope. A line with nothing aligned is left alone.
    """
    rows = [list(row) for row in rows]
    _correct_tails(rows, voice_segments(audio))
    return rows


def _correct_tails(rows: list[list], segments: Sequence[tuple[float, float]]) -> None:
    """A line's last sound reaches the voice's end, or the next line's onset when it runs on."""
    for index, row in enumerate(rows):
        last = _last_aligned(row)
        if last is None:
            continue
        start, end = row[last]
        if end is None:
            continue
        filled = _tail_end(segments, end, _next_onset(rows, index))
        if filled > end:
            row[last] = (start, filled)


def _last_aligned(row: list) -> int | None:
    return next((index for index in range(len(row) - 1, -1, -1) if row[index][0] is not None), None)


def _next_onset(rows: list[list], index: int) -> float:
    """The first aligned start after line `index`, the next sung token FA-Kara reaches."""
    for row in rows[index + 1 :]:
        start = next((start for start, _end in row if start is not None), None)
        if start is not None:
            return start
    return math.inf


def _tail_end(segments: Sequence[tuple[float, float]], end: float, next_onset: float) -> float:
    """Where a sound's voice stops: the stretch's end, or the next onset when the voice runs on."""
    for begin, finish in segments:
        if begin <= end <= finish:
            if finish <= next_onset:
                return finish
            return max(next_onset - FRAME_SECONDS, end)
    return end


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
    parser = argparse.ArgumentParser(prog="namioto-cli align", description=__doc__)
    parser.add_argument("audio", help="the separated vocal to align against")
    parser.add_argument("segments", type=pathlib.Path, help="JSON list of {start, end, tokens}, one entry per line")
    parser.add_argument(
        "--dir", type=pathlib.Path, help=f"model directory (default: ${MODEL_ENV} or the data directory)"
    )
    parser.add_argument("--model", choices=MODELS, default=DEFAULT_MODEL, help="which aligner to use")
    parser.add_argument("--language", choices=LANGUAGES, default="ja", help="which language model to use")
    parser.add_argument(
        "--device",
        choices=devices.RUN_CHOICES,
        default="cpu",
        help="run on the CPU, or on the GPU backend the settings name",
    )
    parser.add_argument("--out", type=pathlib.Path, help="write the alignment here instead of to stdout")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        provider = devices.resolve(args.device)
        directory = resolve_model(args.dir, args.model, args.language)
        fetch_fp16(directory, provider, args.model, args.language)
        backend = OnnxBackend(model_file(directory, provider), provider=provider)
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
    payload = {
        "audio": str(args.audio),
        "segments": [
            {
                "start": segment.start,
                "end": segment.end,
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
