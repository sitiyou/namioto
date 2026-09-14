# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for the beat-tracking tempo estimator, which needs no model and no audio fixtures."""

from __future__ import annotations

import numpy as np
import pytest
import soundfile

from namioto.beats import SAMPLE_RATE, estimate_array, fit_beats, main, refine_grid


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


@pytest.mark.parametrize("bpm", [93.0, 96.0, 120.0, 150.0])
def test_a_click_track_is_measured_to_the_beat(bpm: float) -> None:
    assert estimate_array(click_track(bpm)).bpm == pytest.approx(bpm, abs=0.05)


def test_loose_and_incomplete_beats_are_still_fitted() -> None:
    result = estimate_array(click_track(93.0, jitter=0.02, missing=0.1))
    assert result.bpm == pytest.approx(93.0, abs=0.1)
    assert result.agreement > 0.5


def test_the_fit_is_exact_on_a_regular_grid() -> None:
    beats = np.arange(0.0, 120.0, 60.0 / 93.0)
    bpm, residual = fit_beats(beats)
    assert bpm == pytest.approx(93.0)
    assert residual == pytest.approx(0.0, abs=1e-9)
    assert refine_grid(beats, bpm) == pytest.approx(93.0, abs=0.05)


def test_the_grid_refit_repairs_a_drifted_fit() -> None:
    beats = np.arange(0.0, 240.0, 60.0 / 93.0)
    beats[len(beats) // 3 :] += 0.3  # the tracker skipped a beat and stayed late from there on
    drifted, _ = fit_beats(beats)
    assert drifted < 92.9  # the straight line is dragged down by the shift
    assert refine_grid(beats, drifted) == pytest.approx(93.0, abs=0.1)


def test_a_tempo_change_shows_up_in_the_windows() -> None:
    slow = click_track(93.0, seconds=60.0)
    fast = click_track(105.0, seconds=60.0)
    result = estimate_array(np.concatenate([slow, fast]))
    assert len(result.local) > 4
    assert result.local[0].bpm == pytest.approx(93.0, abs=1.0)
    assert result.local[-1].bpm == pytest.approx(105.0, abs=1.0)
    assert result.agreement < 0.5  # so the suggestion is shown as a weak one


def test_silence_has_no_tempo() -> None:
    with pytest.raises(ValueError, match="too few"):
        estimate_array(np.zeros(SAMPLE_RATE * 5, dtype=np.float32))


def test_the_cli_prints_the_measured_tempo(tmp_path, capsys) -> None:
    path = tmp_path / "click.wav"
    soundfile.write(path, click_track(93.0, seconds=60.0), SAMPLE_RATE)
    assert main([str(path)]) == 0
    printed = capsys.readouterr().out
    assert "93.0" in printed and "beats" in printed
