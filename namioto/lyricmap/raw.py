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
