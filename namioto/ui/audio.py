# SPDX-License-Identifier: AGPL-3.0-only
"""Note playback outputs: an external MIDI synth when one is listening, else the built-in one."""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence

import numpy as np
from PyQt6.QtCore import QIODevice, QObject, pyqtSignal
from PyQt6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices, QtAudio

from namioto.playback import SAMPLE_RATE, render_notes

BUFFER_MS = 80
INT16_PEAK = 32767.0
PREVIEW_SECONDS = 0.6
NOTE_ON, NOTE_OFF, VELOCITY = 0x90, 0x80, 100
PROGRAM_CHANGE = (0xC0, 0x00)  # acoustic grand piano on channel 0
SYNTH_NAMES = ("timidity", "fluidsynth", "qsynth", "wavetable")


def find_synth_port(port_names: Sequence[str]) -> int | None:
    """Index of the first port that looks like a software synth; Midi Through is a loopback."""
    for index, name in enumerate(port_names):
        lowered = name.lower()
        if "through" not in lowered and any(part in lowered for part in SYNTH_NAMES):
            return index
    return None


class NotePlayer(QObject):
    """What the window drives: a prepared program, a transport and an audition."""

    finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.gain = 1.0

    def set_program(self, notes: Sequence[tuple[int, float, float]], speed: float) -> None:
        raise NotImplementedError

    def play(self, seconds: float = 0.0) -> None:
        raise NotImplementedError

    def pause(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def seek(self, seconds: float) -> None:
        raise NotImplementedError

    def preview(self, pitch: int, seconds: float = PREVIEW_SECONDS) -> None:
        raise NotImplementedError

    @property
    def duration(self) -> float:
        raise NotImplementedError

    @property
    def position(self) -> float:
        raise NotImplementedError

    @property
    def is_playing(self) -> bool:
        raise NotImplementedError


class _MixSource(QIODevice):
    """Serves the mix to the sink in int16 chunks, scaled by the current gain."""

    def __init__(self, player: MidiSink):
        super().__init__()
        self._player = player
        self.cursor = 0
        self.open(QIODevice.OpenModeFlag.ReadOnly)

    def isSequential(self) -> bool:
        return True

    def bytesAvailable(self) -> int:
        return (len(self._player.mix) - self.cursor) * 2 + super().bytesAvailable()

    def readData(self, maxlen: int) -> bytes:
        mix = self._player.mix
        count = min(maxlen // 2, len(mix) - self.cursor)
        if count <= 0:
            return b""
        chunk = np.clip(mix[self.cursor : self.cursor + count] * self._player.gain, -1.0, 1.0)
        self.cursor += count
        return (chunk * INT16_PEAK).astype(np.int16).tobytes()


class MidiSink(NotePlayer):
    """The built-in synth: the notes rendered into one buffer and streamed to Qt's audio sink."""

    def __init__(self, parent=None, buffer_ms: int = BUFFER_MS, sample_rate: int = SAMPLE_RATE):
        super().__init__(parent)
        self.sample_rate = sample_rate
        self.mix = np.zeros(0, dtype=np.float32)
        self._key: tuple | None = None
        self._speed = 1.0
        self._start = 0.0
        self._sink: QAudioSink | None = None
        self._source: _MixSource | None = None
        self._format = QAudioFormat()
        self._format.setSampleRate(sample_rate)
        self._format.setChannelCount(1)
        self._format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        self._buffer_bytes = int(sample_rate * 2 * buffer_ms / 1000)

    def set_program(self, notes, speed) -> None:
        """Render the notes into a buffer, but only when they or the speed changed."""
        notes = tuple(notes)
        key = (notes, round(speed, 6))
        if key == self._key:
            return
        self.stop()
        self._speed = speed
        self.mix = render_notes(notes, speed=speed)
        self._key = key

    def _load(self, mix: np.ndarray) -> None:
        self.stop()
        self._key = None
        self.mix = np.ascontiguousarray(mix, dtype=np.float32)

    @property
    def duration(self) -> float:
        return len(self.mix) / self.sample_rate * self._speed

    @property
    def position(self) -> float:
        if self._sink is None:
            return self._start
        return self._start + self._sink.processedUSecs() / 1e6 * self._speed

    @property
    def is_playing(self) -> bool:
        return self._sink is not None and self._sink.state() == QtAudio.State.ActiveState

    def play(self, seconds: float = 0.0) -> None:
        self.stop()
        if self.mix.size == 0:
            return
        self._start = max(0.0, min(seconds, self.duration))
        self._source = _MixSource(self)
        self._source.cursor = int(self._start / self._speed * self.sample_rate)
        self._sink = QAudioSink(QMediaDevices.defaultAudioOutput(), self._format, self)
        self._sink.setBufferSize(self._buffer_bytes)
        self._sink.stateChanged.connect(self._on_state_changed)
        self._sink.start(self._source)

    def pause(self) -> None:
        self._start = self.position
        self._close()

    def seek(self, seconds: float) -> None:
        """Move the play position, carrying on from there when it was playing."""
        if self.is_playing:
            self.play(seconds)
        else:
            self._start = max(0.0, min(seconds, self.duration))

    def stop(self) -> None:
        self._start = 0.0
        self._close()

    def preview(self, pitch: int, seconds: float = PREVIEW_SECONDS) -> None:
        """Audition one note; the prepared program has to be rendered again afterwards."""
        self._load(render_notes([(pitch, 0.0, seconds)]))
        self.play()

    def _close(self) -> None:
        if self._sink is not None:
            self._sink.stop()
            self._sink.deleteLater()
            self._sink = None
        self._source = None

    def _on_state_changed(self, state) -> None:
        if state == QtAudio.State.IdleState:  # the source ran out of samples
            self._start = self.duration
            self._close()
            self.finished.emit()


class MidiPortOut(NotePlayer):
    """An external MIDI synth such as TiMidity: the notes are scheduled onto its port."""

    def __init__(self, port, parent=None):
        super().__init__(parent)
        self.port = port
        self._notes: tuple[tuple[int, float, float], ...] = ()
        self._speed = 1.0
        self._start = 0.0
        self._started: float | None = None
        self._stopping = False
        self._sounding: set[int] = set()
        self._thread: threading.Thread | None = None

    def set_program(self, notes, speed) -> None:
        self.stop()
        self._notes = tuple(sorted(notes, key=lambda note: note[1]))
        self._speed = max(0.01, speed)
        self.port.send_message(list(PROGRAM_CHANGE))

    @property
    def duration(self) -> float:
        return max((start + duration for _pitch, start, duration in self._notes), default=0.0)

    @property
    def position(self) -> float:
        if self._started is None:
            return self._start
        return self._start + (time.monotonic() - self._started) * self._speed

    @property
    def is_playing(self) -> bool:
        return self._started is not None

    def play(self, seconds: float = 0.0) -> None:
        self.stop()
        if not self._notes:
            return
        self._start = max(0.0, min(seconds, self.duration))
        self._started = time.monotonic()
        self._stopping = False
        self._thread = threading.Thread(target=self._schedule, daemon=True)
        self._thread.start()

    def pause(self) -> None:
        position = self.position
        self.stop()
        self._start = position

    def seek(self, seconds: float) -> None:
        if self.is_playing:
            self.play(seconds)
        else:
            self._start = max(0.0, min(seconds, self.duration))

    def stop(self) -> None:
        self._stopping = True
        if self._thread is not None:
            self._thread.join(timeout=0.5)
            self._thread = None
        for pitch in tuple(self._sounding):  # a synth keeps sounding until it is told to stop
            self._send(NOTE_OFF, pitch, 0)
        self._sounding.clear()
        self._started = None
        self._start = 0.0

    def preview(self, pitch: int, seconds: float = PREVIEW_SECONDS) -> None:
        self._send(NOTE_ON, pitch, VELOCITY)
        timer = threading.Timer(seconds, self._send, args=(NOTE_OFF, pitch, 0))
        timer.daemon = True
        timer.start()

    def _schedule(self) -> None:
        started = self._started
        events: list[tuple[float, int, int]] = []
        for pitch, start, duration in self._notes:
            if start + duration <= self._start:
                continue
            begin = max(start, self._start)
            events.append(((begin - self._start) / self._speed, NOTE_ON, pitch))
            events.append(((start + duration - self._start) / self._speed, NOTE_OFF, pitch))
        for offset, kind, pitch in sorted(events, key=lambda event: event[0]):
            if not self._wait_until(started + offset):
                return
            self._send(kind, pitch, VELOCITY if kind == NOTE_ON else 0)
        if self._wait_until(started + (self.duration - self._start) / self._speed):
            self._started = None
            self._start = self.duration
            self.finished.emit()

    def _wait_until(self, deadline: float) -> bool:
        while not self._stopping:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            time.sleep(min(remaining, 0.005))
        return False

    def _send(self, status: int, pitch: int, velocity: int) -> None:
        self.port.send_message([status, pitch, velocity])
        if status == NOTE_ON:
            self._sounding.add(pitch)
        else:
            self._sounding.discard(pitch)


def open_player(parent=None) -> tuple[NotePlayer, str]:
    """A player and a description of where it sends the sound.

    The external synth is preferred: it brings its own patches (TiMidity's piano, FluidSynth's
    SoundFont), which is what the rest of the machine already sounds like.
    """
    try:
        import rtmidi

        port = rtmidi.MidiOut()
        index = find_synth_port(port.get_ports())
    except Exception:  # no MIDI backend on this machine
        return MidiSink(parent), "the built-in synth"
    if index is None:
        return MidiSink(parent), "the built-in synth"
    port.open_port(index)
    return MidiPortOut(port, parent), port.get_port_name(index)
