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
)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setStyleSheet(STYLE_SHEET)
    return app


def source_for(samples: np.ndarray, speed: float = 1.0) -> tuple[SongPlayer, _SongSource]:
    player = SongPlayer()
    player.load(samples, 1000)  # a kilohertz keeps the frame numbers readable
    player.set_speed(speed)
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


def test_the_source_steps_faster_than_one_frame_at_a_time() -> None:
    player, source = source_for(np.arange(1000, dtype=np.float32) / 1000.0, speed=2.0)

    assert read(source, 4) == pytest.approx([0.0, 0.002, 0.004, 0.006], abs=1e-4)
    assert source.cursor == 8.0
    assert source.bytesAvailable() == 992  # 496 output frames left, two bytes each
    assert player.duration == pytest.approx(1.0)  # the song is still one second long


def test_the_source_interpolates_between_frames_when_slower() -> None:
    _player, source = source_for(np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32), speed=0.5)

    assert read(source, 4) == pytest.approx([0.0, 0.5, 1.0, 0.5], abs=1e-4)


def test_the_source_runs_out_at_the_end_of_the_song() -> None:
    _player, source = source_for(np.zeros(10, dtype=np.float32))

    assert len(source.readData(40)) == 20  # ten frames, then nothing
    assert source.readData(20) == b""
    assert source.bytesAvailable() == 0


def test_speed_changes_restart_the_stream_from_the_playhead(monkeypatch) -> None:
    player = SongPlayer()
    player.load(np.zeros(1000, dtype=np.float32), 1000)
    started: list[float] = []
    monkeypatch.setattr(SongPlayer, "is_playing", property(lambda self: True))
    monkeypatch.setattr(SongPlayer, "position", property(lambda self: 0.25))
    monkeypatch.setattr(SongPlayer, "play", lambda self, seconds=0.0: started.append(seconds))

    player.set_speed(2.0)
    player.set_speed(2.0)  # the same step does not touch the stream
    assert started == [pytest.approx(0.25)] and player.speed == 2.0
