# SPDX-License-Identifier: AGPL-3.0-only
"""The native audio layer: the vendored Signalsmith Stretch reached through the build.

No audio device is opened here: `Stretcher` is pure DSP and `Engine::pull` is driven frame by frame,
so the whole realtime path is judged deterministically. `tests/conftest.py` forbids a display and an
audio device in the whole suite.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from namioto import _audio

SAMPLE_RATE = 48000
TONE = 440.0
EDGE = 4096  # latency and window edges carry artefacts, so only the steady middle is judged


def _tone(frequency: float, seconds: float) -> np.ndarray:
    return np.sin(2 * np.pi * frequency * np.arange(int(SAMPLE_RATE * seconds)) / SAMPLE_RATE).astype(np.float32)


def _dominant_frequency(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> float:
    windowed = samples * np.hanning(len(samples))
    spectrum = np.abs(np.fft.rfft(windowed))
    return float(np.fft.rfftfreq(len(samples), 1 / sample_rate)[np.argmax(spectrum)])


def _rms(samples: np.ndarray) -> float:
    return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))


def test_versions_are_reported():
    assert _audio.stretch_version()
    assert _audio.miniaudio_version()


@pytest.mark.parametrize("speed", [1.0, 2.0, 0.5])
def test_stretcher_speed_scales_the_length_and_leaves_the_pitch(speed: float):
    source = _tone(TONE, 2.0)

    stretcher = _audio.Stretcher(SAMPLE_RATE)
    output = stretcher.process(source, speed)

    assert abs(len(output) - len(source) / speed) <= 2
    assert abs(_dominant_frequency(output[EDGE:-EDGE]) - TONE) < 5.0


@pytest.mark.parametrize("speed", [1.0, 2.0, 0.5])
def test_engine_speed_scales_the_position_and_leaves_the_pitch(speed: float):
    engine = _audio.Engine(SAMPLE_RATE)
    engine.load(_tone(TONE, 4.0))
    engine.speed = speed
    engine.play(0.0)

    output = engine.pull(SAMPLE_RATE // 2)  # half a second of output

    assert abs(_dominant_frequency(output[EDGE:-EDGE]) - TONE) < 5.0
    assert engine.position == pytest.approx(0.5 * speed, abs=0.02)


def test_engine_seek_lands_on_the_new_source_position():
    engine = _audio.Engine(SAMPLE_RATE)
    engine.load(np.concatenate([_tone(440.0, 1.0), _tone(880.0, 1.0)]))
    engine.play(0.0)
    engine.pull(SAMPLE_RATE // 4)

    engine.seek(1.0)
    output = engine.pull(SAMPLE_RATE // 4)

    assert abs(_dominant_frequency(output[EDGE:-EDGE]) - 880.0) < 10.0
    assert engine.position == pytest.approx(1.25, abs=0.03)


def test_engine_pause_is_silent_and_resumes():
    engine = _audio.Engine(SAMPLE_RATE)
    engine.load(_tone(TONE, 2.0))
    engine.play(0.0)
    assert np.any(engine.pull(SAMPLE_RATE // 4) != 0)

    engine.pause()
    assert np.all(engine.pull(SAMPLE_RATE // 4) == 0)

    engine.play(engine.position)
    assert np.any(engine.pull(SAMPLE_RATE // 4) != 0)


def test_engine_gain_scales_the_output():
    loud = _audio.Engine(SAMPLE_RATE)
    loud.load(_tone(TONE, 2.0))
    loud.play(0.0)
    quiet = _audio.Engine(SAMPLE_RATE)
    quiet.load(_tone(TONE, 2.0))
    quiet.gain = 0.25
    quiet.play(0.0)

    reference = _rms(loud.pull(SAMPLE_RATE // 2)[EDGE:-EDGE])
    scaled = _rms(quiet.pull(SAMPLE_RATE // 2)[EDGE:-EDGE])

    assert scaled == pytest.approx(0.25 * reference, rel=1e-3)


def test_engine_stops_at_the_end():
    engine = _audio.Engine(SAMPLE_RATE)
    engine.load(_tone(TONE, 0.1))
    engine.play(0.0)

    engine.pull(SAMPLE_RATE)  # a whole second of output from a tenth of a second of source

    assert engine.finished
    assert not engine.playing
    assert engine.position == pytest.approx(engine.duration)


def test_engine_a_speed_change_keeps_the_source_position():
    engine = _audio.Engine(SAMPLE_RATE)
    engine.load(np.zeros(SAMPLE_RATE * 4, dtype=np.float32))
    engine.play(0.0)

    engine.pull(SAMPLE_RATE // 2)  # half a source second at 1x
    engine.speed = 0.5
    engine.pull(SAMPLE_RATE // 2)  # half an output second at 0.5x is a quarter of source

    assert engine.position == pytest.approx(0.75)  # the rate changed where it stood, no reset


def test_engine_mixes_a_submitted_buffer():
    engine = _audio.Engine(SAMPLE_RATE)
    engine.load(np.zeros(SAMPLE_RATE * 2, dtype=np.float32))
    engine.submit_buffer(np.full(SAMPLE_RATE // 10, 0.5, dtype=np.float32), 0.5, 0.5, id=1)
    engine.play(0.0)

    output = engine.pull(SAMPLE_RATE)  # one second of output

    assert np.allclose(output[: int(0.49 * SAMPLE_RATE)], 0.0, atol=1e-4)
    assert np.allclose(output[int(0.51 * SAMPLE_RATE) : int(0.59 * SAMPLE_RATE)], 0.25, atol=1e-2)
    assert np.allclose(output[int(0.61 * SAMPLE_RATE) :], 0.0, atol=1e-4)


def test_engine_clear_buffers_drops_a_submitted_buffer():
    engine = _audio.Engine(SAMPLE_RATE)
    engine.load(np.zeros(SAMPLE_RATE * 2, dtype=np.float32))
    engine.submit_buffer(np.ones(SAMPLE_RATE // 2, dtype=np.float32), 0.0, 1.0, id=1)
    engine.clear_buffers()
    engine.play(0.0)

    assert np.allclose(engine.pull(SAMPLE_RATE // 2), 0.0, atol=1e-4)


def test_engine_replaces_a_buffer_with_the_same_id():
    engine = _audio.Engine(SAMPLE_RATE)
    engine.load(np.zeros(SAMPLE_RATE * 2, dtype=np.float32))
    engine.submit_buffer(np.ones(4800, dtype=np.float32), 0.0, 1.0, id=7)
    engine.submit_buffer(np.full(4800, 0.25, dtype=np.float32), 0.0, 1.0, id=7)
    engine.play(0.0)

    output = engine.pull(4800)

    assert np.allclose(output[EDGE:-EDGE], 0.25, atol=1e-3)


def test_engine_a_higher_speed_reaches_a_marker_sooner():
    def arrival(speed: float) -> float:
        engine = _audio.Engine(SAMPLE_RATE)
        source = np.zeros(SAMPLE_RATE, dtype=np.float32)
        source[SAMPLE_RATE // 2 :] = 1.0  # a step at half a second
        engine.load(source)
        engine.speed = speed
        engine.play(0.0)
        output = engine.pull(int(SAMPLE_RATE * (0.5 / speed + 0.4)))
        return int(np.argmax(output > 0.5)) / SAMPLE_RATE

    # the step is heard in source time, so a faster rate must reach it sooner
    assert arrival(2.0) < arrival(1.0) < arrival(0.5)


def _output(samples: np.ndarray, block_frames: int = 1024) -> _audio.Output:
    """An `Output` on miniaudio's null backend: real time, but no audio device."""
    output = _audio.Output(SAMPLE_RATE, block_frames)
    assert output.open(null_backend=True), output.error()
    output.load(samples)
    return output


def test_output_plays_and_reports_the_position():
    output = _output(_tone(TONE, 2.0))
    try:
        output.play(0.0)
        time.sleep(0.2)
        assert output.playing
        assert 0.05 < output.position < 0.6
    finally:
        output.close()


def test_output_pause_freezes_and_seek_moves():
    output = _output(_tone(TONE, 2.0))
    try:
        output.play(0.0)
        time.sleep(0.15)
        output.pause()
        time.sleep(0.05)
        paused = output.position
        time.sleep(0.1)

        assert not output.playing
        assert output.position == pytest.approx(paused, abs=0.02)

        output.seek(1.0)
        assert output.position == pytest.approx(1.0, abs=0.02)
    finally:
        output.close()


def test_output_stops_when_the_song_ends():
    output = _output(_tone(TONE, 0.2))
    try:
        output.play(0.0)
        deadline = time.monotonic() + 3.0
        while not output.finished and time.monotonic() < deadline:
            time.sleep(0.02)

        assert output.finished
        assert not output.playing
        assert output.position == pytest.approx(output.duration, abs=0.05)
    finally:
        output.close()


def test_output_speed_changes_keep_the_playhead_advancing():
    output = _output(_tone(TONE, 4.0))
    try:
        output.play(0.0)
        time.sleep(0.1)
        dragged = output.position
        for speed in (0.5, 1.0, 2.0, 0.5, 1.0):  # what dragging the slider settles through
            output.speed = speed
            time.sleep(0.03)

        assert output.playing
        assert output.position >= dragged  # a rate change never sends the playhead back
        time.sleep(0.2)
        assert output.position > dragged
    finally:
        output.close()
