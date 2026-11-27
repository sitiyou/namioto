# SPDX-License-Identifier: AGPL-3.0-only
"""Compact local audio evidence for comparing two sets of note boundaries."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import TYPE_CHECKING

import numpy as np
import soundfile as sf
from numpy.lib.stride_tricks import sliding_window_view

if TYPE_CHECKING:
    from namioto.analysis.spectrum import NoteSpectrum

FRAME_SECONDS = 0.032
HOP_SECONDS = 0.010
CONTEXT_SECONDS = 0.060
RELEASE_DROP_DB = 12.0
MAX_FRAMES = 128
HIGH_BAND_HZ = (2500, 8000)
Note = tuple[float, float, float]


def summarize(
    path: str,
    notes: Sequence[tuple[Note, Note]],
    a4: float,
    *,
    spectrum: NoteSpectrum | None = None,
    unmapped_gaps: Sequence[tuple[float, float]] = (),
) -> dict | None:
    """Measure raw/snapped boundaries, pitch changes and gaps on the recording clock."""
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
                if spectrum is not None and previous is not None:
                    change = _pitch_change(spectrum, previous, raw)
                    if change is not None:
                        row["pitch_change_before"] = change
                rows.append(row)
                previous = raw
            gap_levels = [signal.high_band(start, end) for start, end in unmapped_gaps]
    except OSError, sf.LibsndfileError:
        return None
    result = {
        "step_ms": signal.step * 1000,
        "window_ms": signal.width / signal.rate * 1000,
        "level_fields": ["harmonic_dbfs", "rms_dbfs"],
        "notes": rows,
    }
    if spectrum is not None and any("pitch_change_before" in row for row in rows):
        result["note_spectrum"] = {"backend": spectrum.backend, "step_ms": spectrum.frame_ms}
    if unmapped_gaps:
        result["unmapped_gaps"] = [
            {"start": start, "end": end, "high_band": level}
            for (start, end), level in zip(unmapped_gaps, gap_levels, strict=True)
        ]
    return result


def _pitch_change(spectrum: NoteSpectrum, previous: Note, current: Note) -> dict | None:
    """Compare the two expected note bands on the two sides of a close handoff."""
    from namioto.analysis.spectrum import MIDI_OFFSET, NOTE_COUNT

    pitches = round(previous[2]), round(current[2])
    bands = tuple(pitch - MIDI_OFFSET for pitch in pitches)
    if (
        bands[0] == bands[1]
        or not all(0 <= band < NOTE_COUNT for band in bands)
        or abs(current[0] - previous[1]) > CONTEXT_SECONDS
    ):
        return None
    step = spectrum.hop / spectrum.sample_rate
    origin = (spectrum.hop // 2) / spectrum.sample_rate

    def contrast(start: float, end: float) -> float | None:
        first = max(0, math.ceil((start - origin) / step))
        last = math.ceil((end - origin) / step)
        if last <= first or first >= spectrum.frames:
            return None
        left, right = (float(value) for value in np.median(spectrum.table[first:last, bands], axis=0))
        floor = 1e-6
        if max(left, right) <= floor:
            return None
        return round(20 * math.log10(max(right, floor) / max(left, floor)), 1)

    before = contrast(max(previous[0], previous[1] - CONTEXT_SECONDS), previous[1])
    after = contrast(current[0], min(current[1], current[0] + CONTEXT_SECONDS))
    if before is None or after is None:
        return None
    return {"pitches": pitches, "before_db": before, "after_db": after}


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

    def _frames(self, start: float, end: float) -> tuple[np.ndarray, np.ndarray | None]:
        half = self.width // 2
        first = max(math.ceil(start / self.step), math.ceil(half / self.hop))
        last = min(math.ceil(end / self.step), (len(self.audio) - self.width + half) // self.hop + 1)
        if last <= first:
            return np.empty(0), None
        stride = math.ceil((last - first) / MAX_FRAMES)
        centers = np.arange(first, last, stride) * self.hop
        if stride == 1:
            samples = self._read(int(centers[0]) - half, int(centers[-1] - centers[0]) + self.width)
            frames = sliding_window_view(samples, self.width, axis=0)[:: self.hop]
        else:
            frames = np.stack([self._read(int(center) - half, self.width).T for center in centers])
        return centers / self.rate, frames

    def levels(self, start: float, end: float, pitch: float) -> tuple[np.ndarray, np.ndarray]:
        times, frames = self._frames(start, end)
        if frames is None:
            return times, np.empty((0, 2))
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
        harmonic_power = 2 * np.sum(peaks, axis=0) / self.window.sum() ** 2 if peaks else np.full(len(times), np.nan)
        half = self.width // 2
        rms_half = max(1, min(half, self.hop))
        rms_power = np.mean(frames[..., half - rms_half : half + rms_half] ** 2, axis=(1, 2))
        levels = 10 * np.log10(np.maximum(1e-12, np.column_stack((harmonic_power, rms_power))))
        return times, levels

    def high_band(self, start: float, end: float) -> dict | None:
        # Keep the analysis windows inside the gap so a neighbor's attack cannot
        # be mistaken for a sound in the gap merely because the windows overlap.
        half = self.width // 2
        times, frames = self._frames(start + half / self.rate, end - (self.width - half) / self.rate)
        low, high = HIGH_BAND_HZ
        frequencies = np.fft.rfftfreq(self.nfft, 1 / self.rate)
        bins = (frequencies >= low) & (frequencies <= high) & (frequencies < self.rate / 2)
        if frames is None or not bins.any():
            return None
        power = np.abs(np.fft.rfft(frames * self.window, n=self.nfft, axis=-1)[..., bins]) ** 2
        band_power = 2 * power.sum(axis=-1).mean(axis=1) / (self.nfft * np.sum(self.window**2))
        levels = 10 * np.log10(np.maximum(1e-12, band_power))
        peak = int(np.argmax(levels))
        return {
            "range_hz": [low, min(high, self.rate / 2)],
            "peak_at": round(float(times[peak]), 3) if band_power[peak] > 1e-12 else None,
            "peak_dbfs": round(float(levels[peak]), 1),
            "median_dbfs": round(float(np.median(levels)), 1),
        }

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
