# SPDX-License-Identifier: AGPL-3.0-only
"""The aligner's raw evidence for each Sound: a span, and the score it was aligned with.

`Raw` is one `(start, end, score?)`; `chain` turns a line into the onset chain the mapping reads -
each Sound ending where the next begins, the aligner's own end kept only for the line's last Sound.
`validate` refuses anything the edit mapping cannot work from, so the solver never sees a gap or a
backwards step. Qt-free.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import NamedTuple

from namioto.karaoke.sounds import SoundLine
from namioto.lyricmap.problems import MappingError


class Raw(NamedTuple):
    """One Sound's raw evidence: where the aligner put it, and how sure it was."""

    start: float
    end: float
    score: float | None = None


def chain(raw: Sequence[Sequence[Raw]]) -> list[list[Raw]]:
    """Each line as an onset chain: a Sound ends where the next begins, the last end kept."""
    rows: list[list[Raw]] = []
    for line in raw:
        spans = [Raw(span.start, span.end, span.score) for span in line]
        for index in range(len(spans) - 1):
            if spans[index].end is not None and spans[index + 1].start is not None:
                spans[index] = Raw(spans[index].start, spans[index + 1].start, spans[index].score)
        rows.append(spans)
    return rows


def snap_to_beats(
    times: Sequence[Sequence[tuple[float | None, float | None]]], bpm: float, division: float = 1.0, offset: float = 0.0
) -> list[list[tuple[float | None, float | None]]]:
    """Every Sound's start and end rounded to the grid of `division` beats at `bpm` off `offset`.

    The grid is the one that is drawn: `offset` is the editor's slid grid, 0 the absolute beats. A
    Sound rounds on its own, so one whose two ends land in the same cell comes back with no length -
    a Sound nothing is sung on - rather than pushing the rest of its line one cell per collision off
    the beat. A line with an unaligned Sound is left alone, since its boundaries say nothing yet.
    """
    step = 60.0 / max(bpm, 1.0) * division
    rows = []
    for row in times:
        if any(start is None or end is None for start, end in row):
            rows.append([tuple(span) for span in row])
            continue
        snapped = []
        for start, end in row:
            start = offset + round((start - offset) / step) * step
            end = offset + round((end - offset) / step) * step
            snapped.append((start, max(start, end)))
        rows.append(snapped)
    return rows


def validate(lines: Sequence[SoundLine], raw: Sequence[Sequence[Raw]]) -> None:
    """Refuse a mapping whose evidence is missing, reversed or out of order.

    Every Sound must have a finite start and end, no Sound may end before it starts, and the starts
    must not step backwards across the whole song. A failure is `incomplete_alignment`.
    """
    previous = float("-inf")
    for line, spans in zip(lines, raw, strict=True):
        if len(spans) != len(line.sounds):
            raise MappingError(
                "incomplete_alignment", f"'{line.text}' has {len(line.sounds)} sounds but {len(spans)} times"
            )
        for span in spans:
            if span.start is None or span.end is None or not math.isfinite(span.start) or not math.isfinite(span.end):
                raise MappingError("incomplete_alignment", "a sound has no raw time")
            if span.end < span.start or span.start < previous - 1e-9:
                raise MappingError("incomplete_alignment", "the raw times are not in order")
            previous = span.start
