# SPDX-License-Identifier: AGPL-3.0-only
"""The lyrics strip: the `.krc` morae as one row of note-like blocks on the roll's shared time axis.

Each mora is a block a note would be, green while it sits on its own notes and red while it does not,
drawn over the columns of `namioto.ui.roll.PianoRollView`, which owns the spans and is the only thing
that writes them. `mora_edge`/`mora_room` are the pure part of that geometry, which the view and this
strip both work a line out with: a mora of no length takes the pointer from nothing and holds up
nothing around it.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QMenu, QWidget

from namioto.i18n import tr
from namioto.karaoke.timeline import Mora, mora_ok
from namioto.ui import theme

if TYPE_CHECKING:
    from namioto.ui.roll import PianoRollView

CLICK_SLOP_PX = 4
MORA_HEIGHT = 44
MORA_GRAB_PX = 6
MORA_MIN_PX = 6
MORA_MARGIN = 5


def mora_edge(spans: list, column: int, step: int) -> int | None:
    """The nearest block drawn beside `column`: a mora of no length is a point the line reads past."""
    for index in range(column + step, len(spans) if step > 0 else -1, step):
        start, end = spans[index]
        if start is not None and end is not None and end > start:
            return index
    return None


def mora_room(spans: list, first: int, last: int) -> tuple[float, float]:
    """The room a run of a line may move in: what is free around it, its own morae aside.

    A mora of no length takes up none of it, so the room runs to the nearest block that is drawn
    rather than to the neighbour that happens to stand next in the line.
    """
    low, high = 0.0, math.inf
    before, after = mora_edge(spans, first, -1), mora_edge(spans, last, 1)
    if before is not None:
        low = spans[before][1]
    if after is not None:
        high = spans[after][0]
    return low, high


class _ViewportStrip(QWidget):
    """A strip sharing the roll's columns: the viewport's top left in this widget's coordinates."""

    def __init__(self, view: PianoRollView):
        super().__init__()
        self.view = view

    def origin(self) -> QPoint:
        return self.mapFromGlobal(self.view.viewport().mapToGlobal(QPoint(0, 0)))


class MoraStrip(_ViewportStrip):
    """The lyrics as one row of note-like blocks, on the roll's columns and its shared time axis.

    Each mora is a block a note would be, green while it sits on its own notes and red while it does
    not. Its body can be dragged to move it - every block the selection holds, when it holds one -
    and either edge trimmed, on the same snap grid the notes are trimmed on; the line keeps its order
    and never overlaps, so a block that reaches a neighbour takes the room from it rather than
    crossing it. A click takes one block, ctrl a second, and shift everything from the last click to
    the one under the pointer - the lyrics reading as one run of morae, line after line - and a drag
    carries the whole of that selection. Dragged up or down, a block gives up its length altogether
    and stops being drawn: a mora nothing is sung on. The menu that opens over the block it follows
    puts it back, and the menu over a run of selected blocks folds that run into a single word.
    """

    mora_group_requested = pyqtSignal(int, int, int)  # one line's run of morae to fold into a word

    def __init__(self, view: PianoRollView):
        super().__init__(view)
        self.setFixedHeight(MORA_HEIGHT)
        self.setMouseTracking(True)  # the cursor is what says a drag would move a block or resize it
        self._drag: tuple[int, int, str, float, float, float] | None = None
        self._selected: set[tuple[int, int]] = set()
        self._press_block: tuple[int, int] | None = None  # what a press landed on, while it stays a click
        self._collapsed = False  # whether the drag in hand has taken the length off what it holds
        self._anchor: tuple[int, int] | None = None  # where a shift click counts from
        self._rows: dict[int, tuple] = {}  # the lines as the drag found them, so a move is timed from those
        self._press = QPointF()
        self._applied = 0.0
        self._hidden = False
        self._ok: list[list[bool]] | None = None
        view.view_changed.connect(self.update)
        view.lyrics_changed.connect(self._invalidate)
        view.notes_changed.connect(self._invalidate)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        colors = theme.canvas()
        painter.fillRect(self.rect(), colors.panel)
        if not self.view.lyric_lines or not self.view.lyric_times:
            return
        left = self.origin().x()
        painter.setClipRect(QRect(int(left), 0, self.view.viewport().width(), self.height()))
        font = QFont()
        font.setPixelSize(14)
        font.setBold(True)
        painter.setFont(font)
        top = MORA_MARGIN
        height = self.height() - 2 * MORA_MARGIN
        blocks = []
        rows = zip(
            self.view.lyric_lines, self.view.lyric_times, self._matches(), strict=False
        )  # a count that drifted draws what it can
        for row, (line, times, flags) in enumerate(rows):
            for column, (mora, (start, end), good) in enumerate(zip(line.morae, times, flags, strict=False)):
                if start is None or end is None or end <= start:
                    continue  # a mora nothing is sung on draws nothing
                x0 = self._x(start)
                x1 = max(self._x(end), x0 + MORA_MIN_PX)
                blocks.append((QRectF(x0, top, x1 - x0, height), mora, good, (row, column) in self._selected))
        # narrow blocks last, so one flattened against its neighbour still shows over it, and the
        # selected ones after those, so the mark of the selection is never the one covered
        for rect, mora, good, selected in sorted(blocks, key=lambda block: (block[3], block[0].width())):
            self._paint_block(painter, rect, mora, good, selected)
        painter.setPen(QPen(colors.ruler_line, 1))
        right = self.view.viewport().width() + int(left)
        painter.drawLine(int(left), self.height() - 1, right, self.height() - 1)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self.view.lyric_times:
            return
        self._press_block = None
        found = self._at(event.position().x())
        if found is None:
            self._select(set())
            self._anchor = None
            return
        row, column, mode = found
        start, end = self.view.lyric_times[row][column]
        if start is None or end is None:
            return
        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.ShiftModifier and self._anchor is not None:
            self._select(self._between(self._anchor, (row, column)))
        elif modifiers & Qt.KeyboardModifier.ControlModifier:
            self._select(self._selected ^ {(row, column)})
            self._anchor = (row, column)
        elif (row, column) not in self._selected:
            self._select({(row, column)})
            self._anchor = (row, column)
        else:
            # the press keeps the selection a drag would carry; if it stays a click, the release
            # narrows it to this one block
            self._anchor = (row, column)
            self._press_block = (row, column)
        if (row, column) not in self._selected:
            return  # a ctrl click that took the block out of the selection has nothing to drag
        self._press = event.position()
        self._collapsed = False
        self._rows = {selected_row: self.view.lyric_times[selected_row] for selected_row, _column in self._selected}
        self._drag = (row, column, mode, start, end)
        self.view._begin_gesture("Trim mora" if mode != "move" else "Move mora")

    def mouseMoveEvent(self, event) -> None:
        if self._drag is None:
            self._set_cursor(self._at(event.position().x()))
            return
        if self._press_block is not None and (event.position() - self._press).manhattanLength() > CLICK_SLOP_PX:
            self._press_block = None  # the press turned into a drag, so the release is not a click
        if abs(event.position().y() - self._press.y()) > MORA_HEIGHT / 4:
            self._take_the_length_off()  # up or down is what says the mora is sung nowhere
            return
        if self._collapsed:
            self._put_the_length_back()  # the drag came back up: the line is as the press found it
        row, column, mode, start, end = self._drag
        seconds = self._snap(self.view.seconds_at_viewport_x(event.position().x() - self.origin().x()))
        minimum = self.view._cell_beats() * self.view.seconds_per_beat
        if mode == "left":
            edge = min(seconds, end - minimum)
            self.view.set_mora_span(row, column, edge, end, "left", base=self._rows[row])
        elif mode == "right":
            edge = max(seconds, start + minimum)
            self.view.set_mora_span(row, column, start, edge, "right", base=self._rows[row])
        else:
            travelled = self.view.seconds_at_viewport_x(
                event.position().x() - self.origin().x()
            ) - self.view.seconds_at_viewport_x(self._press.x() - self.origin().x())
            self._move_selection(self._snap_move(travelled))

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is None:
            return
        if self._press_block is not None and len(self._selected) > 1:
            self._select({self._press_block})  # the press stayed a click, and a click picks the one
        self._drag = None
        self.view._commit_gesture()

    def contextMenuEvent(self, event) -> None:
        menu = self.strip_menu(event.pos().x())
        if menu is None:
            return
        action = menu.exec(event.globalPos())
        if action is None:
            return
        kind, row, first, last = action.data()
        if kind == "group":
            self.mora_group_requested.emit(row, first, last)
            return
        self.view._begin_gesture("Restore mora")
        self.view.restore_mora(row, first)
        self.view._commit_gesture()

    def strip_menu(self, x: float) -> QMenu | None:
        """What a right click offers: the selection folded into one word, and the morae the block
        under `x` follows put back onto the line; nothing at all when it has neither to offer."""
        menu = QMenu(self)
        run = self._selected_run()
        if run is not None:
            menu.addAction(tr("Make one word")).setData(("group", *run))
        found = self._at(x)
        if found is not None:
            row, column = found[0], found[1]
            hidden = []
            while column - len(hidden) > 0 and self._is_hidden(row, column - len(hidden) - 1):
                hidden.append(column - len(hidden) - 1)
            if hidden and not menu.isEmpty():
                menu.addSeparator()
            for index in reversed(hidden):
                label = self.view.lyric_lines[row].morae[index].label
                menu.addAction(tr("Restore {label}", label=label)).setData(("restore", row, index, index))
        return None if menu.isEmpty() else menu

    def _selected_run(self) -> tuple[int, int, int] | None:
        """The selection as one line's run of morae, when that is what it is and it holds two."""
        rows = {row for row, _column in self._selected}
        if len(rows) != 1:
            return None
        row = rows.pop()
        columns = sorted(column for selected_row, column in self._selected if selected_row == row)
        if len(columns) < 2 or columns != list(range(columns[0], columns[-1] + 1)):
            return None
        return row, columns[0], columns[-1]

    def _take_the_length_off(self) -> None:
        """Give the dragged morae no length at all, each where the drag found it."""
        self._collapsed = True
        for row, column in sorted(self._targets()):
            found = self._rows.get(row)
            if found is None or not 0 <= column < len(found):
                continue
            end = found[column][1]
            if end is not None:
                self.view.set_mora_span(row, column, end, end, base=found)

    def _put_the_length_back(self) -> None:
        """The drag came back up, so every mora it hid takes the span the drag found it with."""
        self._collapsed = False
        for row, column in sorted(self._targets()):
            found = self._rows.get(row)
            if found is None or not 0 <= column < len(found):
                continue
            start, end = found[column]
            if start is not None and end is not None:
                self.view.set_mora_span(row, column, start, end, base=found)

    def _targets(self) -> set[tuple[int, int]]:
        """The morae a drag hides: the whole selection when it holds the pressed block, else that one."""
        row, column = self._drag[0], self._drag[1]
        return self._selected if (row, column) in self._selected else {(row, column)}

    def _move_selection(self, wanted: float) -> None:
        """Shift every run the selection covers by `wanted`, as far as the tightest room allows."""
        runs = []
        for row, lines in self._rows.items():
            columns = [column for selected_row, column in self._selected if selected_row == row]
            if not columns:
                continue
            first, last = min(columns), max(columns)
            moved = [span for span in lines[first : last + 1] if None not in span]
            if not moved:
                continue
            low, high = mora_room(lines, first, last)
            start = min(span[0] for span in moved)
            end = max(span[1] for span in moved)
            if end - start > high - low:
                return  # one line with no room leaves the whole selection where it is
            runs.append((row, first, last, min(max(wanted, low - start), high - end)))
        delta = min((run[3] for run in runs), key=abs, default=0.0)
        for row, first, last, _room in runs:
            self.view.move_morae(row, first, last, delta, base=self._rows[row])

    def _between(self, first: tuple[int, int], last: tuple[int, int]) -> set[tuple[int, int]]:
        """Every mora from one to the other: the lyrics read in one order, a line at a time."""
        low, high = sorted((first, last))
        return {block for block in self._blocks() if low <= block <= high}

    def _blocks(self) -> list[tuple[int, int]]:
        """Every mora of the strip in the order the lyrics read: line by line, left to right."""
        return [(row, column) for row, line in enumerate(self.view.lyric_lines) for column in range(len(line.morae))]

    def _select(self, selected: set[tuple[int, int]]) -> None:
        self._selected = selected
        self.update()

    def _is_hidden(self, row: int, column: int) -> bool:
        start, end = self.view.lyric_times[row][column]
        return start is not None and start == end

    def wheelEvent(self, event) -> None:
        hbar = self.view.horizontalScrollBar()
        hbar.setValue(hbar.value() - event.angleDelta().y())
        event.accept()

    def _x(self, seconds: float) -> float:
        beats = seconds / self.view.seconds_per_beat
        return self.origin().x() + self.view.mapFromScene(QPointF(beats, 0.0)).x()

    def _at(self, x: float) -> tuple[int, int, str] | None:
        """The drawn block under `x`, with which part of it the pointer is on: its left edge, right
        edge or body."""
        for row, row_times in enumerate(self.view.lyric_times):
            for column, (start, end) in enumerate(row_times):
                if start is None or end is None or end <= start:
                    continue
                x0 = self._x(start)
                x1 = max(self._x(end), x0 + MORA_MIN_PX)
                if abs(x - x0) <= MORA_GRAB_PX:
                    return (row, column, "left")
                if abs(x - x1) <= MORA_GRAB_PX:
                    return (row, column, "right")
                if x0 < x < x1:
                    return (row, column, "move")
        return None

    def _set_cursor(self, found: tuple[int, int, str] | None) -> None:
        """The roll's own pair: a hand where a drag would move the block, an arrow where it resizes."""
        shape = Qt.CursorShape.ArrowCursor
        label = ""
        if found is not None:
            shape = Qt.CursorShape.OpenHandCursor if found[2] == "move" else Qt.CursorShape.SizeHorCursor
            label = self.view.lyric_lines[found[0]].morae[found[1]].label  # a narrow block still names itself
        self.setCursor(shape)
        self.setToolTip(label)

    def leaveEvent(self, event) -> None:
        self._set_cursor(None)
        super().leaveEvent(event)

    def _paint_block(self, painter: QPainter, rect: QRectF, mora: Mora, good: bool, selected: bool) -> None:
        body, light, dark = theme.lyric_shades(good)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(body)
        painter.drawRect(rect)
        painter.setPen(QPen(light, 0))
        painter.drawLine(rect.topLeft(), rect.topRight())
        painter.drawLine(rect.topLeft(), rect.bottomLeft())
        painter.setPen(QPen(dark, 0))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.drawLine(rect.topRight(), rect.bottomRight())
        if selected:
            # the body keeps saying whether the mora sits on its notes; the yellow rim is the
            # selection, and it reads over both the green and the red
            painter.setPen(QPen(QColor(theme.LYRIC_SELECT), 3))
            painter.drawRect(rect.adjusted(2, 2, -2, -2))
        painter.restore()
        text = painter.fontMetrics().elidedText(mora.label, Qt.TextElideMode.ElideRight, int(rect.width()) - 2)
        if not text:
            return
        painter.setPen(dark)
        painter.drawText(rect.translated(1, 1), Qt.AlignmentFlag.AlignCenter, text)
        painter.setPen(QColor(theme.LYRIC_TEXT))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _matches(self) -> list[list[bool]]:
        if self._ok is None:
            self._ok = mora_ok(
                list(self.view.lyric_lines),
                [list(row) for row in self.view.lyric_times],
                self.view.note_seconds(),
            )
        return self._ok

    def _invalidate(self) -> None:
        self._ok = None
        known = set(self._blocks())
        self._selected &= known  # another line's lyrics may have taken the blocks it named away
        if self._anchor is not None and self._anchor not in known:
            self._anchor = None
        self.update()

    def _snap(self, seconds: float) -> float:
        """The same grid the notes snap to, in seconds, so a block lands on the note cells."""
        per_beat = self.view.seconds_per_beat
        return self.view._snap_beats(seconds / per_beat) * per_beat

    def _snap_move(self, seconds: float) -> float:
        """A movement rather than a place on the grid: whole cells, so a block keeps its offset."""
        per_beat = self.view.seconds_per_beat
        return round(seconds / per_beat / self.view.snap) * self.view.snap * per_beat
