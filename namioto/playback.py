# SPDX-License-Identifier: AGPL-3.0-only
"""Note playback: pitches turned into a rendered audio buffer.

The mix is plain NumPy so the engine stays testable and the output device can be swapped
(`namioto/ui/audio.py` feeds it to Qt); every note is added to the buffer at its own offset,
so notes keep their overlap instead of stealing each other.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 44100
A4 = 440.0
A4_PITCH = 69
ATTACK = 0.006
RELEASE = 0.04
DECAY = 2.0
TAIL = 0.5


@dataclass(frozen=True)
class Voice:
    """A synthesiser patch: harmonic amplitudes and envelope times.

    The built-in synth cannot imitate 128 GM instruments; GM programs map onto a handful of these
    families instead, so the channels sound different from one another rather than realistic.
    """

    partials: tuple[tuple[int, float], ...]
    attack: float = ATTACK
    decay: float = DECAY
    release: float = RELEASE


PIANO = Voice(((1, 1.0), (2, 0.4), (3, 0.2), (4, 0.1)))
ORGAN = Voice(((1, 1.0), (2, 0.5), (3, 0.3), (4, 0.2)), attack=0.05, decay=0.08)
PLUCK = Voice(((1, 1.0), (2, 0.5), (3, 0.15)), decay=5.0)
STRING = Voice(((1, 1.0), (2, 0.35), (3, 0.2), (4, 0.15), (5, 0.1)), attack=0.08, decay=0.3)
WIND = Voice(((1, 1.0), (2, 0.25), (3, 0.1)), attack=0.04, decay=0.5)
BASS = Voice(((1, 1.0), (2, 0.5), (3, 0.25)), decay=2.5)

# one voice per GM family (the program number divided by 8)
FAMILY_VOICES = (
    PIANO,  # 0-7 piano
    PIANO,  # 8-15 chromatic percussion
    ORGAN,  # 16-23 organ
    PLUCK,  # 24-31 guitar
    BASS,  # 32-39 bass
    STRING,  # 40-47 strings
    STRING,  # 48-55 ensemble
    WIND,  # 56-63 brass
    WIND,  # 64-71 reed
    WIND,  # 72-79 pipe
    WIND,  # 80-87 synth lead
    STRING,  # 88-95 synth pad
    ORGAN,  # 96-103 synth effects
    PLUCK,  # 104-111 ethnic
    PLUCK,  # 112-119 percussive
    ORGAN,  # 120-127 sound effects
)


def voice_for_program(program: int) -> Voice:
    return FAMILY_VOICES[min(len(FAMILY_VOICES) - 1, max(0, int(program)) // 8)]


def note_frequency(pitch: int, a4: float = A4) -> float:
    """Equal-tempered frequency of a MIDI pitch, A4 as the reference."""
    return a4 * 2.0 ** ((pitch - A4_PITCH) / 12.0)


def _envelope(count: int, sample_rate: int, voice: Voice) -> np.ndarray:
    envelope = np.exp(-voice.decay * np.arange(count) / sample_rate)
    attack = min(count, max(1, int(voice.attack * sample_rate)))
    envelope[:attack] *= np.linspace(0.0, 1.0, attack)
    release = min(count, max(1, int(voice.release * sample_rate)))
    envelope[-release:] *= np.linspace(1.0, 0.0, release)
    return envelope


def _tone(frequency: float, count: int, sample_rate: int, voice: Voice) -> np.ndarray:
    time = np.arange(count, dtype=np.float32) / sample_rate
    tone = np.zeros(count, dtype=np.float32)
    for harmonic, amplitude in voice.partials:
        if frequency * harmonic >= sample_rate / 2:  # partials above Nyquist would alias
            break
        tone += amplitude * np.sin(2 * np.pi * frequency * harmonic * time)
    return tone * _envelope(count, sample_rate, voice)


def render_notes(
    notes: Iterable[tuple[int, float, float]],
    *,
    sample_rate: int = SAMPLE_RATE,
    speed: float = 1.0,
    a4: float = A4,
    channels: Iterable[tuple[int, int, int]] = (),
) -> np.ndarray:
    """Mix `(pitch, start, duration)` notes in seconds into one mono float buffer.

    A note may carry a fourth element, the MIDI channel it plays on; `channels` then gives that
    channel its `(channel, program, volume)`, which picks the voice and scales the note. `speed`
    scales the whole timeline, so playback rate does not change the pitch.
    """
    notes = list(notes)
    programs = {channel: program for channel, program, _volume in channels}
    gains = {channel: min(1.27, max(0.0, volume / 100)) for channel, _program, volume in channels}
    end = max((note[1] + note[2] for note in notes), default=0.0)
    total = int(round((end + TAIL) / speed * sample_rate)) if notes else 0
    mix = np.zeros(total, dtype=np.float32)
    for note in notes:
        pitch, start, duration = note[0], note[1], note[2]
        channel = note[3] if len(note) > 3 else 0
        begin = int(round(start / speed * sample_rate))
        count = min(total - begin, int(round(duration / speed * sample_rate)))
        if begin >= total or count <= 0:
            continue
        voice = voice_for_program(programs.get(channel, 0))
        mix[begin : begin + count] += _tone(note_frequency(pitch, a4), count, sample_rate, voice) * gains.get(
            channel, 1.0
        )

    peak = float(np.max(np.abs(mix))) if total else 0.0
    if peak > 1.0:
        mix /= peak  # dense passages must not clip the output
    return mix
