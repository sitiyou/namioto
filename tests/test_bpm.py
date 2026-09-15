# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for the tempo-algorithm switch and the result it hands the editor."""

from __future__ import annotations

import numpy as np
import pytest
import soundfile

from namioto import bpm
from namioto.beats import SAMPLE_RATE


def click_track(bpm_value: float, seconds: float = 30.0) -> np.ndarray:
    audio = np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)
    burst = np.exp(-np.arange(int(SAMPLE_RATE * 0.05)) / (SAMPLE_RATE * 0.005))
    burst = (burst * np.sin(2 * np.pi * 180 * np.arange(len(burst)) / SAMPLE_RATE)).astype(np.float32)
    for start in np.arange(0.0, seconds - 0.1, 60.0 / bpm_value):
        at = int(start * SAMPLE_RATE)
        audio[at : at + len(burst)] += burst
    return audio


def test_the_wavetone_algorithm_is_the_default(tmp_path) -> None:
    path = tmp_path / "click.wav"
    soundfile.write(path, click_track(120.0), SAMPLE_RATE)

    result = bpm.estimate(path)
    assert result.algorithm == bpm.DEFAULT == "wavetone"
    assert result.bpm == pytest.approx(120.0, abs=0.05)
    assert result.source == bpm.SOURCES["wavetone"]


def test_an_unknown_algorithm_is_refused() -> None:
    with pytest.raises(ValueError, match="Unknown tempo algorithm"):
        bpm.estimate("whatever.wav", "essentia")
