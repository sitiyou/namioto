# SPDX-License-Identifier: AGPL-3.0-only
"""File-relative audio windows and their position on the recording clock."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class AudioWindow:
    """Select audio in file seconds; origin is the recording time at file time zero."""

    start: float = 0.0
    end: float | None = None
    origin: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.start) or self.start < 0:
            raise ValueError("start must be a finite, nonnegative time")
        if self.end is not None and (not math.isfinite(self.end) or self.end <= self.start):
            raise ValueError("end must be finite and greater than start")
        if not math.isfinite(self.origin) or not math.isfinite(self.onset):
            raise ValueError("audio origin must give a finite recording time")

    @property
    def onset(self) -> float:
        """Recording time at the start of the selected audio."""
        return self.origin + self.start

    def load(self, path: str | Path, sample_rate: int) -> np.ndarray:
        import librosa

        duration = None if self.end is None else self.end - self.start
        audio, _ = librosa.load(path, sr=sample_rate, mono=True, offset=self.start, duration=duration)
        if not audio.size:
            raise ValueError("the selected audio window is empty")
        return audio
