# SPDX-License-Identifier: AGPL-3.0-only
"""Laying a `.krc`'s sounds onto the notes: the tables the strip draws, and the thread that maps them.

`map_lyrics` chooses the mapping for the edit mode in force, and `LyricMapper` runs it off the GUI
thread because a long `.krc` over a long roll takes a moment. The tables are the five parallel views
of one mapping - the spans the strip draws, the sounds the mapping doubts, the sounds that cover no
note, the note each sound shares, and the raw times the mapping was made from - so the view and the
strip never unpack a `Placement` themselves.
"""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal

from namioto.karaoke import map_faithful, map_sounds


def map_lyrics(lines, times, notes, text, aligned, mode="edit"):
    """The mapping for the mode in force: the `.krc`'s own `.N` and groups in read mode, the
    aligner's times in edit mode, and the sounds and notes paired in order when there are no times.

    The raw times come back with the tables: read mode has none of its own, so the mapped spans are
    what the strip draws and edits.
    """
    if mode == "read":
        # a text that failed to parse has no lines to map, and `map_faithful` would parse it again
        if notes and text and lines:
            spans, red, zero, group = _placement_tables(map_faithful(text, notes))
            return spans, red, zero, group, spans
        spans = [[(None, None)] * len(line.sounds) for line in lines]
        return (
            spans,
            [[False] * len(row) for row in spans],
            [[True] * len(row) for row in spans],
            [[-1] * len(row) for row in spans],
            spans,
        )
    if notes:
        spans, red, zero, group = _placement_tables(map_sounds(lines, times, notes, text, aligned=aligned))
        return spans, red, zero, group, times
    spans = [list(row) for row in times]
    return (
        spans,
        [[False] * len(row) for row in times],
        [[False] * len(row) for row in times],
        [[-1] * len(row) for row in times],
        times,
    )


def _placement_tables(placements):
    spans = [[placement.span for placement in row] for row in placements]
    red = [[placement.red for placement in row] for row in placements]
    zero = [[placement.zero for placement in row] for row in placements]
    group = [[placement.group for placement in row] for row in placements]
    return spans, red, zero, group


class LyricMapper(QThread):
    mapped = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, revision, lines, times, notes, text, aligned, mode, parent=None):
        super().__init__(parent)
        self.revision = revision
        self.lines = lines
        self.times = times
        self.notes = notes
        self.text = text
        self.aligned = aligned
        self.mode = mode

    def run(self) -> None:
        try:
            spans, red, zero, group, raw = map_lyrics(
                self.lines, self.times, self.notes, self.text, self.aligned, self.mode
            )
            self.mapped.emit((self.revision, self.lines, spans, red, raw, zero, group))
        except Exception as error:
            self.failed.emit(f"{type(error).__name__}: {error}")
