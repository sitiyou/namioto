# SPDX-License-Identifier: AGPL-3.0-only
"""Song playback: the decoded audio file streamed to Qt's audio sink.

The buffer stays mono float32 at the file's own sample rate. Playing it at another speed means
stretching it with a phase vocoder (`TimeStretcher`), because a fractional step over the frames would
take the pitch up and down with the tempo. The vocoder runs as the sink asks for samples, so a speed
change resets it in place instead of rerendering the whole song, and one second of sound is `speed`
seconds of the song, which keeps the playhead in song seconds.
"""

from __future__ import annotations

from pathlib import Path

import librosa
import numpy as np
from PyQt6.QtMultimedia import QtAudio

from namioto.ui.audio import BUFFER_MS, SinkPlayer

N_FFT = 2048
HOP = 512
BLOCK_FRAMES = 256  # input frames one STFT block holds: a few seconds between block recomputations
PASSTHROUGH_TOLERANCE = 1e-3
WINDOW = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)
WINDOW_SQUARED = WINDOW * WINDOW


def load_song(path: str | Path) -> tuple[np.ndarray, int]:
    """Decode a file into one mono buffer at its own sample rate, ready for the sink."""
    samples, sample_rate = librosa.load(path, sr=None, mono=True)
    return np.ascontiguousarray(samples, dtype=np.float32), int(sample_rate)


class TimeStretcher:
    """A phase vocoder that produces the song in the order the sink asks for it.

    The output frame `k` reads the input frames around `base + k * speed` and advances its phase by
    the difference it finds there, so the rhythm moves and the frequencies do not. Only the frames the
    last `read` needs are kept: one STFT block, one phase accumulator and one overlap-add window, not
    the `1 / speed` times as long spectrum a whole-song rerender would materialize.
    """

    def __init__(self) -> None:
        self.samples = np.zeros(0, dtype=np.float32)
        self.sample_rate = 0
        self._pad = self.samples
        self._frames_total = 1
        self._cache = np.zeros((0, N_FFT // 2 + 1), dtype=np.complex128)
        self._cache_start = 0
        self._speed = 1.0
        self._base = 0.0
        self._passthrough = False
        self._cursor = 0
        self._total = 0
        self._done = 0
        self._frame = 0
        self._phase: np.ndarray | None = None
        self._mix = np.zeros(0, dtype=np.float64)
        self._window_sum = np.zeros(0, dtype=np.float64)
        self._origin = 0
        self._emit_at = 0

    def load(self, samples: np.ndarray, sample_rate: int) -> None:
        self.samples = np.ascontiguousarray(samples, dtype=np.float32)
        self.sample_rate = int(sample_rate)
        self._pad = np.concatenate(
            [
                np.zeros(N_FFT // 2, dtype=np.float32),
                self.samples,
                np.zeros(N_FFT // 2 + (BLOCK_FRAMES + 2) * HOP, dtype=np.float32),
            ]
        )
        self._frames_total = 1 + len(self.samples) // HOP

    def start(self, seconds: float, speed: float) -> None:
        """Aim the vocoder at a song position and a rate, from the beginning of `seconds`."""
        speed = max(0.01, float(speed))
        seconds = max(0.0, float(seconds))
        self._done = 0
        self._passthrough = abs(speed - 1.0) < PASSTHROUGH_TOLERANCE
        if self._passthrough:
            self._speed = speed
            self._cursor = min(int(round(seconds * self.sample_rate)), len(self.samples))
            self._total = max(0, len(self.samples) - self._cursor)
            return
        self._begin_vocoder(speed, seconds * self.sample_rate / HOP)

    def retune(self, speed: float) -> None:
        """Change the rate where the reading sits, so the stream carries on without a gap.

        The next frame is pinned to the input position it would have had at the old rate, and the
        phase and overlap-add state are left alone: only how fast the frames advance changes.
        """
        speed = max(0.01, float(speed))
        if speed == self._speed:
            return
        if self._passthrough:
            if abs(speed - 1.0) < PASSTHROUGH_TOLERANCE:
                self._speed = speed
                return
            self._begin_vocoder(speed, self._cursor / HOP)
            return
        self._base += self._frame * (self._speed - speed)
        self._speed = speed
        self._refresh_total()

    def _begin_vocoder(self, speed: float, base: float) -> None:
        self._speed = speed
        self._base = base
        self._passthrough = False
        self._frame = 0
        self._phase = None
        self._cache = np.zeros((0, N_FFT // 2 + 1), dtype=np.complex128)
        self._origin = 0
        self._mix = np.zeros(0, dtype=np.float64)
        self._window_sum = np.zeros(0, dtype=np.float64)
        self._emit_at = N_FFT // 2
        self._done = 0
        self._refresh_total()

    def _refresh_total(self) -> None:
        """Output samples the song still holds, from the next frame on; exact for the frame grid."""
        next_input = self._base + self._frame * self._speed
        future = max(0, int((len(self.samples) / HOP - next_input) * HOP / self._speed))
        pending = max(0, self._frame * HOP - self._emit_at)
        self._total = self._done + pending + future

    def remaining(self) -> int:
        return self._total - self._done

    def read(self, frames: int) -> np.ndarray:
        """The next `frames` output samples, or fewer when the song ends first."""
        frames = min(int(frames), self.remaining())
        if frames <= 0:
            return np.zeros(0, dtype=np.float32)
        if self._passthrough:
            out = self.samples[self._cursor : self._cursor + frames]
            self._cursor += len(out)
            self._done += len(out)
            return out
        out = np.empty(frames, dtype=np.float32)
        got = 0
        while got < frames:
            self._add_frame(self._frame)
            ready = self._frame * HOP
            if ready > self._emit_at:
                take = min(frames - got, ready - self._emit_at)
                index = self._emit_at - self._origin
                gains = self._window_sum[index : index + take]
                out[got : got + take] = self._mix[index : index + take] / np.where(gains < 1e-9, 1.0, gains)
                got += take
                self._emit_at += take
            self._frame += 1
        self._trim()
        self._done += got
        return out

    def _trim(self) -> None:
        """Drop the overlap-add samples already handed over: nothing writes before `_emit_at` again."""
        trim = self._emit_at - self._origin
        self._mix = self._mix[trim:]
        self._window_sum = self._window_sum[trim:]
        self._origin = self._emit_at

    def _add_frame(self, frame: int) -> None:
        """One vocoder frame: interpolate the spectrum, carry the phase, overlap-add the wave."""
        position = self._base + frame * self._speed
        index = int(np.floor(position))
        frac = position - index
        index = max(0, min(index, self._frames_total - 1))
        following = min(index + 1, self._frames_total - 1)
        self._ensure(following)
        first = self._cache[index - self._cache_start]
        second = self._cache[following - self._cache_start]
        magnitude = (1.0 - frac) * np.abs(first) + frac * np.abs(second)
        phase = self._phase
        if phase is None:
            phase = np.angle(first).copy()
        wave = np.fft.irfft(magnitude * np.exp(1j * phase), n=N_FFT) * WINDOW
        self._phase = phase + (np.angle(second) - np.angle(first))
        self._overlap(frame * HOP, wave, to_mix=True)
        self._overlap(frame * HOP, WINDOW_SQUARED, to_mix=False)

    def _ensure(self, index: int) -> None:
        if self._cache_start <= index < self._cache_start + len(self._cache):
            return
        self._cache_start = max(0, index - 4)
        starts = (self._cache_start + np.arange(BLOCK_FRAMES)) * HOP
        segments = self._pad[starts[:, None] + np.arange(N_FFT)[None, :]]
        self._cache = np.fft.rfft(segments * WINDOW[None, :], axis=1)

    def _overlap(self, offset: int, values: np.ndarray, *, to_mix: bool) -> None:
        index = offset - self._origin
        end = index + len(values)
        if end > len(self._mix):
            growth = end - len(self._mix)
            self._mix = np.concatenate([self._mix, np.zeros(growth, dtype=np.float64)])
            self._window_sum = np.concatenate([self._window_sum, np.zeros(growth, dtype=np.float64)])
        target = self._mix if to_mix else self._window_sum
        target[index:end] += values


class SongPlayer(SinkPlayer):
    """The loaded audio file, carrying the transport surface the window drives."""

    def __init__(self, parent=None, buffer_ms: int = BUFFER_MS):
        super().__init__(parent, buffer_ms=buffer_ms, sample_rate=0)
        self.samples = np.zeros(0, dtype=np.float32)  # the song as it was decoded
        self._stretcher = TimeStretcher()
        self._position_base = 0.0  # song position when the current rate was set
        self._position_us = 0  # the sink's clock reading at that moment

    @property
    def speed(self) -> float:
        return self._speed

    @speed.setter
    def speed(self, value: float) -> None:
        """A new playback rate: the vocoder retunes where it is, so the sound never stops."""
        value = max(0.01, float(value))
        if value == self._speed:
            return
        if self.is_loaded:
            if self._sink is not None and self.is_playing:
                self._mark_position()
                self._stretcher.retune(value)
            else:
                self._stretcher.start(self._start, value)
        self._speed = value

    def _mark_position(self) -> None:
        """Fold the time played at the current rate into the base the playhead is measured from."""
        used = self._sink.processedUSecs()
        self._position_base += (used - self._position_us) / 1e6 * self._speed
        self._position_us = used

    def load(self, samples: np.ndarray, sample_rate: int) -> None:
        self.stop()
        self.samples = np.ascontiguousarray(samples, dtype=np.float32)
        self.sample_rate = int(sample_rate)
        self._stretcher.load(self.samples, self.sample_rate)
        self._stretcher.start(0.0, self._speed)

    def unload(self) -> None:
        """Drop the song: a project without audio, or without the audio it names, has nothing to play."""
        self.load(np.zeros(0, dtype=np.float32), 0)

    @property
    def remaining(self) -> int:
        """Output samples the vocoder still has for the position it was aimed at."""
        return self._stretcher.remaining() if self.is_loaded else 0

    def read(self, frames: int) -> np.ndarray:
        return self._stretcher.read(frames)

    @property
    def is_loaded(self) -> bool:
        return self.samples.size > 0 and self.sample_rate > 0

    @property
    def duration(self) -> float:
        """The song's own length, whatever rate it is played at."""
        return len(self.samples) / self.sample_rate if self.is_loaded else 0.0

    @property
    def position(self) -> float:
        if self._sink is None:
            return self._start
        elapsed = (self._sink.processedUSecs() - self._position_us) / 1e6
        return min(self.duration, self._position_base + elapsed * self._speed)

    def play(self, seconds: float = 0.0) -> None:
        self._close()
        if not self.is_loaded:
            return
        self._start = max(0.0, min(seconds, self.duration))
        self._position_base = self._start
        self._position_us = 0
        self._stretcher.start(self._start, self._speed)
        self._open()

    def seek(self, seconds: float) -> None:
        """Move the playhead, carrying on from there when the song was playing."""
        if self.is_playing:
            self.play(seconds)
        else:
            self._start = max(0.0, min(seconds, self.duration))

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
