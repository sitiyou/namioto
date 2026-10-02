# SPDX-License-Identifier: AGPL-3.0-only
"""Laying a `.krc`'s Sounds onto the target channel's notes: the result the strip draws and the
thread that maps them.

The mapping is the spec's one: `resolve` picks the single voice a conflicted channel previews,
`solve` lays every Sound on it as a `match`, `merge` or `drop`, and the strip's spans, doubt, grey
and shared-note tables are read off those operations rather than kept as facts of their own.
Read-only mode takes the `.krc`'s own `.N` and groups instead, running no DP. `LyricMapper` runs the
mapping off the GUI thread, since a long `.krc` over a long roll takes a moment.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PyQt6.QtCore import QThread, pyqtSignal

from namioto.karaoke.operations import Match, Merge, Operation
from namioto.lyricmap import confidence, faithful
from namioto.lyricmap.notes import TimedNote, resolve
from namioto.lyricmap.problems import MappingError
from namioto.lyricmap.raw import Raw
from namioto.lyricmap.solver import solve
from namioto.lyricmap.spans import sound_spans

Span = tuple[float | None, float | None]


@dataclass(frozen=True)
class LyricResult:
    """One mapping: what the strip draws, and the operations and readings it was read from."""

    lines: tuple = ()
    spans: tuple = ()
    red: tuple = ()
    raw: tuple = ()
    zero: tuple = ()
    group: tuple = ()
    operations: tuple = ()
    filtered: tuple = ()
    readings: tuple = ()
    error: str = ""


def map_lyrics(
    text: str,
    lines: Sequence,
    raw: Sequence[Sequence[tuple]],
    scores: Sequence[Sequence[float | None]] = (),
    flagged: Sequence[bool] = (),
    notes: Sequence[tuple] = (),
    mode: str = "edit",
    anchors: Sequence[Operation] = (),
) -> LyricResult:
    """The mapping for the mode in force: the aligner's times in edit, the `.krc`'s own in read.

    `raw` is one `(start, end)` per Sound, per line; `scores` the aligner's confidence beside it;
    `notes` the target channel as `(start, end, pitch, id)` tuples. A mapping that cannot be made -
    missing alignment, no notes, a broken anchor - comes back with `.error` set and the raw times as
    the drawn spans, so the strip still shows what it has.
    """
    lines = list(lines)
    raw = [[(span[0], span[1]) for span in row] for row in raw]
    if mode == "read":
        try:
            return _read_result(text, lines, raw, notes)
        except (MappingError, ValueError) as error:
            return _failed(lines, raw, str(error))
    try:
        return _edit_result(lines, raw, scores, flagged, notes, anchors)
    except (MappingError, ValueError) as error:
        return _failed(lines, raw, str(error))
    except Exception as error:  # noqa: BLE001 - the worker reports whatever the mapping raised
        return _failed(lines, raw, f"{type(error).__name__}: {error}")


def _read_result(text, lines, raw, notes) -> LyricResult:
    resolved = resolve([TimedNote(*note) for note in notes])
    spans = faithful.read(text, resolved.stream)
    rows = _rows(lines, spans)
    empty = tuple(tuple(False for _sound in line.sounds) for line in lines)
    zero = tuple(tuple(span is None or span[0] is None for span in row) for row in rows)
    groups = tuple(tuple(-1 for _sound in line.sounds) for line in lines)
    return LyricResult(
        lines=tuple(lines),
        spans=_pairs(rows),
        red=empty,
        raw=_pairs(rows),
        zero=zero,
        group=groups,
        filtered=tuple(resolved.filtered),
    )


def _edit_result(lines, raw, scores, flagged, notes, anchors) -> LyricResult:
    rows = [_raw_row(raw[index], scores[index] if index < len(scores) else ()) for index in range(len(lines))]
    resolved = resolve([TimedNote(*note) for note in notes])
    operations = tuple(solve(lines, rows, resolved.stream, anchors))
    readings = tuple(confidence.read(lines, rows, resolved.stream, operations, flagged))
    spans = sound_spans([len(line.sounds) for line in lines], operations, resolved.stream)
    red, zero, group = _tables(lines, operations, readings, resolved.stream)
    return LyricResult(
        lines=tuple(lines),
        spans=_pairs(spans),
        red=red,
        raw=_pairs(raw),
        zero=zero,
        group=group,
        operations=operations,
        filtered=tuple(resolved.filtered),
        readings=readings,
    )


def _failed(lines, raw, message: str) -> LyricResult:
    empty = tuple(tuple(False for _sound in line.sounds) for line in lines)
    groups = tuple(tuple(-1 for _sound in line.sounds) for line in lines)
    return LyricResult(
        lines=tuple(lines),
        spans=_pairs(raw),
        red=empty,
        raw=_pairs(raw),
        zero=empty,
        group=groups,
        error=message,
    )


def _pairs(rows) -> tuple:
    """Rows of spans as immutable two-tuples, `None` kept as `(None, None)`."""
    return tuple(tuple((None, None) if span is None else (span[0], span[1]) for span in row) for row in rows)


def _raw_row(spans: Sequence[tuple], scores: Sequence[float | None]) -> list[Raw]:
    return [
        Raw(start, end, scores[index] if index < len(scores) else None)
        for index, (start, end) in enumerate(spans)
    ]


def _rows(lines, spans) -> list[list[Span]]:
    """The faithful spans as drawable tuples, one row per line, matching the Sounds."""
    rows: list[list[Span]] = []
    for line, row in zip(lines, spans, strict=True):
        found: list[Span] = []
        for index in range(len(line.sounds)):
            span = row[index] if index < len(row) else None
            found.append((None, None) if span is None else (span[0], span[1]))
        rows.append(found)
    return rows


def _tables(lines, operations, readings, stream) -> tuple:
    """The strip's doubt, grey and shared-note tables, read off the operations and their readings."""
    at = {note.id: index for index, note in enumerate(stream)}
    red = [[False] * len(line.sounds) for line in lines]
    zero = [[False] * len(line.sounds) for line in lines]
    group = [[-1] * len(line.sounds) for line in lines]
    for operation, reading in zip(operations, readings, strict=True):
        if isinstance(operation, Match):
            red[operation.sound.line][operation.sound.index] = reading.low
        elif isinstance(operation, Merge):
            note = at[operation.note]
            for ref in operation.sounds:
                red[ref.line][ref.index] = reading.low
                group[ref.line][ref.index] = note
        else:
            red[operation.sound.line][operation.sound.index] = reading.low
            zero[operation.sound.line][operation.sound.index] = True
    return tuple(tuple(row) for row in red), tuple(tuple(row) for row in zero), tuple(tuple(row) for row in group)


class LyricMapper(QThread):
    mapped = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, revision, request, parent=None):
        super().__init__(parent)
        self.revision = revision
        self.request = request

    def run(self) -> None:
        try:
            result = map_lyrics(**self.request)
            self.mapped.emit((self.revision, result))
        except Exception as error:  # noqa: BLE001 - the worker reports whatever the mapping raised
            self.failed.emit(f"{type(error).__name__}: {error}")
