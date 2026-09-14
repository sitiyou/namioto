# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for song playback: decoding, the streaming cursor and the speed step."""

from __future__ import annotations

import numpy as np
import pytest
import soundfile
from PyQt6.QtWidgets import QApplication

from namioto.ui.app import STYLE_SHEET, dark_palette
from namioto.ui.song import (
    SongPlayer,
    _SongSource,
    load_song,
    stretch_song,
)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setStyleSheet(STYLE_SHEET)
    return app


def source_for(samples: np.ndarray) -> tuple[SongPlayer, _SongSource]:
    player = SongPlayer()
    player.load(samples, 1000)  # a kilohertz keeps the frame numbers readable
    return player, _SongSource(player)


def read(source: _SongSource, frames: int) -> np.ndarray:
    return np.frombuffer(source.readData(frames * 2), dtype=np.int16) / 32767.0


def test_load_song_decodes_a_file_to_mono_at_its_own_rate(tmp_path) -> None:
    path = tmp_path / "song.wav"
    time = np.arange(4410) / 4410.0
    soundfile.write(str(path), (np.sin(2 * np.pi * 440 * time) * 0.5).astype(np.float32), 4410)

    samples, sample_rate = load_song(path)
    assert (sample_rate, samples.shape, samples.dtype) == (4410, (4410,), np.dtype(np.float32))
    assert np.max(np.abs(samples)) == pytest.approx(0.5, abs=0.02)


def test_the_song_plays_from_the_seek_position_and_reports_its_length() -> None:
    player = SongPlayer()
    player.load(np.zeros(1000, dtype=np.float32), 1000)

    assert (player.is_loaded, player.duration) == (True, 1.0)
    assert not player.is_playing and player.position == 0.0  # nothing reaches a device in a test
    player.seek(0.4)
    assert player.position == pytest.approx(0.4)
    player.seek(9.0)  # past the end
    assert player.position == pytest.approx(1.0)
    player.seek(-1.0)
    assert player.position == 0.0
    player.stop()
    assert player.position == 0.0
    assert not SongPlayer().is_loaded and SongPlayer().duration == 0.0


def test_the_source_serves_the_song_in_order() -> None:
    _player, source = source_for(np.arange(1000, dtype=np.float32) / 1000.0)
    chunk = read(source, 8)
    assert chunk == pytest.approx([0.0, 0.001, 0.002, 0.003, 0.004, 0.005, 0.006, 0.007], abs=1e-4)
    assert read(source, 4) == pytest.approx([0.008, 0.009, 0.010, 0.011], abs=1e-4)
    assert source.cursor == 12.0


def test_stretching_keeps_the_pitch_where_it_was() -> None:
    rate = 44100
    time = np.arange(rate) / rate
    tone = (0.5 * np.sin(2 * np.pi * 440 * time)).astype(np.float32)

    def peak(samples: np.ndarray) -> float:
        spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
        return float(np.fft.rfftfreq(len(samples), 1 / rate)[spectrum.argmax()])

    for speed in (0.8, 1.25, 2.0):
        stretched = stretch_song(tone, speed)
        assert len(stretched) / rate == pytest.approx(1.0 / speed, rel=0.01)  # the tempo moved
        assert peak(stretched) == pytest.approx(440, abs=1)  # the pitch did not

    assert np.array_equal(stretch_song(tone, 1.0), tone)  # 1x is handed back untouched


def test_the_source_serves_the_rerendered_song() -> None:
    buffer = np.arange(1000, dtype=np.float32) / 1000.0
    player = SongPlayer()
    player.load(np.zeros(500, dtype=np.float32), 1000)  # a half second song
    player.set_stretched(buffer, 2.0)  # rerendered at twice the speed, so it is half as long again
    _source = _SongSource(player)

    assert read(_source, 8) == pytest.approx([0.0, 0.001, 0.002, 0.003, 0.004, 0.005, 0.006, 0.007], abs=1e-4)
    assert _source.cursor == 8
    assert player.duration == pytest.approx(0.5)  # the song's own length, not the buffer's
    assert player.stretch == 2.0


def test_a_stretched_song_keeps_the_playhead_in_song_seconds() -> None:
    player = SongPlayer()
    player.load(np.zeros(4000, dtype=np.float32), 1000)  # a four second song
    player.set_stretched(np.zeros(2000, dtype=np.float32), 2.0)  # rerendered at 2x: two seconds long

    player.play(3.0)  # three seconds into the song is one and a half into the buffer
    assert player._source.cursor == 1500  # noqa: SLF001 - the cursor is what reaches the sink
    player.pause()
    assert player.position == pytest.approx(3.0)
    player.seek(1.0)
    assert player.position == pytest.approx(1.0)
    player.stop()


def test_handing_over_a_stretched_song_carries_on_from_the_playhead(monkeypatch) -> None:
    player = SongPlayer()
    player.load(np.zeros(4000, dtype=np.float32), 1000)
    started: list[float] = []
    monkeypatch.setattr(SongPlayer, "is_playing", property(lambda self: True))
    monkeypatch.setattr(SongPlayer, "position", property(lambda self: 2.5))
    monkeypatch.setattr(SongPlayer, "play", lambda self, seconds=0.0: started.append(seconds))

    buffer = np.ones(2000, dtype=np.float32)
    player.set_stretched(buffer, 2.0)
    assert started == [pytest.approx(2.5)]  # the song carries on from where it was
    assert player.stretch == 2.0 and player.buffer is not None
    assert np.array_equal(player.buffer, buffer)


def test_the_source_scales_with_the_song_volume() -> None:
    player, source = source_for(np.full(100, 0.5, dtype=np.float32))

    assert read(source, 4) == pytest.approx([0.5] * 4, abs=1e-4)
    player.gain = 0.5
    assert read(source, 4) == pytest.approx([0.25] * 4, abs=1e-4)
    player.gain = 0.0
    assert read(source, 4) == pytest.approx([0.0] * 4, abs=1e-4)
