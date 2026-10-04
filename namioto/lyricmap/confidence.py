# SPDX-License-Identifier: AGPL-3.0-only
"""How much a mapping trusts each of its operations, and the named thresholds that decide it.

Confidence is read off three things: how far an operation's predicted boundaries sit from the raw
evidence (`fit_error`, using its pattern weight and counting a line end only when the note ends
short of the sung line), how sure the aligner was of its Sounds and whether it doubted the line
(`quality`), and whether a match stretches over an unusual rest (`rest`). The thresholds are
named constants so a real corpus can move them, and nothing here changes the mapping itself. Qt-free.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from namioto.karaoke.operations import Match, Operation
from namioto.karaoke.sounds import SoundLine
from namioto.lyricmap.notes import Note
from namioto.lyricmap.raw import Raw
from namioto.lyricmap.solver import fit_errors

FIT_ERROR_SECONDS = 0.2
# the least aligner score one Sound of the operation may carry
QUALITY = 0.5
# a rest inside a match longer than this many of its own note lengths is abnormal
REST_RATIO = 3.0

_CODE = {"Match": "low_confidence_match", "Merge": "low_confidence_merge", "Drop": "low_confidence_drop"}


@dataclass(frozen=True)
class Reading:
    """What one operation's confidence was read from, and whether any signal failed."""

    fit_error: float
    quality: float
    rest: float
    low: bool
    code: str = ""


def read(
    lines: Sequence[SoundLine],
    raw: Sequence[Sequence[Raw]],
    notes: Sequence[Note],
    operations: Sequence[Operation],
    flagged: Sequence[bool] = (),
) -> list[Reading]:
    """One reading per operation, in order, judged against the named thresholds."""
    found = fit_errors(lines, raw, notes, operations)
    note_list = list(notes)
    at = {note.id: index for index, note in enumerate(note_list)}
    readings: list[Reading] = []
    for operation, fit_error in zip(operations, found, strict=True):
        quality = _quality(operation, raw, flagged)
        rest = _rest(operation, note_list, at)
        low = fit_error > FIT_ERROR_SECONDS or quality < QUALITY or rest > REST_RATIO
        readings.append(Reading(fit_error, quality, rest, low, _CODE[type(operation).__name__] if low else ""))
    return readings


def _quality(operation: Operation, rows: Sequence[Sequence[Raw]], flagged: Sequence[bool]) -> float:
    if any(ref.line < len(flagged) and flagged[ref.line] for ref in operation.sounds):
        return 0.0
    scores = [rows[ref.line][ref.index].score for ref in operation.sounds]
    known = [score for score in scores if score is not None]
    if not known:
        # no score at all is an unknown alignment, not a doubted one: it neither passes nor fails
        return QUALITY
    value = min(known)
    return value * 0.5 if len(known) < len(scores) else value


def _rest(operation: Operation, notes: Sequence[Note], at: dict) -> float:
    if not isinstance(operation, Match) or len(operation.notes) < 2:
        return 0.0
    indices = [at[identifier] for identifier in operation.notes]
    lengths = [notes[index].end - notes[index].start for index in indices]
    typical = sorted(lengths)[len(lengths) // 2] or 1.0
    gaps = [notes[indices[step + 1]].start - notes[indices[step]].end for step in range(len(indices) - 1)]
    return max(gaps, default=0.0) / typical


__all__ = ["FIT_ERROR_SECONDS", "QUALITY", "REST_RATIO", "Reading", "read"]
