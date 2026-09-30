# SPDX-License-Identifier: AGPL-3.0-only
"""Song playback in a process of its own.

The sink pulls its samples on the application thread, and that pull needs the GIL, so a busy analysis
thread starves it and the sound runs dry (`ui/song.py` carries the fallback for it). A child process
has an interpreter and a GIL of its own, so the transport stops depending on what the rest of the
editor is doing.

`SongProcess` is the parent's half, and it carries the surface `SongPlayer` has, so the window can
hold it without asking which kind of player it has. The child owns the clock and publishes a position
about as often as the playhead is drawn; the timestamp is `time.monotonic`, which is system wide, so
the parent extrapolates from that anchor instead of paying a round trip on every read.

The song travels once, through shared memory: the parent writes the decoded samples into a segment,
tells the child its name, and frees it when the child reports it has taken its own copy.
"""

from __future__ import annotations

import multiprocessing
import queue
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from multiprocessing import shared_memory

import numpy as np
from PyQt6.QtCore import QCoreApplication, QObject, QTimer, pyqtSignal

POLL_MS = 16  # one position per frame the playhead is drawn on
JOIN_SECONDS = 1.0

# parent -> child
LOAD, UNLOAD, PLAY, PAUSE, STOP, SEEK, SPEED, GAIN, BUFFER, QUIT = (
    "load",
    "unload",
    "play",
    "pause",
    "stop",
    "seek",
    "speed",
    "gain",
    "buffer",
    "quit",
)

# child -> parent
POSITION, FINISHED, FAILED, LOADED = "position", "finished", "failed", "loaded"


def make_song_player():
    """The child's real player: the file streamed to Qt's sink, in the child's own interpreter."""
    from namioto.ui.song import SongPlayer

    return SongPlayer()


class _Child:
    """The child's whole job, with no Qt in it, so it can be driven tick by tick in a test."""

    def __init__(self, commands, events, make_player: Callable[[], object]) -> None:
        self.commands = commands
        self.events = events
        self.player = make_player()
        self.was_playing = False
        self.quitting = False

    def tick(self) -> None:
        self._take_commands()
        self._publish()

    def _take_commands(self) -> None:
        while not self.quitting:
            try:
                message = self.commands.get_nowait()
            except queue.Empty:
                return
            if message[0] == QUIT:
                self.quitting = True
                return
            self._apply(message)

    def _apply(self, message) -> None:
        kind = message[0]
        if kind == LOAD:
            self._load(message[1], message[2], message[3], message[4])
        elif kind == UNLOAD:
            self.player.unload()
        elif kind == PLAY:
            self.player.play(message[1])
        elif kind == PAUSE:
            self.player.pause()
        elif kind == STOP:
            self.player.stop()
        elif kind == SEEK:
            self.player.seek(message[1])
        elif kind == SPEED:
            self.player.speed = message[1]
        elif kind == GAIN:
            self.player.gain = message[1]
        elif kind == BUFFER:
            self.player.buffer_ms = message[1]

    def _load(self, name, shape, dtype, rate: int) -> None:
        """Take the song off the shared segment and report back, so the parent may free it."""
        if name is None:
            self.player.load([], rate)
        else:
            shared = shared_memory.SharedMemory(name=name)
            try:
                view = np.ndarray(tuple(shape), dtype=np.dtype(dtype), buffer=shared.buf)
                self.player.load(np.array(view), rate)
            finally:
                shared.close()
        self.events.put((LOADED,))

    def _publish(self) -> None:
        """The clock the parent follows: a position and the monotonic instant it was read at."""
        if not self.player.is_loaded:
            return
        position, playing = self.player.position, self.player.is_playing
        if self.was_playing and not playing and position >= self.player.duration - 1e-3:
            self.events.put((FINISHED,))
        self.was_playing = playing
        self.events.put((POSITION, position, time.monotonic(), playing))


def song_process_main(commands, events, make_player: Callable[[], object] = make_song_player) -> None:
    """The child's whole job, on its own event loop: the sink needs one, and nothing else runs here.

    It returns when `quit` arrives; the queues are the only contact with the parent.
    """
    app = QCoreApplication.instance() or QCoreApplication([])
    child = _Child(commands, events, make_player)

    def pump_once() -> None:
        child.tick()
        if child.quitting:
            app.quit()

    pump = QTimer()
    pump.setInterval(POLL_MS)
    pump.timeout.connect(pump_once)
    pump.start()
    app.exec()


def spawn_song_process():
    """Start the child and the two queues it talks over; the one seam the tests replace."""
    context = multiprocessing.get_context("spawn")
    commands = context.Queue()
    events = context.Queue()
    process = context.Process(target=song_process_main, args=(commands, events), daemon=True)
    process.start()
    return process, commands, events


class SongProcess(QObject):
    """The window's player, backed by a child: commands down, an anchored clock up."""

    finished = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, parent=None, start: Callable[[], tuple] | None = None):
        super().__init__(parent)
        self._spawn = start or spawn_song_process
        self._process = None
        self._commands = None
        self._events = None
        self._pending: deque = deque()  # segments the child has not reported taking a copy of yet
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._drain)
        self._loaded = False
        self._playing = False
        self._duration = 0.0
        self._speed = 1.0
        self._gain = 1.0
        self._buffer_ms = 80
        self._target = 0.0  # where a play or a seek aimed
        self._target_time = 0.0
        self._anchor_seconds = 0.0  # the child's last position and the instant it was read at
        self._anchor_time = 0.0
        self._anchored = False

    # ---------- the surface `SongPlayer` has ----------

    def load(self, samples, sample_rate: int) -> None:
        rate = int(sample_rate)
        duration = len(samples) / rate if rate else 0.0
        self._playing = False
        self._anchored = False
        self._aim(0.0)
        if not self._ensure():
            self._duration = 0.0
            self._loaded = False
            return
        array = np.ascontiguousarray(samples, dtype=np.float32)
        if array.size:
            shared = shared_memory.SharedMemory(create=True, size=array.nbytes)
            np.ndarray(array.shape, dtype=array.dtype, buffer=shared.buf)[:] = array
            self._pending.append(shared)
            self._commands.put((LOAD, shared.name, array.shape, str(array.dtype), rate))
        else:
            self._commands.put((LOAD, None, (), "float32", rate))
        self._duration = duration
        self._loaded = duration > 0.0
        # a child that just started defaults to 1.0/1.0/80, so hand it what the window is on
        self._send((GAIN, self._gain))
        self._send((SPEED, self._speed))
        self._send((BUFFER, self._buffer_ms))

    def unload(self) -> None:
        self._loaded = False
        self._playing = False
        self._anchored = False
        self._duration = 0.0
        self._aim(0.0)
        self._send((UNLOAD,))

    def play(self, seconds: float = 0.0) -> None:
        self._aim(seconds)
        self._playing = self._loaded
        self._anchored = False
        self._send((PLAY, self._target))

    def pause(self) -> None:
        self._reanchor()
        self._playing = False
        self._send((PAUSE,))

    def stop(self) -> None:
        self._playing = False
        self._anchored = False
        self._aim(0.0)
        self._send((STOP,))

    def seek(self, seconds: float) -> None:
        self._aim(seconds)
        self._anchored = False
        self._send((SEEK, self._target))

    @property
    def position(self) -> float:
        if self._anchored:
            seconds, since = self._anchor_seconds, self._anchor_time
        else:
            seconds, since = self._target, self._target_time
        if not self._playing:
            return seconds
        return min(self._duration, seconds + (time.monotonic() - since) * self._speed)

    @property
    def duration(self) -> float:
        return self._duration

    @property
    def is_playing(self) -> bool:
        return self._playing

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def speed(self) -> float:
        return self._speed

    @speed.setter
    def speed(self, value: float) -> None:
        value = max(0.01, float(value))
        if value == self._speed:
            return
        self._reanchor()
        self._speed = value
        self._send((SPEED, value))

    @property
    def gain(self) -> float:
        return self._gain

    @gain.setter
    def gain(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        if value == self._gain:
            return
        self._gain = value
        self._send((GAIN, value))

    @property
    def buffer_ms(self) -> int:
        return self._buffer_ms

    @buffer_ms.setter
    def buffer_ms(self, value: int) -> None:
        self._buffer_ms = max(10, int(value))
        self._send((BUFFER, self._buffer_ms))

    def close(self) -> None:
        """Ask the child to leave, then take it down if it does not."""
        self._timer.stop()
        process, self._process = self._process, None
        if process is not None and process.is_alive():
            self._commands.put((QUIT,))
            process.join(JOIN_SECONDS)
            if process.is_alive():
                process.terminate()
                process.join(JOIN_SECONDS)
        self._commands = None
        self._events = None
        self._release_all()
        self._playing = False
        self._loaded = False

    # ---------- the pipe ----------

    def _aim(self, seconds: float) -> None:
        """Where the playhead reads from until the child's next anchor arrives."""
        self._target = max(0.0, min(float(seconds), self._duration))
        self._target_time = time.monotonic()

    def _reanchor(self) -> None:
        """Hold the clock where it is, so a pause or a new rate does not move the playhead."""
        if self._playing or self._anchored:
            self._anchor_seconds = self.position
            self._anchor_time = time.monotonic()
            self._anchored = True

    def _send(self, message) -> None:
        """Only a song that is on the way to the child takes a command; nothing here starts it."""
        if self._process is not None:
            self._commands.put(message)

    def _ensure(self) -> bool:
        if self._process is not None:
            return True
        try:
            self._process, self._commands, self._events = self._spawn()
        except Exception as error:  # a child that cannot start must not take the window down
            self.failed.emit(f"{type(error).__name__}: {error}")
            return False
        self._timer.start()
        return True

    def _drain(self) -> None:
        self._take()
        process = self._process
        if process is None or process.is_alive():
            return
        self._take()  # the last messages can land just after the child exits
        self._timer.stop()
        self._process = None
        self._commands = None
        self._events = None
        self._release_all()
        self._playing = False
        self.failed.emit(f"the player stopped unexpectedly (exit code {process.exitcode})")

    def _take(self) -> None:
        while self._events is not None:
            try:
                message = self._events.get_nowait()
            except queue.Empty:
                return
            self._follow(message)

    def _follow(self, message) -> None:
        kind = message[0]
        if kind == POSITION:
            self._anchor_seconds, self._anchor_time, self._playing = message[1], message[2], message[3]
            self._anchored = True
        elif kind == LOADED:
            self._release_one()
        elif kind == FINISHED:
            self._playing = False
            self._anchored = False
            self._aim(self._duration)
            self.finished.emit()
        elif kind == FAILED:
            self.failed.emit(message[1])

    def _release_one(self) -> None:
        if self._pending:
            self._free(self._pending.popleft())

    def _release_all(self) -> None:
        while self._pending:
            self._free(self._pending.popleft())

    @staticmethod
    def _free(shared) -> None:
        shared.close()
        with suppress(FileNotFoundError):
            shared.unlink()
