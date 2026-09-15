# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for song playback: decoding, the streaming cursor and the speed step."""

from __future__ import annotations

import numpy as np
import pytest
import soundfile
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtMultimedia import QtAudio
from PyQt6.QtWidgets import QApplication

from namioto.ui import theme
from namioto.ui.audio import _FloatSource
from namioto.ui.song import SongPlayer, TimeStretcher, load_song


class FakeSink(QObject):
    """Qt's sound output with no sound card behind it: a test must not play into the room."""

    stateChanged = pyqtSignal(object)

    def __init__(self, *_args) -> None:
        super().__init__()
        self._state = QtAudio.State.StoppedState

    def setBufferSize(self, size: int) -> None:
        pass

    def start(self, source=None) -> None:
        self._state = QtAudio.State.ActiveState

    def stop(self) -> None:
        self._state = QtAudio.State.StoppedState

    def state(self):
        return self._state

    def processedUSecs(self) -> int:
        """A sink that has processed nothing, so the playhead stays where the song left it."""
        return 0


class TimedSink(FakeSink):
    """A sink a test can tell how much of the stream it has played."""

    def __init__(self, *_args) -> None:
        super().__init__(*_args)
        self.usecs = 0

    def processedUSecs(self) -> int:
        return self.usecs


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    theme.apply(app)
    return app


def source_for(samples: np.ndarray) -> tuple[SongPlayer, _FloatSource]:
    player = SongPlayer()
    player.load(samples, 1000)  # a kilohertz keeps the frame numbers readable
    return player, _FloatSource(player)


def read(source: _FloatSource, frames: int) -> np.ndarray:
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


def render(samples: np.ndarray, speed: float, sample_rate: int, chunk: int = 4096) -> np.ndarray:
    """Play a whole song through the vocoder in one go, the way the sink pulls it."""
    stretcher = TimeStretcher()
    stretcher.load(samples, sample_rate)
    stretcher.start(0.0, speed)
    pieces = []
    while stretcher.remaining():
        pieces.append(stretcher.read(chunk))
    return np.concatenate(pieces) if pieces else np.zeros(0, dtype=np.float32)


def test_stretching_keeps_the_pitch_where_it_was() -> None:
    rate = 44100
    time = np.arange(rate) / rate
    tone = (0.5 * np.sin(2 * np.pi * 440 * time)).astype(np.float32)

    def peak(samples: np.ndarray) -> float:
        spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
        return float(np.fft.rfftfreq(len(samples), 1 / rate)[spectrum.argmax()])

    for speed in (0.8, 1.25, 2.0):
        stretched = render(tone, speed, rate)
        assert len(stretched) / rate == pytest.approx(1.0 / speed, rel=0.01)  # the tempo moved
        assert peak(stretched) == pytest.approx(440, abs=1)  # the pitch did not

    assert np.array_equal(render(tone, 1.0, rate), tone)  # 1x is handed back untouched


def test_the_stretcher_reads_the_same_in_any_chunk_size() -> None:
    rate = 4410
    tone = np.sin(2 * np.pi * 220 * np.arange(rate) / rate).astype(np.float32)
    assert np.array_equal(render(tone, 1.3, rate, chunk=100_000), render(tone, 1.3, rate, chunk=512))


def test_the_source_serves_the_rerendered_song() -> None:
    player = SongPlayer()
    player.load(np.zeros(4000, dtype=np.float32), 1000)  # a four second song
    player.speed = 2.0  # rerendered at twice the speed, so half as many samples come out
    source = _FloatSource(player)

    assert player.duration == pytest.approx(4.0)  # the song's own length, not the output's
    assert player.remaining == 2000  # at 2x, a two second buffer
    assert player.speed == 2.0
    assert read(source, 8) == pytest.approx([0.0] * 8, abs=1e-4)  # a silent song, served as it goes


def test_a_seek_serves_the_song_from_where_the_playhead_lands(monkeypatch) -> None:
    monkeypatch.setattr("namioto.ui.audio.QAudioSink", FakeSink)
    player = SongPlayer()
    player.load(np.arange(4000, dtype=np.float32) / 4000.0, 1000)

    player.play(3.0)  # three seconds into the song
    assert player.position == pytest.approx(3.0)
    source = player._source
    assert source is not None
    assert read(source, 4) == pytest.approx([0.75, 0.75025, 0.7505, 0.75075], abs=1e-4)
    player.pause()
    assert player.position == pytest.approx(3.0)
    player.seek(1.0)
    assert player.position == pytest.approx(1.0)
    player.stop()


def test_a_speed_change_while_playing_carries_on_from_the_playhead(monkeypatch) -> None:
    monkeypatch.setattr("namioto.ui.audio.QAudioSink", FakeSink)
    player = SongPlayer()
    player.load(np.arange(4000, dtype=np.float32) / 4000.0, 1000)  # a four second song
    player.play(1.0)
    assert player.is_playing and player.position == pytest.approx(1.0)

    player.speed = 2.0
    assert player.speed == 2.0 and player.is_playing
    assert player.position == pytest.approx(1.0)  # carries on from where it was
    assert player.remaining == 1500  # at 2x, the rest of the four second song
    player.pause()


def test_a_speed_change_does_not_restart_the_sink(monkeypatch) -> None:
    created: list[FakeSink] = []

    class CountingSink(FakeSink):
        def __init__(self, *_args) -> None:
            super().__init__(*_args)
            created.append(self)

    monkeypatch.setattr("namioto.ui.audio.QAudioSink", CountingSink)
    player = SongPlayer()
    player.load(np.zeros(8000, dtype=np.float32), 1000)
    player.play(0.0)
    assert len(created) == 1

    player.speed = 2.0
    assert len(created) == 1  # the vocoder retuned where it stood, the sink kept running
    assert player.is_playing


def test_the_playhead_counts_each_rate_from_where_it_changed(monkeypatch) -> None:
    monkeypatch.setattr("namioto.ui.audio.QAudioSink", TimedSink)
    player = SongPlayer()
    player.load(np.zeros(8000, dtype=np.float32), 1000)
    player.play(0.0)
    assert player._sink is not None
    player._sink.usecs = 1_000_000  # a second of output at 1x is a second of song

    player.speed = 2.0
    assert player.position == pytest.approx(1.0)  # no jump when the rate changes

    player._sink.usecs = 1_500_000  # another half second of output at 2x is a second more
    assert player.position == pytest.approx(2.0)
    player.pause()


def test_the_source_scales_with_the_song_volume() -> None:
    player, source = source_for(np.full(100, 0.5, dtype=np.float32))

    assert read(source, 4) == pytest.approx([0.5] * 4, abs=1e-4)
    player.gain = 0.5
    assert read(source, 4) == pytest.approx([0.25] * 4, abs=1e-4)
    player.gain = 0.0
    assert read(source, 4) == pytest.approx([0.0] * 4, abs=1e-4)
