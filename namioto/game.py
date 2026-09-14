# SPDX-License-Identifier: MIT
"""Singing-voice note extraction with GAME's ONNX models.

GAME (Generative Adaptive MIDI Extractor, Team OpenVPI) is a PyTorch project and does not ship ONNX
inference itself: the models are exported through `deploy.py`, and this module is a port of the
standalone `infer_onnx.py` that goes with those exports. It keeps that pipeline as it stands - slice
the waveform on silence, run the encoder, decode note boundaries with the segmenter's D3PM loop,
estimate pitches with the estimator, merge the chunks - with the file and CLI plumbing replaced by
this program's own.

The code follows GAME's MIT license; the models are a separate work under CC BY-NC-SA 4.0 and are not
shipped with the program, so a directory holding the ONNX export package (`config.json` plus
encoder/segmenter/estimator/dur2bd/bd2dur) is passed in, or pointed at with `$NAMIOTO_GAME_MODEL`.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable, Sequence

import numpy as np
import platformdirs

try:
    import librosa
except ImportError:  # the extractor itself only needs numpy; the file loader and pitch names do not
    librosa = None

import onnxruntime as ort

GAME_VERSION = "1.0.3"
GAME_RELEASE = f"v{GAME_VERSION}"
MODEL_SIZES = ("small", "medium", "large")
MODEL_FILES = ("encoder", "segmenter", "estimator", "dur2bd", "bd2dur")
SAMPLE_FORMATS = (".wav", ".flac", ".mp3", ".aac", ".ogg")
Note = tuple[float, float, float]  # onset and offset in seconds, then the pitch


def asset_url(size: str) -> str:
    """Where the ONNX export package of one size is published."""
    if size not in MODEL_SIZES:
        raise ValueError(f"unknown model size {size!r}, expected one of {', '.join(MODEL_SIZES)}")
    name = f"GAME-{GAME_VERSION}-{size}-onnx.zip"
    return f"https://github.com/openvpi/GAME/releases/download/{GAME_RELEASE}/{name}"


def models_root() -> pathlib.Path:
    """Where the downloaded models live: the platform's data directory, not the package."""
    return pathlib.Path(platformdirs.user_data_dir("namioto")) / "models" / "game"


def model_dir(size: str) -> pathlib.Path:
    asset_url(size)  # a size nobody publishes is a mistake worth raising before touching the disk
    return models_root() / size


def is_installed(size: str) -> bool:
    return (model_dir(size) / "config.json").is_file()


def get_rms(y, *, frame_length=2048, hop_length=512, pad_mode="constant"):
    padding = (int(frame_length // 2), int(frame_length // 2))
    y = np.pad(y, padding, mode=pad_mode)
    axis = -1
    out_strides = y.strides + (y.strides[axis],)
    x_shape_trimmed = list(y.shape)
    x_shape_trimmed[axis] -= frame_length - 1
    out_shape = tuple(x_shape_trimmed) + (frame_length,)
    xw = np.lib.stride_tricks.as_strided(y, shape=out_shape, strides=out_strides)
    xw = np.moveaxis(xw, -1, axis + 1)
    slices = [slice(None)] * xw.ndim
    slices[axis] = slice(0, None, hop_length)
    power = np.mean(np.abs(xw[tuple(slices)]) ** 2, axis=-2, keepdims=True)
    return np.sqrt(power)


class Slicer:
    """Cuts the waveform at the silences worth cutting, so no chunk is longer than it must be."""

    def __init__(self, sr, threshold=-40.0, min_length=5000, min_interval=300, hop_size=20, max_sil_kept=5000):
        if not min_length >= min_interval >= hop_size:
            raise ValueError("min_length >= min_interval >= hop_size required")
        if not max_sil_kept >= hop_size:
            raise ValueError("max_sil_kept >= hop_size required")
        self.sr = sr
        self.threshold = 10 ** (threshold / 20.0)
        self.hop_size = round(sr * hop_size / 1000)
        self.win_size = min(round(min_interval), 4 * self.hop_size)
        self.min_length = round(sr * min_length / 1000 / self.hop_size)
        self.min_interval = round(min_interval / self.hop_size)
        self.max_sil_kept = round(sr * max_sil_kept / 1000 / self.hop_size)

    def _apply_slice(self, waveform, begin, end):
        chunk = {"offset": begin * self.hop_size / self.sr}
        if len(waveform.shape) > 1:
            waveform = waveform.mean(axis=0)
        chunk["waveform"] = waveform[begin * self.hop_size : min(waveform.shape[0], end * self.hop_size)]
        return chunk

    def slice(self, waveform):
        samples = waveform.mean(axis=0) if len(waveform.shape) > 1 else waveform
        if (samples.shape[0] + self.hop_size - 1) // self.hop_size <= self.min_length:
            return [{"offset": 0, "waveform": samples}]
        rms_list = get_rms(y=samples, frame_length=self.win_size, hop_length=self.hop_size).squeeze(0)
        sil_tags = []
        silence_start = None
        clip_start = 0
        for i, rms in enumerate(rms_list):
            if rms < self.threshold:
                if silence_start is None:
                    silence_start = i
                continue
            if silence_start is None:
                continue
            is_leading_silence = silence_start == 0 and i > self.max_sil_kept
            need_slice_middle = i - silence_start >= self.min_interval and i - clip_start >= self.min_length
            if not is_leading_silence and not need_slice_middle:
                silence_start = None
                continue
            if i - silence_start <= self.max_sil_kept:
                pos = rms_list[silence_start : i + 1].argmin() + silence_start
                sil_tags.append((0, pos) if silence_start == 0 else (pos, pos))
                clip_start = pos
            elif i - silence_start <= self.max_sil_kept * 2:
                window = slice(i - self.max_sil_kept, silence_start + self.max_sil_kept + 1)
                pos = rms_list[window].argmin() + i - self.max_sil_kept
                pos_l = rms_list[silence_start : silence_start + self.max_sil_kept + 1].argmin() + silence_start
                pos_r = rms_list[i - self.max_sil_kept : i + 1].argmin() + i - self.max_sil_kept
                if silence_start == 0:
                    sil_tags.append((0, pos_r))
                    clip_start = pos_r
                else:
                    sil_tags.append((min(pos_l, pos), max(pos_r, pos)))
                    clip_start = max(pos_r, pos)
            else:
                pos_l = rms_list[silence_start : silence_start + self.max_sil_kept + 1].argmin() + silence_start
                pos_r = rms_list[i - self.max_sil_kept : i + 1].argmin() + i - self.max_sil_kept
                sil_tags.append((0, pos_r) if silence_start == 0 else (pos_l, pos_r))
                clip_start = pos_r
            silence_start = None
        total_frames = rms_list.shape[0]
        if silence_start is not None and total_frames - silence_start >= self.min_interval:
            silence_end = min(total_frames, silence_start + self.max_sil_kept)
            pos = rms_list[silence_start : silence_end + 1].argmin() + silence_start
            sil_tags.append((pos, total_frames + 1))
        if len(sil_tags) == 0:
            return [{"offset": 0, "waveform": samples}]
        chunks = []
        if sil_tags[0][0] > 0:
            chunks.append(self._apply_slice(samples, 0, sil_tags[0][0]))
        for i in range(len(sil_tags) - 1):
            chunks.append(self._apply_slice(samples, sil_tags[i][1], sil_tags[i + 1][0]))
        if sil_tags[-1][1] < total_frames:
            chunks.append(self._apply_slice(samples, sil_tags[-1][1], total_frames))
        return chunks


class OnnxBackend:
    """The five ONNX modules, loaded once and driven through the pipeline they describe."""

    def __init__(self, model_dir: pathlib.Path | str):
        self.model_dir = pathlib.Path(model_dir)
        config_path = self.model_dir / "config.json"
        if not config_path.is_file():
            raise FileNotFoundError(
                f"{config_path} not found: point at a directory holding GAME's ONNX export package "
                "(config.json plus the five .onnx files)"
            )
        self.config = json.loads(config_path.read_text(encoding="utf8"))
        self.sr = int(self.config["samplerate"])
        self.timestep = float(self.config["timestep"])
        self.embedding_dim = int(self.config["embedding_dim"])
        self.languages = self.config.get("languages") or {}
        self.loop = bool(self.config.get("loop", False))
        self.session_options = ort.SessionOptions()
        self.session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.sessions = {name: self._make_session(name) for name in MODEL_FILES}

    def _make_session(self, name: str):
        return ort.InferenceSession(
            str(self.model_dir / f"{name}.onnx"),
            self.session_options,
            providers=["CPUExecutionProvider"],
        )

    def _run(self, name, feeds):
        session = self.sessions[name]
        return session.run(None, {i.name: feeds[i.name] for i in session.get_inputs()})

    def encode(self, waveforms: np.ndarray, durations: np.ndarray):
        x_seg, x_est, mask_t = self._run("encoder", {"waveform": waveforms, "duration": durations})
        return x_seg, x_est, mask_t

    def known_boundaries(self, durations, mask_t):
        (boundaries,) = self._run("dur2bd", {"durations": durations, "maskT": mask_t})
        return boundaries & mask_t

    def segment(self, x_seg, language, known_boundaries, prev_boundaries, t, mask_t, threshold, radius):
        feeds = {
            "x_seg": x_seg,
            "known_boundaries": known_boundaries,
            "maskT": mask_t,
            "threshold": threshold,
            "radius": radius,
        }
        if "language" in {i.name for i in self.sessions["segmenter"].get_inputs()}:
            feeds["language"] = language
        if self.loop:
            feeds["prev_boundaries"] = prev_boundaries
            feeds["t"] = t
        (boundaries,) = self._run("segmenter", feeds)
        return boundaries

    def durations_n_mask(self, boundaries, mask_t):
        return self._run("bd2dur", {"boundaries": boundaries, "maskT": mask_t})

    def estimate(self, x_est, boundaries, mask_t, mask_n, threshold):
        return self._run(
            "estimator",
            {"x_est": x_est, "boundaries": boundaries, "maskT": mask_t, "maskN": mask_n, "threshold": threshold},
        )


def collect_notes(durations, scores, presence, offset, length) -> list[Note]:
    """The notes one slice holds, from its part of the batch output."""
    notes = []
    onsets = np.concatenate(([0.0], np.cumsum(durations))).clip(max=length) + offset
    offsets = np.cumsum(durations).clip(max=length) + offset
    for onset, oset, score, valid in zip(onsets, offsets, scores, presence):  # noqa: B905
        # the estimator answers for the notes it kept, so its two arrays are the short ones
        if oset - onset <= 0 or not valid:
            continue
        notes.append((onset, oset, float(score)))
    return notes


def merge_notes(notes: Sequence[Note]) -> list[Note]:
    """Sort and remove overlaps, exactly like GAME's SaveCombinedFileCallback."""
    notes = sorted(notes, key=lambda note: (note[0], note[1], note[2]))
    last_time = 0.0
    merged = []
    for onset, oset, pitch in notes:
        onset = max(onset, last_time)
        oset = max(oset, onset)
        if oset <= onset:
            continue
        merged.append((onset, oset, pitch))
        last_time = oset
    return merged


def quantize_notes(notes: Sequence[Note], unit: float, phase: float = 0.0) -> list[Note]:
    """Snap to the grid cells picked around `phase`, output strictly on the absolute grid.

    The phase only influences rounding decisions; it is never baked into the output.
    """
    out = []
    last_time = 0.0
    for onset, oset, pitch in notes:
        onset = unit * round((onset - phase) / unit)
        oset = unit * round((oset - phase) / unit)
        if oset <= onset:
            oset = onset + unit
        onset = max(onset, last_time)
        oset = max(oset, onset)
        out.append((onset, oset, pitch))
        last_time = oset
    return out


def estimate_grid_phase(onsets, unit: float) -> float:
    """Best global phase offset for the grid: the one with the least quantization distortion."""
    o = np.asarray(onsets, dtype=np.float64)
    if o.size == 0:
        return 0.0

    def score(phase):
        off = (o - phase) / unit
        err = off - np.round(off)
        return float(np.sum(err * err))

    coarse = np.linspace(0.0, unit, 65, endpoint=False)
    s = [score(p) for p in coarse]
    k = int(np.argmin(s))
    lo, hi = coarse[(k - 1) % len(coarse)], coarse[(k + 1) % len(coarse)]
    if lo > hi:
        lo, hi = hi, lo
    for _ in range(20):  # golden section on [lo, hi]
        a = lo + (hi - lo) * 0.382
        b = lo + (hi - lo) * 0.618
        if score(a) < score(b):
            hi = b
        else:
            lo = a
    return (lo + hi) / 2


def estimate_grid_period(onsets, phase: float, unit: float, max_drift: float = 0.05) -> float:
    """Refine the grid period around `unit` by least squares on the snapped groups."""
    o = np.asarray(onsets, dtype=np.float64)
    if o.size < 4:
        return unit
    k = np.round((o - phase) / unit)
    for _ in range(6):
        k = np.round((o - phase) / unit)
        valid = (k > 0) & (np.abs(o - (phase + k * unit)) < unit * 0.25)
        if valid.sum() < 3:
            break
        period = float(np.sum(k[valid] * (o[valid] - phase)) / np.sum(k[valid] ** 2))
        if unit * (1 - max_drift) <= period <= unit * (1 + max_drift):
            unit = period
        else:
            break
    return unit


def save_midi(path: pathlib.Path, notes: Sequence[Note], tempo: float, grid: tuple[float, int] | None = None) -> None:
    """Write the notes as a type 1 MIDI file; `grid` is (seconds per cell, ticks per cell)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    us_per_qn = round(60_000_000 / tempo)
    track_data = bytearray()
    track_data += _vlq(0) + bytes([0xFF, 0x51, 0x03]) + us_per_qn.to_bytes(3, "big")
    last_time = 0
    for onset, oset, pitch in notes:
        if grid is not None:
            seconds, ticks = grid
            onset_ticks = round(onset / seconds) * ticks
            offset_ticks = round(oset / seconds) * ticks
        else:
            onset_ticks = round(onset * tempo * 8)
            offset_ticks = round(oset * tempo * 8)
        midi_pitch = round(pitch)
        if offset_ticks <= onset_ticks:
            continue
        track_data += _vlq(onset_ticks - last_time) + bytes([0x90, midi_pitch, 100])
        track_data += _vlq(offset_ticks - onset_ticks) + bytes([0x80, midi_pitch, 64])
        last_time = offset_ticks
    track_data += _vlq(0) + bytes([0xFF, 0x2F, 0x00])
    header = bytes([0x4D, 0x54, 0x68, 0x64])  # MThd
    header += (6).to_bytes(4, "big") + (1).to_bytes(2, "big")
    header += (1).to_bytes(2, "big") + (480).to_bytes(2, "big")
    header += bytes([0x4D, 0x54, 0x72, 0x6B]) + len(track_data).to_bytes(4, "big")
    path.write_bytes(header + bytes(track_data))


def _vlq(value: int) -> bytes:
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.insert(0, 0x80 | (value & 0x7F))
        value >>= 7
    return bytes(out)


def save_text(path: pathlib.Path, notes: Sequence[Note], file_format: str, round_pitch: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pitches = [str(round(p)) if round_pitch else f"{p:.3f}" for _, _, p in notes]
    if file_format == "txt":
        with path.open("w", encoding="utf8") as handle:
            for (onset, oset, _), pitch in zip(notes, pitches, strict=True):
                handle.write(f"{onset:.3f}\t{oset:.3f}\t{pitch}\n")
        return
    import csv

    with path.open("w", encoding="utf8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["onset", "offset", "pitch"])
        for (onset, oset, _), pitch in zip(notes, pitches, strict=True):
            writer.writerow([f"{onset:.3f}", f"{oset:.3f}", pitch])


def install_zip(archive: pathlib.Path, size: str) -> pathlib.Path:
    """Unpack a downloaded release zip into this size's directory, top-level folder and all."""
    target = model_dir(size)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as staging:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(staging)
        source = next((path.parent for path in pathlib.Path(staging).rglob("config.json")), None)
        if source is None:
            raise ValueError(f"{archive.name} holds no config.json: not a GAME export package")
        target.mkdir(parents=True, exist_ok=True)
        for path in source.iterdir():
            shutil.move(str(path), str(target / path.name))
    return target


def download_model(
    size: str,
    progress: Callable[[int, int], None] | None = None,
    *,
    opener: Callable[..., object] | None = None,
) -> pathlib.Path:
    """Fetch one size of the model into the data directory and unpack it.

    The zip is downloaded to a temporary file first, so a broken connection leaves no half-installed
    model behind, and the package is only moved into place once it is whole.
    """
    url = asset_url(size)
    target = model_dir(size)
    target.parent.mkdir(parents=True, exist_ok=True)
    open_url = opener or urllib.request.urlopen
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".zip", delete=False) as handle:
        archive = pathlib.Path(handle.name)
    try:
        with open_url(url) as response, archive.open("wb") as out:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            while True:
                block = response.read(1 << 20)
                if not block:
                    break
                out.write(block)
                done += len(block)
                if progress is not None:
                    progress(done, total)
        return install_zip(archive, size)
    finally:
        archive.unlink(missing_ok=True)


def resolve_model(
    path: str | pathlib.Path | None = None,
    size: str = "small",
    *,
    download: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> pathlib.Path:
    """The directory to load the models from.

    An explicit path or `$NAMIOTO_GAME_MODEL` wins, then an already installed size, and only then a
    download of that size into the data directory.
    """
    if path is not None:
        return pathlib.Path(path)
    from_env = os.environ.get("NAMIOTO_GAME_MODEL")
    if from_env:
        return pathlib.Path(from_env)
    if is_installed(size):
        return model_dir(size)
    if not download:
        raise FileNotFoundError(f"no {size} model in {models_root()}")
    return download_model(size, progress)


def extract(
    backend: OnnxBackend,
    path: str | pathlib.Path,
    *,
    language: str | None = None,
    batch_size: int = 4,
    seg_threshold: float = 0.2,
    seg_radius: float = 0.02,
    est_threshold: float = 0.2,
    d3pm_t0: float = 0.0,
    d3pm_steps: int = 8,
    d3pm_ts: Sequence[float] | None = None,
    silence_slice: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> list[Note]:
    """Extract the notes of one audio file as (onset, offset, pitch) in seconds.

    The audio is loaded at the sample rate the model wants and cut into chunks, which are then run
    through the pipeline in batches; the notes of all chunks come back merged.
    """
    if librosa is None:
        raise RuntimeError("librosa is required to load the audio and to convert pitch names")
    if language and language not in backend.languages:
        raise ValueError(f"language {language!r} is not in config.json: {', '.join(backend.languages)}")
    language_id = backend.languages.get(language, 0)
    if d3pm_ts:
        steps = [float(value) for value in d3pm_ts]
    else:
        step = (1 - d3pm_t0) / d3pm_steps
        steps = [d3pm_t0 + index * step for index in range(d3pm_steps)]
    radius = np.array(round(seg_radius / backend.timestep), dtype=np.int64)
    threshold = np.array(seg_threshold, dtype=np.float32)
    presence_threshold = np.array(est_threshold, dtype=np.float32)
    slicer = Slicer(sr=backend.sr, threshold=-40.0, min_length=1000, min_interval=200, max_sil_kept=100)

    waveform, _ = librosa.load(path, sr=backend.sr, mono=True)
    if waveform.size == 0:
        return []
    chunks = [{"offset": 0, "waveform": waveform}] if not silence_slice else slicer.slice(waveform)
    notes: list[Note] = []
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        longest = max(chunk["waveform"].shape[0] for chunk in batch)
        waveforms = np.zeros((len(batch), longest), dtype=np.float32)
        durations = np.zeros((len(batch),), dtype=np.float32)
        for index, chunk in enumerate(batch):
            waveforms[index, : chunk["waveform"].shape[0]] = chunk["waveform"]
            durations[index] = chunk["waveform"].shape[0] / backend.sr
        x_seg, x_est, mask_t = backend.encode(waveforms, durations)
        known = backend.known_boundaries(durations[:, None], mask_t)
        language_batch = np.full((len(batch),), language_id, dtype=np.int64)
        if backend.loop:
            boundaries = known
            for t in steps:
                boundaries = backend.segment(
                    x_seg,
                    language_batch,
                    known,
                    boundaries,
                    np.full((len(batch),), t, dtype=np.float32),
                    mask_t,
                    threshold,
                    radius,
                )
        else:
            boundaries = backend.segment(x_seg, language_batch, known, None, None, mask_t, threshold, radius)
        chunk_durations, mask_n = backend.durations_n_mask(boundaries, mask_t)
        presence, scores = backend.estimate(x_est, boundaries, mask_t, mask_n, presence_threshold)
        for index, chunk in enumerate(batch):
            notes.extend(
                collect_notes(
                    chunk_durations[index],
                    scores[index],
                    presence[index],
                    chunk["offset"],
                    chunk["waveform"].shape[0] / backend.sr,
                )
            )
        if progress is not None:
            progress(min(start + len(batch), len(chunks)), len(chunks))
    return merge_notes(notes)


def quantized(notes: Sequence[Note], tempo: float, subdivisions: int, phase: float | None = None, fit_tempo=False):
    """Snap the notes to the beat grid `subdivisions` per quarter note apart, and report what was used.

    Returns the notes, the seconds per cell and the phase the snapping was decided around; the phase
    is only used to choose the cell, it is never written into the notes.
    """
    unit = (60.0 / tempo) / subdivisions
    onsets = [note[0] for note in notes]
    if phase is None:
        phase = estimate_grid_phase(onsets, unit)
    if fit_tempo:
        unit = estimate_grid_period(onsets, phase, unit)
    return quantize_notes(notes, unit, phase), unit, phase


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="namioto-game",
        description="Extract the notes of a singing voice with GAME's ONNX models.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("audio", nargs="?", help="audio file to extract from; leave out to only fetch a model")
    parser.add_argument("-m", "--model", type=pathlib.Path, help="directory holding the ONNX export package")
    parser.add_argument(
        "--size",
        choices=MODEL_SIZES,
        default="small",
        help="model size to use, downloaded into the data directory when it is not there yet",
    )
    parser.add_argument("-l", "--language", help="language code from config.json, if the model has any")
    parser.add_argument("--batch-size", type=int, default=4, help="chunks per inference batch")
    parser.add_argument("--seg-threshold", type=float, default=0.2, help="boundary decoding threshold")
    parser.add_argument("--seg-radius", type=float, default=0.02, help="boundary decoding radius, in seconds")
    parser.add_argument("--est-threshold", type=float, default=0.2, help="note presence threshold")
    parser.add_argument("--d3pm-t0", type=float, default=0.0, help="starting T of D3PM sampling")
    parser.add_argument("--d3pm-steps", type=int, default=8, help="number of D3PM sampling steps")
    parser.add_argument("--no-silence-slice", action="store_true", help="do not cut the audio at its silences")
    parser.add_argument("--tempo", type=float, default=120.0, help="tempo used for the grid and the MIDI file")
    parser.add_argument(
        "--quantize",
        type=int,
        choices=[1, 2, 4, 8, 16, 32],
        help="snap to a beat grid this many subdivisions per quarter note",
    )
    parser.add_argument("--quantize-fit-tempo", action="store_true", help="refine the grid period from the onsets")
    parser.add_argument("--midi", type=pathlib.Path, help="also write the notes to this MIDI file")
    parser.add_argument("--json", action="store_true", help="print the notes as JSON")
    return parser.parse_args(argv)


def report_download(done: int, total: int) -> None:
    """One line per megabyte on stderr, so a fetch does not look like a hang."""
    megabytes = done // (1 << 20)
    if megabytes != getattr(report_download, "last", -1):
        report_download.last = megabytes
        where = f" of {total // (1 << 20)}" if total else ""
        print(f"  {megabytes}{where} MB", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        model_dir = resolve_model(args.model, args.size, progress=report_download)
    except (OSError, ValueError) as error:
        print(f"No model to use: {error}", file=sys.stderr)
        return 2
    if args.audio is None:
        print(f"{args.size} model in {model_dir}")
        return 0
    backend = OnnxBackend(model_dir)
    notes = extract(
        backend,
        args.audio,
        language=args.language,
        batch_size=args.batch_size,
        seg_threshold=args.seg_threshold,
        seg_radius=args.seg_radius,
        est_threshold=args.est_threshold,
        d3pm_t0=args.d3pm_t0,
        d3pm_steps=args.d3pm_steps,
        silence_slice=not args.no_silence_slice,
        progress=lambda done, total: print(f"  parts {done}/{total}", file=sys.stderr),
    )
    grid = None
    if args.quantize:
        notes, unit, phase = quantized(notes, args.tempo, args.quantize, fit_tempo=args.quantize_fit_tempo)
        grid = (unit, 480 // args.quantize)
        print(f"grid {unit * 1000:.1f} ms, phase {phase * 1000:.1f} ms", file=sys.stderr)
    if args.midi:
        save_midi(args.midi, notes, args.tempo, grid=grid)
    if args.json:
        dump = {"notes": [{"onset": onset, "offset": oset, "pitch": pitch} for onset, oset, pitch in notes]}
        json.dump(dump, sys.stdout, indent=2)
        print()
    else:
        print(f"{len(notes)} notes")
        for onset, oset, pitch in notes[:20]:
            print(f"  {onset:8.3f} -> {oset:8.3f} s   {pitch:7.3f}")
        if len(notes) > 20:
            print(f"  … and {len(notes) - 20} more")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
