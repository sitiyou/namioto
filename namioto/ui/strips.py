# SPDX-License-Identifier: AGPL-3.0-only
"""The lyrics strip: the `.krc` sounds as text on the roll's shared time axis.

One line of the `.krc` reads across the strip, each sound marked by a `|` at its own start and its
label just after it - green while it sits on its notes, red while the mapping doubts it, grey while
it covers none. Hovering a sound in a group lights up the whole run the note shares. The `|` is the
editor: dragging it slides that boundary of the raw aligned times, the notes and the mapping over
them following on release. The block is the note span the mapping derives (`lyric_times`), so it
lines up with the roll and covers the whole note; the `|` and the label are the raw start
(`lyric_raw`) the aligner gave. A zero-length sound's own `|` is stepped left of the note it butts
against, so the two can be told apart and dragged separately. A label is dropped, or loses its
brackets, when its room is too narrow, rather than elided.

The `|` drag is smooth - several sounds may sit inside one note cell - and is pulled onto a drawn
beat division whenever the pointer comes within `SOUND_MAGNET_PX` of one; one gesture is one undo
step and one dirty mark. Grouping is a display decision only, so it never folds the `.krc`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from namioto.ui import theme

if TYPE_CHECKING:
    from namioto.karaoke.timeline import Sound
    from namioto.ui.roll import PianoRollView

SOUND_HEIGHT = 44
SOUND_GRAB_PX = 6
SOUND_MIN_PX = 6
SOUND_GAP_PX = 20  # one CJK character plus its padding, so a squeezed sound can still name itself
SOUND_LINE_PX = 2
SOUND_PAD = 3
SOUND_MARGIN = 5
# how close (device pixels) the pointer must come to a drawn grid line for an aligned time to stick
# to it: the drag is otherwise smooth, since several sounds may sit inside one note cell
SOUND_MAGNET_PX = 4


class _ViewportStrip(QWidget):
    """A strip sharing the roll's columns: the viewport's top left in this widget's coordinates."""

    def __init__(self, view: PianoRollView):
        super().__init__()
        self.view = view

    def origin(self) -> QPoint:
        return self.mapFromGlobal(self.view.viewport().mapToGlobal(QPoint(0, 0)))


class SoundStrip(_ViewportStrip):
    """The lyrics as text on the roll's columns and its shared time axis.

    Each sound starts with a `|` and its label, green while it sits on its notes, red while the
    mapping doubts it and grey while it covers none; the run of sounds one note is shared by lights
    up while the pointer is on it. The `|` is the pointer's target: a drag slides that boundary of
    the raw aligned times, unless the view is read-only. A label is drawn only when it fits - a
    rubied one drops the base in its brackets first - so it never runs under the next `|`.
    """

    def __init__(self, view: PianoRollView):
        super().__init__(view)
        self.setFixedHeight(SOUND_HEIGHT)
        self.setMouseTracking(True)  # the pointer names the block under it and shows the drag cursor
        self._hover: tuple[int, int] | None = None
        self._drag: tuple[int, int] | None = None  # (row, boundary) in hand
        self._base: tuple | None = None  # the row as the drag found it, so a move is timed from there
        self._press_x = 0.0
        self._press_seconds = 0.0
        view.view_changed.connect(self.update)
        view.lyrics_changed.connect(self._invalidate)
        view.notes_changed.connect(self._invalidate)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        colors = theme.canvas()
        painter.fillRect(self.rect(), colors.panel)
        if not self.view.lyric_lines or not self.view.lyric_raw:
            return
        left = self.origin().x()
        painter.setClipRect(QRect(int(left), 0, self.view.viewport().width(), self.height()))
        font = QFont()
        font.setPixelSize(14)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        top = SOUND_MARGIN
        height = self.height() - 2 * SOUND_MARGIN
        blocks, labels = [], []
        for row, line in enumerate(self.view.lyric_lines):
            if row >= len(self.view.lyric_raw):
                continue
            spans = self.view.lyric_raw[row]
            mapped = self.view.lyric_times[row] if row < len(self.view.lyric_times) else spans
            flags = self.view.lyric_red[row] if row < len(self.view.lyric_red) else ()
            zeros = self.view.lyric_zero[row] if row < len(self.view.lyric_zero) else ()
            xs = self._layout(row)
            for column, sound in enumerate(line.sounds):
                if column >= len(spans):
                    break
                start, _end = spans[column]
                if start is None:
                    continue
                x0 = self._here(xs[column], self._x(start))
                x1 = self._here(xs[column + 1] if column + 1 < len(xs) else None, x0)
                room = max(int(x1 - x0) - 2 * SOUND_PAD, 0)
                good = not (column < len(flags) and flags[column])
                zero = column < len(zeros) and zeros[column]
                # the block is the NOTE the sound maps to: it lines up with the roll and covers the
                # whole note, even the stretch a `.0` neighbour sits on
                if not zero and column < len(mapped):
                    bstart, bend = mapped[column]
                    if bstart is not None and bend is not None and bend > bstart:
                        rect = QRectF(self._x(bstart), top, self._x(bend) - self._x(bstart), height)
                        blocks.append((rect, good, row, column))
                color = QColor(theme.LYRIC_TEXT) if not zero else QColor(theme.LYRIC_ZERO)
                labels.append((x0, self._fit_label(sound, metrics, room), color))
        # narrow blocks last, so one flattened against its neighbour still shows over it
        groups = self.view.lyric_group
        for rect, good, row, column in sorted(blocks, key=lambda block: block[0].width()):
            row_groups = groups[row] if row < len(groups) else ()
            note = row_groups[column] if column < len(row_groups) else -1
            # two sounds that share one NOTE are two pieces of it: their common edge is the note's
            # inside, not a boundary between notes, so it is left off and the pieces read as one
            left_join = note >= 0 and column > 0 and row_groups[column - 1] == note
            right_join = note >= 0 and column + 1 < len(row_groups) and row_groups[column + 1] == note
            self._paint_body(painter, rect, good, left_join, right_join)
        # the run that shares one note tints the blocks it holds, so the pointer can pick it out
        self._paint_groups(painter, top, height)
        for x0, text, color in labels:
            painter.setPen(QPen(color, SOUND_LINE_PX))
            painter.drawLine(int(x0), top, int(x0), top + height)
            if not text:
                continue
            rect = QRectF(x0 + SOUND_PAD, top, metrics.horizontalAdvance(text), height)
            painter.setPen(color)
            painter.drawText(rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text)
        painter.setPen(QPen(colors.ruler_line, 1))
        right = self.view.viewport().width() + int(left)
        painter.drawLine(int(left), self.height() - 1, right, self.height() - 1)

    def _paint_body(self, painter: QPainter, rect: QRectF, good: bool, left_join=False, right_join=False) -> None:
        body, light, dark = theme.lyric_shades(good)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(body)
        painter.drawRect(rect)
        painter.setPen(QPen(light, 0))
        painter.drawLine(rect.topLeft(), rect.topRight())
        if not left_join:
            painter.drawLine(rect.topLeft(), rect.bottomLeft())
        painter.setPen(QPen(dark, 0))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        if not right_join:
            painter.drawLine(rect.topRight(), rect.bottomRight())
        painter.restore()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self.view.lyric_raw or not self.view.lyric_editable:
            return
        found = self._boundary_at(event.position().x())
        if found is None:
            return
        row, boundary = found
        self._drag = (row, boundary)
        self._base = self.view.lyric_raw[row]
        self._press_x = event.position().x()
        self._press_seconds = self._boundary_seconds(self.view.lyric_raw[row], boundary) or 0.0
        self.view._begin_gesture("Move lyrics")
        self.update()  # the grabbed `|` drops back to where it really is

    def mouseMoveEvent(self, event) -> None:
        if self._drag is None:
            self._set_cursor(self._boundary_at(event.position().x()))
            self._update_hover(self._sound_at(event.position().x()))
            return
        row, boundary = self._drag
        x = event.position().x()
        # measured from the press, so a `|` drawn a gap away from its own time does not jump
        origin = self.origin().x()
        travelled = self.view.seconds_at_viewport_x(x - origin) - self.view.seconds_at_viewport_x(
            self._press_x - origin
        )
        value = self._magnet_seconds(self._press_seconds + travelled, x)
        self.view.set_sound_boundary(row, boundary, value, base=self._base)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag is None:
            return
        self._drag = None
        self._base = None
        self.view._commit_gesture()

    def leaveEvent(self, event) -> None:
        self._update_hover(None)
        self._set_cursor(None)
        super().leaveEvent(event)

    def wheelEvent(self, event) -> None:
        hbar = self.view.horizontalScrollBar()
        hbar.setValue(hbar.value() - event.angleDelta().y())
        event.accept()

    def _x(self, seconds: float) -> float:
        beats = seconds / self.view.seconds_per_beat
        return self.origin().x() + self.view.mapFromScene(QPointF(beats, 0.0)).x()

    def _here(self, drawn: float | None, fallback: float) -> float:
        return fallback if drawn is None else drawn

    def _layout(self, row: int) -> list[float | None]:
        """The drawn x of every boundary: a zero-length sound's own `|` steps left, the rest stay true.

        Two starts may share a time - a zero-length sound has the same start twice, and the sound
        after it begins there too. The note the sound butts against keeps its place, so the `.0`
        sound's own `|` steps one `SOUND_GAP_PX` to the left instead; each then has its own grab spot.
        The one in hand is drawn at its true time, so a drag follows the data and not the step.
        """
        spans = self.view.lyric_raw[row]
        true: list[float | None] = []
        for index in range(len(spans) + 1):
            seconds = self._boundary_seconds(spans, index)
            true.append(self._x(seconds) if seconds is not None else None)
        drawn = list(true)
        for index in range(len(drawn) - 2, -1, -1):
            if true[index] is None or true[index + 1] is None:
                continue
            if abs(true[index + 1] - true[index]) < 1.0 and drawn[index + 1] is not None:
                drawn[index] = drawn[index + 1] - SOUND_GAP_PX
        if self._drag is not None and self._drag[0] == row:
            # the `|` in hand shows its true time again, so the drag moves the step away
            index = self._drag[1]
            if index < len(true):
                drawn[index] = true[index]
        return drawn

    def _magnet_seconds(self, seconds: float, x: float) -> float:
        """A smooth drag, pulled onto a drawn grid line when the pointer is close enough to it.

        The time is free otherwise - several sounds may sit inside one note cell - so the beat
        divisions are magnets, not the only places an aligned time can land.
        """
        step = self.view.grid_step() * self.view.seconds_per_beat
        offset = self.view.offset
        nearest = offset + round((seconds - offset) / step) * step
        if abs(self._x(nearest) - x) <= SOUND_MAGNET_PX:
            return nearest
        return seconds

    def _fit_label(self, sound: Sound, metrics, room: int) -> str:
        """The label as far as the room takes it: whole, then just the ruby, then nothing."""
        if room <= 0:
            return ""
        if metrics.horizontalAdvance(sound.label) <= room:
            return sound.label
        if sound.rubied and metrics.horizontalAdvance(sound.ruby) <= room:
            return sound.ruby
        return ""

    def _boundary_seconds(self, spans, index: int) -> float | None:
        if index < len(spans):
            return spans[index][0]
        return spans[-1][1] if spans else None

    def _boundary_x(self, row: int, index: int) -> float | None:
        layout = self._layout(row)
        return layout[index] if index < len(layout) else None

    def _boundary_at(self, x: float) -> tuple[int, int] | None:
        """The drawn `|` nearest `x`, within reach, over the lines as they read."""
        best: tuple[int, int] | None = None
        nearest = SOUND_GRAB_PX
        for row in range(len(self.view.lyric_raw)):
            for index in range(len(self.view.lyric_raw[row]) + 1):
                found = self._boundary_x(row, index)
                if found is None:
                    continue
                distance = abs(x - found)
                if distance <= SOUND_GRAB_PX and (best is None or distance < nearest):
                    best, nearest = (row, index), distance
        return best

    def _sound_at(self, x: float) -> tuple[int, int] | None:
        """The sound whose drawn span holds `x`, for the tooltip and the group highlight."""
        for row, spans in enumerate(self.view.lyric_raw):
            layout = self._layout(row)
            for column, (start, _end) in enumerate(spans):
                if start is None:
                    continue
                x0 = self._here(layout[column], self._x(start))
                x1 = self._here(layout[column + 1] if column + 1 < len(layout) else None, x0)
                if x0 - SOUND_GRAB_PX <= x <= max(x1, x0 + SOUND_GRAB_PX):
                    return (row, column)
        return None

    def _group_run(self, found: tuple[int, int] | None) -> tuple[int, int, int] | None:
        """The run of sounds sharing one note with the hovered one, when there is one."""
        if found is None:
            return None
        row, column = found
        if row >= len(self.view.lyric_group):
            return None
        flags = self.view.lyric_group[row]
        if column >= len(flags) or flags[column] < 0:
            return None
        note = flags[column]
        first = column
        while first > 0 and flags[first - 1] == note:
            first -= 1
        last = column
        while last + 1 < len(flags) and flags[last + 1] == note:
            last += 1
        return (row, first, last) if last > first else None

    def _paint_groups(self, painter: QPainter, top: float, height: float) -> None:
        run = self._group_run(self._hover)
        if run is None:
            return
        row, first, last = run
        mapped = self.view.lyric_times[row] if row < len(self.view.lyric_times) else self.view.lyric_raw[row]
        start = mapped[first][0]
        end = mapped[last][1] if mapped[last][1] is not None else self._boundary_seconds(mapped, last + 1)
        if start is None or end is None:
            return
        color = QColor(theme.LYRIC_SELECT)
        rect = QRectF(self._x(start), top, self._x(end) - self._x(start), height)
        tint = QColor(color)
        tint.setAlpha(70)
        painter.save()
        painter.fillRect(rect, tint)
        painter.setPen(QPen(color, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
        painter.restore()

    def _set_cursor(self, found: tuple[int, int] | None) -> None:
        if found is None or not self.view.lyric_editable:
            self.unsetCursor()
        else:
            self.setCursor(Qt.CursorShape.SizeHorCursor)

    def _update_hover(self, found: tuple[int, int] | None) -> None:
        label = self.view.lyric_lines[found[0]].sounds[found[1]].label if found is not None else ""
        self.setToolTip(label)
        if found != self._hover:
            self._hover = found
            self.update()

    def _invalidate(self) -> None:
        self.update()
