# SPDX-License-Identifier: AGPL-3.0-only
"""The lyrics strip: the `.krc` morae as one row of note-like blocks on the roll's shared time axis.

Each mora is drawn where the mapping put it on the notes, green while the mapping holds and red
while it is doubted, over the columns of `namioto.ui.roll.PianoRollView`, which owns the spans. The
strip is read-only: the pointer only names the block under it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from namioto.karaoke.timeline import Mora
from namioto.ui import theme

if TYPE_CHECKING:
    from namioto.ui.roll import PianoRollView

MORA_HEIGHT = 44
MORA_GRAB_PX = 6
MORA_MIN_PX = 6
MORA_TICK_PX = 2
MORA_MARGIN = 5


class _ViewportStrip(QWidget):
    """A strip sharing the roll's columns: the viewport's top left in this widget's coordinates."""

    def __init__(self, view: PianoRollView):
        super().__init__()
        self.view = view

    def origin(self) -> QPoint:
        return self.mapFromGlobal(self.view.viewport().mapToGlobal(QPoint(0, 0)))


class MoraStrip(_ViewportStrip):
    """The lyrics as one row of note-like blocks, on the roll's columns and its shared time axis.

    Each mora is a block a note would be, green while it sits on the notes the mapping gave it and
    red while the mapping is doubted; a mora of no length is a narrow tick. Read-only: nothing here
    changes the times, and the pointer only names the block under it.
    """

    def __init__(self, view: PianoRollView):
        super().__init__(view)
        self.setFixedHeight(MORA_HEIGHT)
        self.setMouseTracking(True)  # the pointer names the block under it
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
        rows = zip(self.view.lyric_lines, self.view.lyric_times, self._red(), strict=False)
        blocks = []
        for line, times, flags in rows:
            for mora, (start, end), doubted in zip(line.morae, times, flags, strict=False):
                if start is None or end is None:
                    continue
                if end <= start:
                    # a mora of no length is a tick, pulled in so it reads apart from a short block
                    tick = QRectF(self._x(start) - MORA_TICK_PX / 2, top + 6, MORA_TICK_PX, height - 12)
                    blocks.append((tick, mora, not doubted))
                    continue
                x0 = self._x(start)
                x1 = max(self._x(end), x0 + MORA_MIN_PX)
                blocks.append((QRectF(x0, top, x1 - x0, height), mora, not doubted))
        # narrow blocks last, so one flattened against its neighbour still shows over it
        for rect, mora, good in sorted(blocks, key=lambda block: block[0].width()):
            self._paint_block(painter, rect, mora, good)
        painter.setPen(QPen(colors.ruler_line, 1))
        right = self.view.viewport().width() + int(left)
        painter.drawLine(int(left), self.height() - 1, right, self.height() - 1)

    def mouseMoveEvent(self, event) -> None:
        self._set_tooltip(self._at(event.position().x()))

    def leaveEvent(self, event) -> None:
        self._set_tooltip(None)
        super().leaveEvent(event)

    def wheelEvent(self, event) -> None:
        hbar = self.view.horizontalScrollBar()
        hbar.setValue(hbar.value() - event.angleDelta().y())
        event.accept()

    def _x(self, seconds: float) -> float:
        beats = seconds / self.view.seconds_per_beat
        return self.origin().x() + self.view.mapFromScene(QPointF(beats, 0.0)).x()

    def _at(self, x: float) -> tuple[int, int] | None:
        """The drawn block under `x`, or None where the strip is empty."""
        for row, row_times in enumerate(self.view.lyric_times):
            for column, (start, end) in enumerate(row_times):
                if start is None or end is None or end <= start:
                    continue
                x0 = self._x(start)
                x1 = max(self._x(end), x0 + MORA_MIN_PX)
                if x0 - MORA_GRAB_PX <= x <= x1 + MORA_GRAB_PX:
                    return (row, column)
        return None

    def _set_tooltip(self, found: tuple[int, int] | None) -> None:
        label = self.view.lyric_lines[found[0]].morae[found[1]].label if found is not None else ""
        self.setToolTip(label)

    def _paint_block(self, painter: QPainter, rect: QRectF, mora: Mora, good: bool) -> None:
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
        painter.restore()
        text = painter.fontMetrics().elidedText(mora.label, Qt.TextElideMode.ElideRight, int(rect.width()) - 2)
        if not text:
            return
        painter.setPen(dark)
        painter.drawText(rect.translated(1, 1), Qt.AlignmentFlag.AlignCenter, text)
        painter.setPen(QColor(theme.LYRIC_TEXT))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def _red(self) -> list:
        return list(self.view.lyric_red)

    def _invalidate(self) -> None:
        self.update()
