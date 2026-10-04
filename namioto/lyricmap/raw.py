# SPDX-License-Identifier: AGPL-3.0-only
"""The evidence for each Sound: an editable onset, a reference duration and an optional score.

The aligner's duration is captured before quantization and never changes when an onset moves.
Only a line's last Sound supplies an end reference, computed as onset plus duration; it is a soft
mapping comparison, never an editing boundary. Qt-free.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import NamedTuple

from namioto.karaoke.sounds import SoundLine
from namioto.lyricmap.problems import MappingError


class Raw(NamedTuple):
    """One Sound's editable onset, reference duration and alignment score."""

    onset: float
    raw_length: float
    score: float | None = None

    @property
    def reference_end(self) -> float:
        return self.onset + self.raw_length


def from_spans(
    times: Sequence[Sequence[tuple[float | None, float | None]]],
) -> list[list[tuple[float | None, float | None]]]:
    """Capture onset and duration from the alignment's spans before quantization."""
    return [
        [(start, end - start if start is not None and end is not None else None) for start, end in row] for row in times
    ]


def snap_to_beats(
    raw: Sequence[Sequence[tuple[float | None, float | None]]], bpm: float, division: float = 1.0, offset: float = 0.0
) -> list[list[tuple[float | None, float | None]]]:
    """Quantize onsets onto the drawn grid, preserving every reference duration.

    A line with missing evidence is left alone. Coincident onsets are allowed; quantization never
    pushes another Sound away to make room.
    """
    step = 60.0 / max(bpm, 1.0) * division
    rows = []
    for row in raw:
        if any(onset is None or length is None for onset, length in row):
            rows.append([tuple(sound) for sound in row])
            continue
        rows.append([(max(0.0, offset + round((onset - offset) / step) * step), length) for onset, length in row])
    return rows


def validate(lines: Sequence[SoundLine], raw: Sequence[Sequence[Raw]]) -> None:
    """Require finite, nonnegative evidence and globally nondecreasing onsets."""
    if len(lines) != len(raw):
        raise MappingError("incomplete_alignment", "the lyrics and raw evidence have different line counts")
    previous = float("-inf")
    for line, sounds in zip(lines, raw, strict=True):
        if len(sounds) != len(line.sounds):
            raise MappingError(
                "incomplete_alignment", f"'{line.text}' has {len(line.sounds)} sounds but {len(sounds)} times"
            )
        for sound in sounds:
            if any(value is None or not math.isfinite(value) for value in (sound.onset, sound.raw_length)):
                raise MappingError("incomplete_alignment", "a sound has no raw evidence")
            if sound.onset < 0 or sound.raw_length < 0 or not math.isfinite(sound.reference_end):
                raise MappingError("incomplete_alignment", "the raw evidence is invalid")
            if sound.onset < previous - 1e-9:
                raise MappingError("incomplete_alignment", "the raw onsets are not in order")
            previous = sound.onset
