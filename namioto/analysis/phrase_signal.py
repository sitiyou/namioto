# SPDX-License-Identifier: AGPL-3.0-only
"""Compact local audio evidence for comparing two sets of note boundaries."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
import soundfile as sf
from numpy.lib.stride_tricks import sliding_window_view

FRAME_SECONDS = 0.032
HOP_SECONDS = 0.010
CONTEXT_SECONDS = 0.060
RELEASE_DROP_DB = 12.0
MAX_FRAMES = 128
Note = tuple[float, float, float]


def summarize(path: str, notes: Sequence[tuple[Note, Note]], a4: float) -> dict | None:
    """Measure raw/snapped boundary neighborhoods and the gaps between raw notes."""
    try:
        with sf.SoundFile(path) as audio:
            signal = _Signal(audio, a4)
            rows = []
            previous = None
            for raw, snapped in notes:
                pitch = raw[2]
                row = {
                    "onset": signal.boundary(raw[0], snapped[0], pitch),
                    "offset": signal.boundary(raw[1], snapped[1], pitch, release=True),
                }
                if previous is not None and raw[0] - previous[1] >= signal.step:
                    start, end = previous[1], raw[0]
                    _, levels = signal.levels(start, end, previous[2])
                    row["gap_before"] = {"start": start, "end": end, "levels": _median(levels)}
                rows.append(row)
                previous = raw
    except OSError, sf.LibsndfileError:
        return None
    return {
        "step_ms": signal.step * 1000,
        "window_ms": signal.width / signal.rate * 1000,
        "level_fields": ["harmonic_dbfs", "rms_dbfs"],
        "notes": rows,
    }


def _median(levels: np.ndarray) -> list[float | None] | None:
    if not len(levels):
        return None
    return [round(float(value), 1) if np.isfinite(value) else None for value in np.median(levels, axis=0)]


class _Signal:
    def __init__(self, audio: sf.SoundFile, a4: float):
        self.audio = audio
        self.a4 = a4
        self.rate = audio.samplerate
        self.width = max(4, round(self.rate * FRAME_SECONDS))
        self.hop = max(1, round(self.rate * HOP_SECONDS))
        self.step = self.hop / self.rate
        self.window = np.hanning(self.width)
        self.nfft = 1 << (self.width * 2 - 1).bit_length()

    def _read(self, start: int, count: int) -> np.ndarray:
        self.audio.seek(start)
        return self.audio.read(count, dtype="float32", always_2d=True)

    def levels(self, start: float, end: float, pitch: float) -> tuple[np.ndarray, np.ndarray]:
        half = self.width // 2
        first = max(math.ceil(start / self.step), math.ceil(half / self.hop))
        last = min(math.ceil(end / self.step), (len(self.audio) - self.width + half) // self.hop + 1)
        if last <= first:
            return np.empty(0), np.empty((0, 2))
        stride = math.ceil((last - first) / MAX_FRAMES)
        centers = np.arange(first, last, stride) * self.hop
        if stride == 1:
            samples = self._read(int(centers[0]) - half, int(centers[-1] - centers[0]) + self.width)
            frames = sliding_window_view(samples, self.width, axis=0)[:: self.hop]
        else:
            frames = np.stack([self._read(int(center) - half, self.width).T for center in centers])
        power = np.mean(np.abs(np.fft.rfft(frames * self.window, n=self.nfft, axis=-1)) ** 2, axis=1)
        fundamental_bin = self.a4 * 2 ** ((pitch - 69) / 12) * self.nfft / self.rate
        tolerance = 2 ** (0.75 / 12)
        peaks = []
        for harmonic in range(1, 6):
            center = fundamental_bin * harmonic
            if center >= self.nfft / 2:
                break
            low = max(1, round(center / tolerance))
            high = min(power.shape[1], max(low, round(center * tolerance)) + 1)
            peaks.append(power[:, low:high].max(axis=1))
        harmonic_power = 2 * np.sum(peaks, axis=0) / self.window.sum() ** 2 if peaks else np.full(len(centers), np.nan)
        rms_half = max(1, min(half, self.hop))
        rms_power = np.mean(frames[..., half - rms_half : half + rms_half] ** 2, axis=(1, 2))
        levels = 10 * np.log10(np.maximum(1e-12, np.column_stack((harmonic_power, rms_power))))
        return centers / self.rate, levels

    def boundary(self, raw: float, snapped: float, pitch: float, *, release: bool = False) -> dict:
        low, high = sorted((raw, snapped))
        times, levels = self.levels(low - CONTEXT_SECONDS, high + CONTEXT_SECONDS, pitch)
        before = _median(levels[times < low])
        row = {
            "before": before,
            "between": _median(levels[(times >= low) & (times < high)]),
            "after": _median(levels[times >= high]),
        }
        if release:
            drop = None
            if before and before[0] is not None and before[0] > -108:
                below = (times >= low) & (levels[:, 0] <= before[0] - RELEASE_DROP_DB)
                adjacent = np.diff(times) <= self.step * 1.01
                sustained = np.flatnonzero(below[:-1] & below[1:] & adjacent)
                if sustained.size:
                    drop = round(float(times[sustained[0]]), 3)
            row["harmonic_drop"] = drop
        return row
