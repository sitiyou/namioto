# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for the WaveTone volume-envelope tempo estimator, which needs no model and no audio."""

from __future__ import annotations

import numpy as np
import pytest
import soundfile

from namioto.beats import SAMPLE_RATE
from namioto.wavetone import FRAME, estimate_array, main, volume_change


def click_track(bpm: float, seconds: float = 90.0, jitter: float = 0.0, missing: float = 0.0, seed: int = 1):
    """A click every beat, optionally loose and with beats dropped, like a real recording."""
    rng = np.random.default_rng(seed)
    audio = np.zeros(int(SAMPLE_RATE * seconds), dtype=np.float32)
    burst = np.exp(-np.arange(int(SAMPLE_RATE * 0.05)) / (SAMPLE_RATE * 0.005))
    burst = (burst * np.sin(2 * np.pi * 180 * np.arange(len(burst)) / SAMPLE_RATE)).astype(np.float32)
    for index, start in enumerate(np.arange(0.0, seconds - 0.1, 60.0 / bpm)):
        if index > 2 and rng.random() < missing:
            continue
        at = max(0, int((start + (rng.normal(0.0, jitter) if jitter else 0.0)) * SAMPLE_RATE))
        audio[at : at + len(burst)] += burst
    return audio


@pytest.mark.parametrize("bpm", [93.0, 120.0, 168.0])
def test_a_click_track_is_measured_to_the_beat(bpm: float) -> None:
    assert estimate_array(click_track(bpm), SAMPLE_RATE) == pytest.approx(bpm, abs=0.05)


def test_loose_and_incomplete_beats_are_still_measured() -> None:
    assert estimate_array(click_track(93.0, jitter=0.02, missing=0.1), SAMPLE_RATE) == pytest.approx(93.0, abs=0.5)


def test_the_envelope_rate_follows_the_sample_rate() -> None:
    envelope, rate = volume_change(np.ones(FRAME * 4, dtype=np.float32), SAMPLE_RATE)
    assert rate == SAMPLE_RATE / FRAME
    assert envelope.shape == (3,)
    assert not envelope.any()  # a steady volume changes by nothing


def test_silence_has_no_tempo() -> None:
    with pytest.raises(ValueError, match="no volume change"):
        estimate_array(np.zeros(SAMPLE_RATE * 5, dtype=np.float32), SAMPLE_RATE)


def test_too_short_audio_raises() -> None:
    with pytest.raises(ValueError, match="too short"):
        estimate_array(np.zeros(FRAME - 1, dtype=np.float32), SAMPLE_RATE)


def test_the_cli_prints_the_measured_tempo(tmp_path, capsys) -> None:
    path = tmp_path / "click.wav"
    soundfile.write(path, click_track(168.0, seconds=60.0), SAMPLE_RATE)
    assert main([str(path)]) == 0
    assert "168.0" in capsys.readouterr().out
