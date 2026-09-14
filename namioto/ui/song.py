# SPDX-License-Identifier: AGPL-3.0-only
"""Song playback: the decoded audio file streamed to Qt's audio sink.

The buffer stays mono float32 at the file's own sample rate, so seeking is a cursor move and the
speed control is a fractional step over it: one output frame consumes `speed` frames of the song.
That keeps the audio and the notes on the same timeline - the sink plays one second of wall clock
per second, and the song advances `speed` seconds of its own with it.
"""

from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np
from PyQt6.QtCore import QIODevice, QObject, pyqtSignal
from PyQt6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices, QtAudio

BUFFER_MS = 80
INT16_PEAK = 32767.0


def load_song(path: str | Path) -> tuple[np.ndarray, int]:
    """Decode a file into one mono buffer at its own sample rate, ready for the sink."""
    samples, sample_rate = librosa.load(path, sr=None, mono=True)
    return np.ascontiguousarray(samples, dtype=np.float32), int(sample_rate)


class _SongSource(QIODevice):
    """Serves the song in int16 chunks, taking `speed` song frames per output frame."""

    def __init__(self, player: SongPlayer):
        super().__init__()
        self._player = player
        self.cursor = 0.0
        self.open(QIODevice.OpenModeFlag.ReadOnly)

    def isSequential(self) -> bool:
        return True

    def bytesAvailable(self) -> int:
        return self._frames_left() * 2 + super().bytesAvailable()

    def readData(self, maxlen: int) -> bytes:
        samples = self._player.samples
        step = self._player.speed
        count = min(maxlen // 2, self._frames_left())
        if count <= 0:
            return b""
        positions = self.cursor + np.arange(count) * step
        whole = positions.astype(np.int64)
        if step == 1.0:
            chunk = samples[whole]
        else:
            fraction = (positions - whole).astype(np.float32)
            following = samples[np.minimum(whole + 1, len(samples) - 1)]
            chunk = samples[whole] * (1.0 - fraction) + following * fraction
        self.cursor = float(positions[-1]) + step
        chunk = np.clip(chunk * self._player.gain, -1.0, 1.0)
        return (chunk * INT16_PEAK).astype(np.int16).tobytes()

    def _frames_left(self) -> int:
        remaining = (len(self._player.samples) - self.cursor) / self._player.speed
        return max(0, int(remaining))


class SongPlayer(QObject):
    """The loaded audio file, carrying the transport surface the window drives."""

    finished = pyqtSignal()

    def __init__(self, parent=None, buffer_ms: int = BUFFER_MS):
        super().__init__(parent)
        self.gain = 1.0
        self.speed = 1.0
        self.samples = np.zeros(0, dtype=np.float32)
        self.sample_rate = 0
        self._start = 0.0
        self._sink: QAudioSink | None = None
        self._source: _SongSource | None = None
        self._buffer_ms = buffer_ms

    def load(self, samples: np.ndarray, sample_rate: int) -> None:
        self.stop()
        self.samples = np.ascontiguousarray(samples, dtype=np.float32)
        self.sample_rate = int(sample_rate)

    @property
    def is_loaded(self) -> bool:
        return self.samples.size > 0 and self.sample_rate > 0

    @property
    def duration(self) -> float:
        return len(self.samples) / self.sample_rate if self.is_loaded else 0.0

    @property
    def position(self) -> float:
        if self._sink is None:
            return self._start
        return min(self.duration, self._start + self._sink.processedUSecs() / 1e6 * self.speed)

    @property
    def is_playing(self) -> bool:
        return self._sink is not None and self._sink.state() == QtAudio.State.ActiveState

    def set_speed(self, speed: float) -> None:
        """Change the step, carrying on from where the playhead sits."""
        speed = max(0.01, speed)
        if speed == self.speed:
            return
        self.speed = speed
        if self.is_playing:
            self.play(self.position)

    def play(self, seconds: float = 0.0) -> None:
        self._close()
        if not self.is_loaded:
            return
        self._start = max(0.0, min(seconds, self.duration))
        self._source = _SongSource(self)
        self._source.cursor = self._start * self.sample_rate
        self._sink = QAudioSink(QMediaDevices.defaultAudioOutput(), self._format(), self)
        self._sink.setBufferSize(int(self.sample_rate * 2 * self._buffer_ms / 1000))
        self._sink.stateChanged.connect(self._on_state_changed)
        self._sink.start(self._source)

    def pause(self) -> None:
        self._start = self.position
        self._close()

    def stop(self) -> None:
        self._start = 0.0
        self._close()

    def seek(self, seconds: float) -> None:
        """Move the playhead, carrying on from there when the song was playing."""
        if self.is_playing:
            self.play(seconds)
        else:
            self._start = max(0.0, min(seconds, self.duration))

    def _format(self) -> QAudioFormat:
        audio_format = QAudioFormat()
        audio_format.setSampleRate(self.sample_rate)
        audio_format.setChannelCount(1)
        audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        return audio_format

    def _close(self) -> None:
        if self._sink is not None:
            self._sink.stop()
            self._sink.deleteLater()
            self._sink = None
        self._source = None

    def _on_state_changed(self, state) -> None:
        if state != QtAudio.State.IdleState:
            return
        position = self.position
        if self._source is not None and self._source.bytesAvailable() > 0 and position > self._start + 1e-3:
            # Qt's sink is fed by a timer on the GUI thread, so it runs dry while the analysis
            # threads hold the GIL. Hand it the rest of the song instead of calling it finished;
            # the progress check stops a sink that gets nothing through from spinning on the spot
            self.play(position)
            return
        self._start = self.duration
        self._close()
        self.finished.emit()
