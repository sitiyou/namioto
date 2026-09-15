# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for the note renderer: pitch, envelope, mixing and the speed control."""

from __future__ import annotations

import numpy as np
import pytest

from namioto.playback import SAMPLE_RATE, TAIL, note_frequency, render_notes


def dominant_frequency(mix: np.ndarray) -> float:
    spectrum = np.abs(np.fft.rfft(mix * np.hanning(len(mix))))
    return float(np.fft.rfftfreq(len(mix), 1 / SAMPLE_RATE)[spectrum.argmax()])


def rms(values: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(values))))


@pytest.mark.parametrize(("pitch", "frequency"), [(69, 440.0), (81, 880.0), (57, 220.0), (60, 261.63)])
def test_note_frequency_follows_equal_temperament(pitch, frequency):
    assert note_frequency(pitch) == pytest.approx(frequency, abs=0.01)


def test_a_note_renders_its_own_pitch():
    for pitch, frequency in ((69, 440.0), (57, 220.0), (81, 880.0)):
        assert dominant_frequency(render_notes([(pitch, 0.0, 0.5)])) == pytest.approx(frequency, abs=2.0)


def test_the_mix_covers_the_notes_and_a_release_tail():
    mix = render_notes([(60, 0.0, 1.0)])
    assert len(mix) == round((1.0 + TAIL) * SAMPLE_RATE)
    assert mix.dtype == np.float32
    assert render_notes([]).size == 0


def test_a_note_starts_at_its_own_offset():
    mix = render_notes([(69, 1.0, 0.2)])
    head = int(0.5 * SAMPLE_RATE)
    assert np.allclose(mix[:head], 0.0)
    assert np.max(np.abs(mix[head:])) > 0.01


def test_the_envelope_fades_in_and_out():
    mix = render_notes([(69, 0.0, 0.5)])
    assert rms(mix[:64]) < rms(mix[264:882])  # the attack ramps up
    assert rms(mix[21000:22050]) < rms(mix[11025:12075])  # and the release fades out
    assert rms(mix[22050:]) == 0.0  # nothing left after the note itself


def test_overlapping_notes_add_up():
    chord = render_notes([(60, 0.0, 0.5), (67, 0.0, 0.5)])  # C4 and G4 together
    spectrum = np.abs(np.fft.rfft(chord * np.hanning(len(chord))))
    for pitch in (60, 67):
        center = round(note_frequency(pitch) * len(chord) / SAMPLE_RATE)
        assert spectrum[center - 3 : center + 4].max() > 0.05 * spectrum.max()


def test_dense_passages_do_not_clip():
    notes = [(pitch, 0.0, 0.5) for pitch in (36, 48, 52, 55, 60, 64, 67)] * 4
    assert np.max(np.abs(render_notes(notes))) <= 1.0


def test_speed_scales_the_timeline_without_moving_the_pitch():
    slow = render_notes([(69, 0.0, 0.4)], speed=0.5)
    fast = render_notes([(69, 0.0, 0.4)], speed=2.0)
    assert len(slow) == pytest.approx(4 * len(fast), rel=0.01)
    assert dominant_frequency(slow) == pytest.approx(dominant_frequency(fast), rel=0.01)


def test_a_channel_gains_its_own_volume():
    # both renders stay under the clipping peak, so the mix is not normalised and the ratio is exact
    quiet = render_notes([(69, 0.0, 0.4, 0)], channels=((0, 0, 10),))
    loud = render_notes([(69, 0.0, 0.4, 0)], channels=((0, 0, 20),))
    assert rms(quiet) == pytest.approx(rms(loud) * 0.5, rel=0.01)


def test_a_channel_picks_the_voice_of_its_program():
    piano = render_notes([(69, 0.0, 1.0, 0)], channels=((0, 0, 100),))
    organ = render_notes([(69, 0.0, 1.0, 0)], channels=((0, 16, 100),))
    head = slice(0, int(0.1 * SAMPLE_RATE))
    tail = slice(int(0.8 * SAMPLE_RATE), int(0.9 * SAMPLE_RATE))
    # the piano has decayed away by the tail, the organ is sustained and keeps sounding
    assert rms(piano[tail]) < 0.3 * rms(piano[head])
    assert rms(organ[tail]) > 0.8 * rms(organ[head])


def test_a_note_without_a_channel_plays_on_the_default_one():
    plain = render_notes([(69, 0.0, 0.4)])
    channelled = render_notes([(69, 0.0, 0.4, 0)], channels=((0, 0, 100),))
    assert np.array_equal(plain, channelled)
