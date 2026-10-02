# SPDX-License-Identifier: AGPL-3.0-only
"""How much a mapping trusts each of its operations, and the named thresholds that decide it.

Confidence is read off four things a whole operation either has or has not: how far its predicted
boundaries sit from the raw evidence (`fit_error`, a line end counted only when the note ends short
of the sung line), how much cheaper it is than the best alternative
mapping from the same state (`margin`), how sure the aligner was of its Sounds and whether it doubted
the line (`quality`), and whether a match stretches over an unusual rest (`rest`). The thresholds are
named constants so a real corpus can move them, and nothing here changes the mapping itself. Qt-free.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from namioto.karaoke.operations import Match, Merge, Operation
from namioto.karaoke.sounds import SoundLine
from namioto.lyricmap.notes import Note
from namioto.lyricmap.raw import Raw, chain
from namioto.lyricmap.solver import diagnose

# a raw boundary more than a fifth of a second off is the operation's own trouble
FIT_ERROR_SECONDS = 0.2
# the alternative must cost this much more per second of the notes it takes, or the choice is a coin toss
MARGIN_PER_SECOND = 0.05
# the least aligner score one Sound of the operation may carry
QUALITY = 0.5
# a rest inside a match longer than this many of its own note lengths is abnormal
REST_RATIO = 3.0

_CODE = {"Match": "low_confidence_match", "Merge": "low_confidence_merge", "Drop": "low_confidence_drop"}


@dataclass(frozen=True)
class Reading:
    """What one operation's confidence was read from, and whether any signal failed."""

    fit_error: float
    margin: float
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
    rows = chain(raw)
    found = diagnose(lines, raw, notes, operations)
    note_list = list(notes)
    at = {note.id: index for index, note in enumerate(note_list)}
    readings: list[Reading] = []
    note_at = 0
    for operation, (fit_error, margin) in zip(operations, found, strict=True):
        span = _span(operation, note_list, note_at, at)
        normalized = margin / span if span > 1e-9 else margin
        quality = _quality(operation, rows, flagged)
        rest = _rest(operation, note_list, at)
        low = fit_error > FIT_ERROR_SECONDS or normalized < MARGIN_PER_SECOND or quality < QUALITY or rest > REST_RATIO
        readings.append(
            Reading(fit_error, normalized, quality, rest, low, _CODE[type(operation).__name__] if low else "")
        )
        note_at += _notes_taken(operation)
    return readings


def _notes_taken(operation: Operation) -> int:
    if isinstance(operation, Match):
        return len(operation.notes)
    if isinstance(operation, Merge):
        return 1
    return 0


def _span(operation: Operation, notes: Sequence[Note], note_at: int, at: dict) -> float:
    if isinstance(operation, Match):
        return sum(notes[at[identifier]].end - notes[at[identifier]].start for identifier in operation.notes)
    if isinstance(operation, Merge):
        index = at[operation.note]
        return notes[index].end - notes[index].start
    if note_at < len(notes):
        return notes[note_at].end - notes[note_at].start
    return notes[-1].end - notes[-1].start if notes else 1.0


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


__all__ = ["FIT_ERROR_SECONDS", "MARGIN_PER_SECOND", "QUALITY", "REST_RATIO", "Reading", "read"]
