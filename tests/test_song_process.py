# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for the process-backed player: the protocol, the extrapolated clock and the child's job.

The child is driven tick by tick and the parent's spawn seam is faked, so no test starts a process
or opens an audio device. The song travels as it does in the app: through a shared segment.
"""

from __future__ import annotations

import queue
import time
from collections.abc import Callable
from multiprocessing import shared_memory

import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

from namioto.ui.song_process import (
    FINISHED,
    LOAD,
    LOADED,
    PAUSE,
    PLAY,
    POSITION,
    SEEK,
    SPEED,
    UNLOAD,
    SongProcess,
    _Child,
)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    return QApplication.instance() or QApplication([])


class LoopbackPlayer:
    """The player the child runs, with the surface but no sound: the clock is real, the buffer is not."""

    def __init__(self) -> None:
        self.duration = 0.0
        self._speed = 1.0
        self._gain = 1.0
        self._buffer_ms = 80
        self._playing = False
        self._base = 0.0
        self._since = 0.0

    @property
    def is_loaded(self) -> bool:
        return self.duration > 0.0

    @property
    def is_playing(self) -> bool:
        return self._playing and self.position < self.duration

    @property
    def position(self) -> float:
        if not self._playing:
            return self._base
        return min(self.duration, self._base + (time.monotonic() - self._since) * self._speed)

    def load(self, samples, sample_rate: int) -> None:
        rate = int(sample_rate)
        self.duration = len(samples) / rate if rate else 0.0
        self._base = 0.0
        self._playing = False

    def unload(self) -> None:
        self.load([], 0)

    def play(self, seconds: float = 0.0) -> None:
        self.seek(seconds)
        self._playing = self.duration > 0.0

    def pause(self) -> None:
        self._base = self.position
        self._playing = False

    def stop(self) -> None:
        self._base = 0.0
        self._playing = False

    def seek(self, seconds: float) -> None:
        self._base = max(0.0, min(float(seconds), self.duration))
        self._since = time.monotonic()

    @property
    def speed(self) -> float:
        return self._speed

    @speed.setter
    def speed(self, value: float) -> None:
        self._base = self.position
        self._since = time.monotonic()
        self._speed = max(0.01, float(value))

    @property
    def gain(self) -> float:
        return self._gain

    @gain.setter
    def gain(self, value: float) -> None:
        self._gain = max(0.0, min(1.0, float(value)))

    @property
    def buffer_ms(self) -> int:
        return self._buffer_ms

    @buffer_ms.setter
    def buffer_ms(self, value: int) -> None:
        self._buffer_ms = max(10, int(value))


class FakeProcess:
    """The child handle, with the exit code the parent reads after losing it."""

    def __init__(self, exitcode=None):
        self.exitcode = exitcode

    def is_alive(self) -> bool:
        return self.exitcode is None

    def join(self, timeout=None) -> None:
        pass

    def terminate(self) -> None:
        self.exitcode = -15


def fake_child():
    """The spawn seam replaced by in-process queues and a child that is alive until it is not."""
    commands: queue.Queue = queue.Queue()
    events: queue.Queue = queue.Queue()
    process = FakeProcess()

    def start():
        return process, commands, events

    return start, commands, events, process


def sent(commands: queue.Queue) -> list:
    out = []
    while True:
        try:
            out.append(commands.get_nowait())
        except queue.Empty:
            return out


def child_for(make_player: Callable[[], object] = LoopbackPlayer) -> tuple:
    commands: queue.Queue = queue.Queue()
    events: queue.Queue = queue.Queue()
    return _Child(commands, events, make_player), commands, events


def push_song(child: _Child, seconds: float, rate: int = 44100):
    """Hand the child a song the way the parent does: through a shared segment."""
    samples = np.zeros(max(1, int(seconds * rate)), dtype=np.float32)
    shared = shared_memory.SharedMemory(create=True, size=samples.nbytes)
    np.ndarray(samples.shape, dtype=samples.dtype, buffer=shared.buf)[:] = samples
    child.commands.put((LOAD, shared.name, samples.shape, str(samples.dtype), rate))
    return shared


def last_position(events: queue.Queue):
    found = None
    while not events.empty():
        message = events.get_nowait()
        if message[0] == POSITION:
            found = message
    return found


# ---------- the parent's half ----------


def test_a_load_reports_the_duration_and_tells_the_child(qt_app) -> None:
    start, commands, _events, _process = fake_child()
    song = SongProcess(start=start)

    song.load(np.zeros(44100 * 2, dtype=np.float32), 44100)

    assert song.duration == pytest.approx(2.0)
    assert song.is_loaded
    message = sent(commands)[0]
    assert message[0] == LOAD
    assert message[4] == 44100
    song.close()


def test_a_rate_change_alone_does_not_start_the_child(qt_app) -> None:
    start, _commands, _events, _process = fake_child()
    started: list[bool] = []

    def counting():
        started.append(True)
        return start()

    song = SongProcess(start=counting)

    song.speed = 1.5
    song.gain = 0.5
    song.unload()
    song.pause()
    song.stop()

    assert started == []


def test_the_shared_segment_is_freed_once_the_child_has_it(qt_app) -> None:
    start, _commands, events, _process = fake_child()
    song = SongProcess(start=start)
    song.load(np.zeros(44100, dtype=np.float32), 44100)
    assert len(song._pending) == 1

    events.put((LOADED,))
    song._drain()

    assert not song._pending


def test_the_position_extrapolates_from_the_childs_anchor(qt_app) -> None:
    start, _commands, events, _process = fake_child()
    song = SongProcess(start=start)
    song.load(np.zeros(44100 * 10, dtype=np.float32), 44100)
    song.speed = 2.0
    song.play()

    anchor = time.monotonic()
    events.put((POSITION, 1.0, anchor, True))
    song._drain()
    first = song.position
    time.sleep(0.05)

    assert song.position > first
    assert song.position - 1.0 == pytest.approx(2.0 * (time.monotonic() - anchor), abs=0.05)
    song.close()


def test_a_pause_holds_the_playhead_where_it_was(qt_app) -> None:
    start, commands, events, _process = fake_child()
    song = SongProcess(start=start)
    song.load(np.zeros(44100 * 10, dtype=np.float32), 44100)
    events.put((POSITION, 1.0, time.monotonic(), True))
    song._drain()

    song.pause()
    held = song.position
    time.sleep(0.03)

    assert not song.is_playing
    assert song.position == pytest.approx(held)
    assert sent(commands)[-1] == (PAUSE,)
    song.close()


def test_a_new_rate_does_not_move_the_playhead(qt_app) -> None:
    start, commands, events, _process = fake_child()
    song = SongProcess(start=start)
    song.load(np.zeros(44100 * 10, dtype=np.float32), 44100)
    events.put((POSITION, 2.0, time.monotonic(), True))
    song._drain()
    before = song.position

    song.speed = 2.0

    assert song.position == pytest.approx(before, abs=0.02)
    assert sent(commands)[-1] == (SPEED, 2.0)
    song.close()


def test_the_end_of_the_song_puts_the_playhead_at_the_end(qt_app) -> None:
    start, _commands, events, _process = fake_child()
    song = SongProcess(start=start)
    song.load(np.zeros(44100, dtype=np.float32), 44100)
    announced: list[bool] = []
    song.finished.connect(lambda: announced.append(True))
    events.put((POSITION, 1.0, time.monotonic(), True))
    song._drain()

    events.put((FINISHED,))
    song._drain()

    assert announced == [True]
    assert song.position == pytest.approx(1.0)
    assert not song.is_playing
    song.close()


def test_a_child_that_cannot_start_is_reported(qt_app) -> None:
    def refuse():
        raise OSError("no processes left")

    song = SongProcess(start=refuse)
    problems: list[str] = []
    song.failed.connect(problems.append)

    song.load(np.zeros(10, dtype=np.float32), 44100)

    assert any("no processes left" in problem for problem in problems)
    assert not song.is_loaded


def test_a_child_that_dies_is_reported(qt_app) -> None:
    start, _commands, _events, process = fake_child()
    song = SongProcess(start=start)
    problems: list[str] = []
    song.failed.connect(problems.append)
    song.load(np.zeros(44100, dtype=np.float32), 44100)
    song.play()

    process.exitcode = 1
    song._drain()

    assert any("exit code 1" in problem for problem in problems)
    assert not song.is_playing


# ---------- the child's half ----------


def test_the_child_loads_a_song_and_reports_it(qt_app) -> None:
    child, _commands, events = child_for()
    shared = push_song(child, 0.5)
    try:
        child.tick()

        assert child.player.is_loaded
        assert events.get_nowait() == (LOADED,)
    finally:
        shared.close()
        shared.unlink()


def test_the_child_plays_and_reaches_the_end(qt_app) -> None:
    child, commands, events = child_for()
    shared = push_song(child, 0.05)
    try:
        child.tick()  # loads
        commands.put((PLAY, 0.0))
        child.tick()
        assert child.player.is_playing

        deadline = time.monotonic() + 1.0
        ended = False
        while time.monotonic() < deadline and not ended:
            child.tick()
            while not events.empty():
                ended = events.get_nowait()[0] == FINISHED or ended
            time.sleep(0.01)
        assert ended
    finally:
        shared.close()
        shared.unlink()


def test_the_child_follows_a_seek(qt_app) -> None:
    child, commands, events = child_for()
    shared = push_song(child, 4.0)
    try:
        child.tick()  # loads
        commands.put((SEEK, 3.0))
        child.tick()

        message = last_position(events)
        assert message is not None
        assert abs(message[1] - 3.0) < 0.01
        assert message[3] is False
    finally:
        shared.close()
        shared.unlink()


def test_the_child_stops_publishing_once_the_song_is_unloaded(qt_app) -> None:
    child, commands, events = child_for()
    shared = push_song(child, 1.0)
    try:
        child.tick()
        commands.put((UNLOAD,))
        child.tick()
        while not events.empty():
            events.get_nowait()

        child.tick()

        assert events.empty()
    finally:
        shared.close()
        shared.unlink()
