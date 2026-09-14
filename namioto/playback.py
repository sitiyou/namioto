# SPDX-License-Identifier: AGPL-3.0-only
"""Note playback: pitches turned into a rendered audio buffer.

The mix is plain NumPy so the engine stays testable and the output device can be swapped
(`namioto/ui/audio.py` feeds it to Qt); every note is added to the buffer at its own offset,
so notes keep their overlap instead of stealing each other.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np

SAMPLE_RATE = 44100
A4 = 440.0
A4_PITCH = 69
ATTACK = 0.006
RELEASE = 0.04
DECAY = 2.0
TAIL = 0.5
PARTIALS = ((1, 1.0), (2, 0.4), (3, 0.2), (4, 0.1))


def note_frequency(pitch: int, a4: float = A4) -> float:
    """Equal-tempered frequency of a MIDI pitch, A4 as the reference."""
    return a4 * 2.0 ** ((pitch - A4_PITCH) / 12.0)


def _envelope(count: int, sample_rate: int) -> np.ndarray:
    envelope = np.exp(-DECAY * np.arange(count) / sample_rate)
    attack = min(count, max(1, int(ATTACK * sample_rate)))
    envelope[:attack] *= np.linspace(0.0, 1.0, attack)
    release = min(count, max(1, int(RELEASE * sample_rate)))
    envelope[-release:] *= np.linspace(1.0, 0.0, release)
    return envelope


def _tone(frequency: float, count: int, sample_rate: int) -> np.ndarray:
    time = np.arange(count, dtype=np.float32) / sample_rate
    tone = np.zeros(count, dtype=np.float32)
    for harmonic, amplitude in PARTIALS:
        if frequency * harmonic >= sample_rate / 2:  # partials above Nyquist would alias
            break
        tone += amplitude * np.sin(2 * np.pi * frequency * harmonic * time)
    return tone * _envelope(count, sample_rate)


def render_notes(
    notes: Iterable[tuple[int, float, float]],
    *,
    sample_rate: int = SAMPLE_RATE,
    speed: float = 1.0,
    a4: float = A4,
) -> np.ndarray:
    """Mix `(pitch, start, duration)` notes in seconds into one mono float buffer.

    `speed` scales the whole timeline, so playback rate does not change the pitch.
    """
    notes = list(notes)
    end = max((start + duration for _pitch, start, duration in notes), default=0.0)
    total = int(round((end + TAIL) / speed * sample_rate)) if notes else 0
    mix = np.zeros(total, dtype=np.float32)
    for pitch, start, duration in notes:
        begin = int(round(start / speed * sample_rate))
        count = min(total - begin, int(round(duration / speed * sample_rate)))
        if begin >= total or count <= 0:
            continue
        mix[begin : begin + count] += _tone(note_frequency(pitch, a4), count, sample_rate)

    peak = float(np.max(np.abs(mix))) if total else 0.0
    if peak > 1.0:
        mix /= peak  # dense passages must not clip the output
    return mix
