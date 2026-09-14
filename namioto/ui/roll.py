# SPDX-License-Identifier: AGPL-3.0-only
"""Piano-roll widgets: note items, grid background, interactive view, ruler and keyboard."""

from __future__ import annotations

import math

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QTransform
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QRubberBand,
    QWidget,
)

from namioto.spectrum import MIDI_OFFSET, NOTE_COUNT, NoteSpectrum
from namioto.ui.spectrogram import SpectrumImage

PITCH_MIN = 21
PITCH_MAX = 108
PITCH_COUNT = PITCH_MAX - PITCH_MIN + 1
LENGTH_BEATS = 64
CONTENT_MARGIN = 4.0
NOTE_INSET = 0.06
MIN_DURATION = 0.0625
BAR_BEATS = 4.0

SNAP_CHOICES = (("1/1", 4.0), ("1/2", 2.0), ("1/4", 1.0), ("1/8", 0.5), ("1/16", 0.25), ("1/32", 0.125))

BG = QColor("#191c23")
ROW_WHITE = QColor("#262b34")
ROW_BLACK = QColor("#20242c")
GRID_LINE = QColor("#2f3541")
GRID_BEAT = QColor("#434c5c")
GRID_BAR = QColor("#6d7a92")
NOTE_FILL = QColor("#ff2f2f")
NOTE_EDGE_LIGHT = QColor("#ffb9b9")
NOTE_EDGE_DARK = QColor("#550f0f")
NOTE_SELECTED = QColor("#fecfcf")
NOTE_SELECTED_EDGE = QColor("#fe7474")
TEXT = QColor("#94a0b5")
PANEL = QColor("#20242c")
SPECTRUM_BG = QColor("#000000")
SPECTRUM_OCTAVE = QColor("#c0c0c0")
SPECTRUM_BEAT = QColor("#606060")
SPECTRUM_BAR = QColor("#c0c0c0")
SPECTRUM_TOP = PITCH_MAX - (MIDI_OFFSET + NOTE_COUNT - 1)
MIN_LINE_SPACING = 16.0


def is_black_key(pitch: int) -> bool:
    return pitch % 12 in (1, 3, 6, 8, 10)


def note_name(pitch: int) -> str:
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{names[pitch % 12]}{pitch // 12 - 1}"


def _is_multiple(value: float, step: float) -> bool:
    return abs(value / step - round(value / step)) < 1e-6


class NoteItem(QGraphicsRectItem):
    """A single MIDI note; scene units are beats (x) and semitone rows (y)."""

    def __init__(self, pitch: int, start: float, duration: float):
        super().__init__()
        self.pitch = pitch
        self.start = start
        self.duration = duration
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self._sync()

    def _sync(self) -> None:
        self.setRect(0.0, 0.0, self.duration, 1.0 - 2 * NOTE_INSET)
        self.setPos(self.start, PITCH_MAX - self.pitch + NOTE_INSET)

    @property
    def end(self) -> float:
        return self.start + self.duration

    def set_duration(self, duration: float) -> None:
        self.duration = max(MIN_DURATION, duration)
        self._sync()

    def set_range(self, start: float, pitch: int) -> None:
        self.start = max(0.0, start)
        self.pitch = min(PITCH_MAX, max(PITCH_MIN, pitch))
        self._sync()

    def paint(self, painter: QPainter, option, widget=None) -> None:
        selected = self.isSelected()
        body = NOTE_SELECTED if selected else NOTE_FILL
        light = NOTE_SELECTED_EDGE if selected else NOTE_EDGE_LIGHT
        dark = NOTE_SELECTED_EDGE if selected else NOTE_EDGE_DARK
        rect = self.rect()
        transform = painter.transform()
        px, py = 1.0 / transform.m11(), 1.0 / transform.m22()  # one device pixel in scene units
        left, top = rect.left() + px / 2, rect.top() + py / 2
        right, bottom = rect.right() - px / 2, rect.bottom() - py / 2

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(body)
        painter.drawRect(rect)
        painter.setPen(QPen(light, 0))
        painter.drawLine(QPointF(left, top), QPointF(right + px / 2, top))
        painter.drawLine(QPointF(left, top), QPointF(left, bottom + py / 2))
        painter.setPen(QPen(dark, 0))
        painter.drawLine(QPointF(left - px / 2, bottom), QPointF(right + px / 2, bottom))
        painter.drawLine(QPointF(right, top - py / 2), QPointF(right, bottom + py / 2))
        painter.restore()


class PianoRollView(QGraphicsView):
    view_changed = pyqtSignal()
    notes_changed = pyqtSignal()

    GRAB_PX = 7
    MIN_ZOOM_X, MAX_ZOOM_X = 12.0, 900.0
    MIN_ZOOM_Y, MAX_ZOOM_Y = 8.0, 64.0

    def __init__(self, parent=None):
        scene = QGraphicsScene()
        scene.setSceneRect(QRectF(0.0, 0.0, LENGTH_BEATS, PITCH_COUNT))
        super().__init__(scene, parent)
        self._scene = scene
        self.snap = 0.25
        self.tool = "pen"
        self._bpm = 120.0
        self.gain = 240.0
        self.contrast = 1.0
        self.spectrum: SpectrumImage | None = None
        self._zoom_x = 48.0
        self._zoom_y = 16.0
        self._mode: str | None = None
        self._anchor = QPointF()
        self._press_pos = QPoint()
        self._grab_note: NoteItem | None = None
        self._snapshot: dict[NoteItem, tuple[float, int, float]] = {}

        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setTransform(QTransform.fromScale(self._zoom_x, self._zoom_y))

        self._rubber = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
        self._rubber.setStyleSheet("background: rgba(90,150,255,50); border: 1px solid #5a96ff;")
        self._rubber_origin = QPoint()
        self._initialized = False

    @property
    def bpm(self) -> float:
        return self._bpm

    @bpm.setter
    def bpm(self, value: float) -> None:
        self._bpm = max(1.0, float(value))
        self._update_scene()
        self.refresh()

    def content_beats(self) -> float:
        """Scrollable length: the default canvas, the spectrum or the notes, whichever is longest."""
        end = float(LENGTH_BEATS)
        if self.spectrum is not None:
            end = max(end, self.spectrum.spectrum.frames * self.frame_width())
        for note in self.notes():
            end = max(end, note.end)
        return end + CONTENT_MARGIN

    def _update_scene(self) -> None:
        self._scene.setSceneRect(QRectF(0.0, 0.0, self.content_beats(), PITCH_COUNT))

    def add_note(self, pitch: int, start: float, duration: float) -> NoteItem:
        note = NoteItem(pitch, start, duration)
        self._scene.addItem(note)
        self._update_scene()
        self.notes_changed.emit()
        return note

    def clear_notes(self) -> None:
        for note in self.notes():
            self._scene.removeItem(note)
        self._update_scene()
        self.notes_changed.emit()
        self.view_changed.emit()

    def notes(self) -> list[NoteItem]:
        return [item for item in self._scene.items() if isinstance(item, NoteItem)]

    def selected_notes(self) -> list[NoteItem]:
        return [note for note in self.notes() if note.isSelected()]

    def set_spectrum(self, spectrum: NoteSpectrum | None) -> None:
        self.spectrum = SpectrumImage(spectrum) if spectrum is not None else None
        self._update_scene()
        self.refresh()

    def refresh(self) -> None:
        self.viewport().update()
        self.view_changed.emit()

    def frame_width(self) -> float:
        """Scene width of one spectrum frame, in beats."""
        return self.spectrum.spectrum.frame_ms / 1000.0 * self.bpm / 60.0

    def grid_step(self) -> float:
        for step in (1 / 32, 1 / 16, 1 / 8, 1 / 4, 1 / 2, 1.0, 2.0, BAR_BEATS):
            if step * self._zoom_x >= 9.0:
                return step
        return BAR_BEATS

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._initialized:
            self._initialized = True
            self.centerOn(8.0, PITCH_MAX - 60)

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        super().scrollContentsBy(dx, dy)
        self.view_changed.emit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.view_changed.emit()

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        first_row = max(0, int(math.floor(rect.top())))
        last_row = min(PITCH_COUNT, int(math.ceil(rect.bottom())) + 1)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)  # keeps 1px grid lines on one pixel

        if self.spectrum is None:
            painter.fillRect(rect, BG)
            for row in range(first_row, last_row):
                color = ROW_BLACK if is_black_key(PITCH_MAX - row) else ROW_WHITE
                painter.fillRect(QRectF(rect.left(), float(row), rect.width(), 1.0), color)
            painter.setPen(QPen(GRID_LINE, 0))
            for row in range(first_row, last_row + 1):
                y = float(row)
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            step = self.grid_step()
            x = math.floor(rect.left() / step) * step
            while x <= rect.right():
                if _is_multiple(x, BAR_BEATS):
                    painter.setPen(QPen(GRID_BAR, 0))
                elif _is_multiple(x, 1.0):
                    painter.setPen(QPen(GRID_BEAT, 0))
                else:
                    painter.setPen(QPen(GRID_LINE, 0))
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                x += step
            painter.restore()
            return

        painter.fillRect(rect, SPECTRUM_BG)
        self._draw_spectrum(painter, rect)
        painter.setPen(QPen(SPECTRUM_OCTAVE, 0))
        for pitch in range(MIDI_OFFSET, MIDI_OFFSET + NOTE_COUNT + 1, 12):
            y = float(PITCH_MAX - pitch + 1)  # the C row's lower edge (B sits below C)
            if first_row <= y <= last_row:
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        for beat in range(math.floor(rect.left()), int(rect.right()) + 2):
            bar = _is_multiple(beat, BAR_BEATS)
            spacing = self._zoom_x * (BAR_BEATS if bar else 1.0)
            if spacing < MIN_LINE_SPACING:
                continue
            painter.setPen(QPen(SPECTRUM_BAR if bar else SPECTRUM_BEAT, 0))
            painter.drawLine(QPointF(beat, rect.top()), QPointF(beat, rect.bottom()))
        painter.restore()

    def _draw_spectrum(self, painter: QPainter, rect: QRectF) -> None:
        image = self.spectrum.image(self.gain, self.contrast)
        width = self.frame_width()
        first = max(0, int(math.floor(rect.left() / width)))
        last = min(self.spectrum.spectrum.frames, int(math.ceil(rect.right() / width)) + 1)
        if last <= first:
            return
        target = QRectF(first * width, SPECTRUM_TOP, (last - first) * width, float(image.height()))
        source = QRectF(first, 0.0, last - first, float(image.height()))
        painter.setRenderHint(
            QPainter.RenderHint.SmoothPixmapTransform, width * self._zoom_x < 1.0 or self._zoom_y < 1.0
        )
        painter.drawImage(target, image, source)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

    # --- interaction -----------------------------------------------------

    def _note_at(self, scene_pos: QPointF) -> NoteItem | None:
        item = self.itemAt(self.mapFromScene(scene_pos))
        return item if isinstance(item, NoteItem) else None

    def _pitch_at(self, y: float) -> int:
        row = min(PITCH_COUNT - 1, max(0, int(math.floor(y))))
        return PITCH_MAX - row

    def _snap_beats(self, x: float) -> float:
        return round(x / self.snap) * self.snap

    def _clear_selection(self) -> None:
        for note in self.notes():
            note.setSelected(False)

    def mousePressEvent(self, event) -> None:
        pos = event.position().toPoint()
        scene_pos = self.mapToScene(pos)

        if event.button() == Qt.MouseButton.MiddleButton:
            self._mode = "pan"
            self._press_pos = pos
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if event.button() == Qt.MouseButton.RightButton:
            note = self._note_at(scene_pos)
            if note is not None:
                self._scene.removeItem(note)
                self.notes_changed.emit()
                self.view_changed.emit()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        note = self._note_at(scene_pos)
        modifier = event.modifiers()
        ctrl = bool(modifier & Qt.KeyboardModifier.ControlModifier)
        shift = bool(modifier & Qt.KeyboardModifier.ShiftModifier)
        self._anchor = scene_pos

        if note is None:
            if ctrl or self.tool == "select":
                if not shift:
                    self._clear_selection()
                self._mode = "select"
                self._rubber_origin = pos
                self._rubber.setGeometry(QRect(pos, pos))
                self._rubber.show()
                return
            start = max(0.0, self._snap_beats(scene_pos.x()))
            note = self.add_note(self._pitch_at(scene_pos.y()), start, max(self.snap, MIN_DURATION))
            self._clear_selection()
            note.setSelected(True)
            self._grab_note = note
            self._mode = "resize"
            self._snapshot = {note: (note.start, note.pitch, note.duration)}
            self.view_changed.emit()
            return

        if shift or ctrl:
            note.setSelected(not note.isSelected())
        elif not note.isSelected():
            self._clear_selection()
            note.setSelected(True)
        if not note.isSelected():
            return

        self._grab_note = note
        edge = self.GRAB_PX / self._zoom_x
        self._mode = "resize" if scene_pos.x() >= note.end - edge else "move"
        self._snapshot = {n: (n.start, n.pitch, n.duration) for n in self.selected_notes()}

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        scene_pos = self.mapToScene(pos)

        if self._mode is None:
            note = self._note_at(scene_pos)
            if note is not None and scene_pos.x() >= note.end - self.GRAB_PX / self._zoom_x:
                self.viewport().setCursor(Qt.CursorShape.SizeHorCursor)
            elif note is not None:
                self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.viewport().unsetCursor()
            return

        if self._mode == "pan":
            delta = pos - self._press_pos
            self._press_pos = pos
            hbar, vbar = self.horizontalScrollBar(), self.verticalScrollBar()
            hbar.setValue(hbar.value() - delta.x())
            vbar.setValue(vbar.value() - delta.y())
            return

        if self._mode == "select":
            self._rubber.setGeometry(QRect(self._rubber_origin, pos).normalized())
            region = self.mapToScene(self._rubber.geometry()).boundingRect()
            for item in self._scene.items(region):
                if isinstance(item, NoteItem):
                    item.setSelected(True)
            return

        if self._grab_note is None or self._grab_note not in self._snapshot:
            return

        if self._mode == "resize":
            start = self._snapshot[self._grab_note][0]
            end = self._snap_beats(scene_pos.x())
            self._grab_note.set_duration(end - start)
            return

        origin_start = self._snapshot[self._grab_note][0]
        delta_x = self._snap_beats(origin_start + scene_pos.x() - self._anchor.x()) - origin_start
        delta_row = round(scene_pos.y() - self._anchor.y())
        for note, (start, pitch, _duration) in self._snapshot.items():
            note.set_range(start + delta_x, pitch - delta_row)
        self.view_changed.emit()

    def mouseReleaseEvent(self, event) -> None:
        if self._rubber.isVisible():
            self._rubber.hide()
        self._mode = None
        self._grab_note = None
        self._snapshot = {}
        self.viewport().unsetCursor()
        self.view_changed.emit()

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        modifier = event.modifiers()
        anchor = event.position().toPoint()
        factor = 1.0016**delta
        if modifier & Qt.KeyboardModifier.ControlModifier and modifier & Qt.KeyboardModifier.ShiftModifier:
            self._zoom(1.0, factor, anchor)
        elif modifier & Qt.KeyboardModifier.ControlModifier:
            self._zoom(factor, 1.0, anchor)
        elif modifier & Qt.KeyboardModifier.ShiftModifier:
            hbar = self.horizontalScrollBar()
            hbar.setValue(hbar.value() - delta)
        else:
            vbar = self.verticalScrollBar()
            vbar.setValue(vbar.value() - delta)
        event.accept()

    def _zoom(self, factor_x: float, factor_y: float, anchor: QPoint) -> None:
        scene_pos = self.mapToScene(anchor)
        before = self.mapFromScene(scene_pos)
        self._zoom_x = min(self.MAX_ZOOM_X, max(self.MIN_ZOOM_X, self._zoom_x * factor_x))
        self._zoom_y = min(self.MAX_ZOOM_Y, max(self.MIN_ZOOM_Y, self._zoom_y * factor_y))
        self.setTransform(QTransform.fromScale(self._zoom_x, self._zoom_y))
        delta = self.mapFromScene(scene_pos) - before
        hbar, vbar = self.horizontalScrollBar(), self.verticalScrollBar()
        hbar.setValue(hbar.value() + delta.x())
        vbar.setValue(vbar.value() + delta.y())
        self.view_changed.emit()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            selection = self.selected_notes()
            for note in selection:
                self._scene.removeItem(note)
            if selection:
                self.notes_changed.emit()
                self.view_changed.emit()
            return
        if key == Qt.Key.Key_A and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            for note in self.notes():
                note.setSelected(True)
            return
        if key == Qt.Key.Key_Escape and self._snapshot:
            for note, (start, pitch, duration) in self._snapshot.items():
                note.set_range(start, pitch)
                note.set_duration(duration)
            self._snapshot = {}
            self._mode = None
            self._grab_note = None
            self.view_changed.emit()
            return
        super().keyPressEvent(event)


class TimelineRuler(QWidget):
    def __init__(self, view: PianoRollView):
        super().__init__()
        self.view = view
        self._last_x: float | None = None
        self.setFixedHeight(24)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        view.view_changed.connect(self.update)

    def origin(self) -> QPoint:
        """The viewport's top left corner in this widget's coordinates."""
        return self.mapFromGlobal(self.view.viewport().mapToGlobal(QPoint(0, 0)))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), PANEL)
        viewport = self.view.viewport()
        left_offset = self.origin().x()
        painter.setClipRect(QRect(int(left_offset), 0, viewport.width(), self.height()))
        font = QFont()
        font.setPixelSize(10)
        painter.setFont(font)

        step = self.view.grid_step()
        left = self.view.mapToScene(QPoint(0, 0)).x()
        right = self.view.mapToScene(QPoint(viewport.width(), 0)).x()
        x = math.floor(left / step) * step
        while x <= right:
            px = left_offset + self.view.mapFromScene(QPointF(x, 0.0)).x()
            if _is_multiple(x, BAR_BEATS):
                painter.setPen(QPen(GRID_BAR, 1))
                painter.drawLine(px, 0, px, self.height())
                painter.setPen(TEXT)
                painter.drawText(px + 4, 16, str(int(x // BAR_BEATS) + 1))
            elif _is_multiple(x, 1.0):
                painter.setPen(QPen(GRID_BEAT, 1))
                painter.drawLine(px, self.height() - 8, px, self.height())
            else:
                painter.setPen(QPen(GRID_LINE, 1))
                painter.drawLine(px, self.height() - 4, px, self.height())
            x += step

        painter.setPen(QPen(QColor("#3a4152"), 1))
        right_edge = viewport.width() + int(left_offset)
        painter.drawLine(int(left_offset), self.height() - 1, right_edge, self.height() - 1)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._last_x = event.position().x()

    def mouseMoveEvent(self, event) -> None:
        if self._last_x is None:
            return
        hbar = self.view.horizontalScrollBar()
        hbar.setValue(hbar.value() - int(event.position().x() - self._last_x))
        self._last_x = event.position().x()

    def mouseReleaseEvent(self, event) -> None:
        self._last_x = None

    def wheelEvent(self, event) -> None:
        hbar = self.view.horizontalScrollBar()
        hbar.setValue(hbar.value() - event.angleDelta().y())


class PianoKeyboard(QWidget):
    def __init__(self, view: PianoRollView):
        super().__init__()
        self.view = view
        self.setFixedWidth(66)
        view.view_changed.connect(self.update)

    def origin(self) -> QPoint:
        """The viewport's top left corner in this widget's coordinates."""
        return self.mapFromGlobal(self.view.viewport().mapToGlobal(QPoint(0, 0)))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), PANEL)
        viewport = self.view.viewport()
        top_offset = self.origin().y()
        painter.setClipRect(QRect(0, int(top_offset), self.width(), viewport.height()))
        font = QFont()
        font.setPixelSize(9)
        painter.setFont(font)
        white = QColor("#d8dde6")
        black = QColor("#15181e")

        for pitch in range(PITCH_MIN, PITCH_MAX + 1):
            row = PITCH_MAX - pitch
            top = top_offset + self.view.mapFromScene(QPointF(0.0, float(row))).y()
            bottom = top_offset + self.view.mapFromScene(QPointF(0.0, float(row) + 1.0)).y()
            if bottom < top_offset or top > viewport.height() + top_offset:
                continue
            if is_black_key(pitch):
                width = int(self.width() * 0.62)
                painter.fillRect(QRect(0, top, width, bottom - top), black)
            else:
                painter.fillRect(QRect(0, top, self.width(), bottom - top), white)
            if pitch % 12 == 0:
                painter.setPen(QColor("#454c5a"))
                painter.drawText(4, (top + bottom) // 2 + 3, note_name(pitch))

        painter.setPen(QPen(QColor("#101318"), 1))
        for pitch in range(PITCH_MIN, PITCH_MAX + 2):
            y = top_offset + self.view.mapFromScene(QPointF(0.0, float(PITCH_MAX - pitch))).y()
            painter.drawLine(0, y, self.width(), y)

    def wheelEvent(self, event) -> None:
        vbar = self.view.verticalScrollBar()
        vbar.setValue(vbar.value() - event.angleDelta().y())
