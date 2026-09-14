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
OVERTONES = (2, 3)  # the partials WaveTone marks over the row under the mouse
HOVER_BAND = QColor(255, 255, 255, 85)
HOVER_KEY = QColor("#ff4040")
EDIT_DIM = 0.65  # the spectrum steps back while editing so the notes stand out over it
PLAYHEAD = QColor("#e6ecf5")
MIN_GRID_SPACING = 9.0
CLICK_SLOP_PX = 4
RULER_TIME_ROW = 24
RULER_HEIGHT = 46
TIME_LABEL_SPACING = 84.0
MEASURE_LABEL_SPACING = 30.0
TIME_STEPS = (0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 30.0, 60.0, 120.0, 300.0)


def is_black_key(pitch: int) -> bool:
    return pitch % 12 in (1, 3, 6, 8, 10)


def note_name(pitch: int) -> str:
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    return f"{names[pitch % 12]}{pitch // 12 - 1}"


def _is_multiple(value: float, step: float) -> bool:
    return abs(value / step - round(value / step)) < 1e-6


def format_time(seconds: float) -> str:
    """Time as the transport shows it, mm:ss.mmm."""
    minutes, rest = divmod(max(0.0, seconds), 60.0)
    return f"{int(minutes):02d}:{rest:06.3f}"


def time_step(pixels_per_second: float, minimum: float) -> float:
    """Smallest 1-2-5 step that keeps the time grid at least `minimum` pixels apart."""
    for step in TIME_STEPS:
        if step * pixels_per_second >= minimum:
            return step
    return TIME_STEPS[-1]


class NoteItem(QGraphicsRectItem):
    """A single MIDI note; scene units are beats (x) and semitone rows (y)."""

    def __init__(self, pitch: int, start: float, duration: float):
        super().__init__()
        # notes also arrive from files and models, and they still have to land inside the roll
        self.pitch = min(PITCH_MAX, max(PITCH_MIN, int(pitch)))
        self.start = max(0.0, float(start))
        self.duration = max(MIN_DURATION, float(duration))
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
    """The roll: the note grid, the interaction with it and the drawn extras (spectrum, cursor)."""

    view_changed = pyqtSignal()
    notes_changed = pyqtSignal()
    hover_changed = pyqtSignal(object)
    note_preview = pyqtSignal(int)
    seek_requested = pyqtSignal(float)

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
        self.edit_mode = False
        self.overtone_highlight = True
        self.playing = False
        self.division = "beats"
        self.initial_center = (8.0, float(PITCH_MAX - 60))  # where the view opens, unless a session says otherwise
        self.hover_pitch: int | None = None
        self._preview_pitch: int | None = None
        self.playhead: float | None = None
        self._bpm = 120.0
        self.gain = 240.0
        self.contrast = 1.0
        self.spectrum: SpectrumImage | None = None
        self._zoom_x = 48.0
        self._zoom_y = 16.0
        self._mode: str | None = None
        self._anchor = QPointF()
        self._press_pos = QPoint()
        self._trim_edge = ""
        self._grab_note: NoteItem | None = None
        self._snapshot: dict[NoteItem, tuple[float, int, float]] = {}

        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.viewport().setMouseTracking(True)  # the row under the mouse is highlighted
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
        """Tempo of the beat grid. Notes hold their place in the audio, so a new tempo only
        re-divides the grid they sit on: their beat coordinates and the horizontal zoom are
        rescaled together, which leaves the audio and the notes where they are on screen."""
        tempo = max(1.0, float(value))
        scale = tempo / self._bpm  # seconds = beats * 60 / bpm, so the beats scale with the tempo
        if scale != 1.0:
            self._zoom_x = min(self.MAX_ZOOM_X, max(self.MIN_ZOOM_X, self._zoom_x / scale))
            self.setTransform(QTransform.fromScale(self._zoom_x, self._zoom_y))
            for note in self.notes():
                note.set_range(note.start * scale, note.pitch)
                note.set_duration(note.duration * scale)
        self._bpm = tempo
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

    def set_notes(self, notes) -> None:
        """Replace every note in one go: a project or an extraction arrives all at once."""
        for note in self.notes():
            self._scene.removeItem(note)
        for pitch, start, duration in notes:
            self._scene.addItem(NoteItem(pitch, start, duration))
        self._update_scene()
        self.notes_changed.emit()

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

    def highlight_pitches(self) -> list[int]:
        """The rows to tint behind the notes: the row under the mouse, and while editing also the
        rows of its overtones, which is the WaveTone hint about where a note would double it."""
        if self.hover_pitch is None:
            return []
        if not self.edit_mode or not self.overtone_highlight:
            return [self.hover_pitch]
        pitches = [self.hover_pitch]
        for harmonic in OVERTONES:
            pitch = self.hover_pitch + round(12 * math.log2(harmonic))
            if PITCH_MIN <= pitch <= PITCH_MAX and pitch not in pitches:
                pitches.append(pitch)
        return pitches

    def set_hover_pitch(self, pitch: int | None) -> None:
        if pitch != self.hover_pitch:
            self.hover_pitch = pitch
            self.hover_changed.emit(pitch)
            self.refresh()

    def set_playhead(self, seconds: float | None) -> None:
        if seconds != self.playhead:
            self.playhead = seconds
            self.viewport().update()

    def pixels_per_beat(self) -> float:
        return self._zoom_x

    @property
    def zoom(self) -> tuple[float, float]:
        """Pixels per beat and per semitone row, as the zoom controls leave them."""
        return self._zoom_x, self._zoom_y

    def set_zoom(self, zoom_x: float, zoom_y: float) -> None:
        """Start at a given zoom, the way `showEvent` centers the view where the session left it."""
        self._zoom_x = min(self.MAX_ZOOM_X, max(self.MIN_ZOOM_X, zoom_x))
        self._zoom_y = min(self.MAX_ZOOM_Y, max(self.MIN_ZOOM_Y, zoom_y))
        self.setTransform(QTransform.fromScale(self._zoom_x, self._zoom_y))
        self.refresh()

    def center_on(self, x: float, y: float) -> None:
        """Put (x, y) in the middle of the viewport, the way opening a project restores its view."""
        self.initial_center = (x, y)
        self.centerOn(x, y)
        self.refresh()

    @property
    def seconds_per_beat(self) -> float:
        """What one scene unit is worth in time: the one conversion every caller has to get right."""
        return 60.0 / self.bpm

    def seconds_at_viewport_x(self, x: float) -> float:
        """The timeline position under a viewport x, for the widgets that share the roll's columns."""
        return max(0.0, self.mapToScene(QPoint(int(x), 0)).x()) * 60.0 / self.bpm

    def pixels_per_second(self) -> float:
        return self._zoom_x * self.bpm / 60.0

    def seconds_lines(self, rect: QRectF, minimum: float) -> list[tuple[float, float, bool]]:
        """(x, seconds, major) of the time grid across `rect`, on the 1-2-5 step that keeps the
        lines at least `minimum` pixels apart and marks every step above it as a major line."""
        index = TIME_STEPS.index(time_step(self.pixels_per_second(), minimum))
        step, major = TIME_STEPS[index], TIME_STEPS[min(index + 1, len(TIME_STEPS) - 1)]
        beats_per_second = self.bpm / 60.0
        value = math.floor(rect.left() / (step * beats_per_second)) * step
        lines = []
        while value * beats_per_second <= rect.right():
            lines.append((value * beats_per_second, value, _is_multiple(value, major)))
            value += step
        return lines

    def division_lines(self, rect: QRectF, minimum: float, snap: bool = False) -> list[tuple[float, int]]:
        """Time-axis grid lines across `rect` as (x, level) pairs, 0 minor, 1 beat, 2 prominent.

        The division picks the basis: beats of the tempo map, or seconds. `snap` follows the snap
        grid on the beats basis, for the canvas that has no spectrum behind it.
        """
        if self.division == "seconds":
            return [(x, 2 if major else 0) for x, _seconds, major in self.seconds_lines(rect, minimum)]
        step = self.grid_step() if snap else 1.0
        value = math.floor(rect.left() / step) * step
        lines = []
        while value <= rect.right():
            level = 2 if _is_multiple(value, BAR_BEATS) else 1 if _is_multiple(value, 1.0) else 0
            if self._zoom_x * (BAR_BEATS if level == 2 else step) >= minimum:
                lines.append((value, level))
            value += step
        return lines

    def grid_step(self) -> float:
        for step in (1 / 32, 1 / 16, 1 / 8, 1 / 4, 1 / 2, 1.0, 2.0, BAR_BEATS):
            if step * self._zoom_x >= MIN_GRID_SPACING:
                return step
        return BAR_BEATS

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._initialized:
            self._initialized = True
            self.centerOn(*self.initial_center)

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
            for x, level in self.division_lines(rect, MIN_GRID_SPACING, snap=True):
                painter.setPen(QPen((GRID_LINE, GRID_BEAT, GRID_BAR)[level], 0))
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            self._draw_cursor(painter, rect)
            painter.restore()
            return

        painter.fillRect(rect, SPECTRUM_BG)
        self._draw_spectrum(painter, rect)
        painter.setPen(QPen(SPECTRUM_OCTAVE, 0))
        for pitch in range(MIDI_OFFSET, MIDI_OFFSET + NOTE_COUNT + 1, 12):
            y = float(PITCH_MAX - pitch + 1)  # the C row's lower edge (B sits below C)
            if first_row <= y <= last_row:
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        for x, level in self.division_lines(rect, MIN_LINE_SPACING):
            painter.setPen(QPen(SPECTRUM_BAR if level == 2 else SPECTRUM_BEAT, 0))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        self._draw_cursor(painter, rect)
        painter.restore()

    def _draw_cursor(self, painter: QPainter, rect: QRectF) -> None:
        """The rows highlighted under the mouse; the playback position goes over the notes."""
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(HOVER_BAND)
        for pitch in self.highlight_pitches():
            painter.drawRect(QRectF(rect.left(), float(PITCH_MAX - pitch), rect.width(), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        """The playback position, over the notes so that they never hide it."""
        if self.playhead is None:
            return
        x = self.playhead * self.bpm / 60.0
        if not rect.left() <= x <= rect.right():
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(QPen(PLAYHEAD, 0))
        painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
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
        if self.edit_mode:
            painter.setOpacity(EDIT_DIM)  # blends with the black underneath, so only the cells fade
        painter.drawImage(target, image, source)
        painter.setOpacity(1.0)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

    # --- interaction -----------------------------------------------------

    def _note_at(self, scene_pos: QPointF) -> NoteItem | None:
        item = self.itemAt(self.mapFromScene(scene_pos))
        return item if isinstance(item, NoteItem) else None

    def _pitch_at(self, y: float) -> int:
        row = min(PITCH_COUNT - 1, max(0, int(math.floor(y))))
        return PITCH_MAX - row

    def _cell_beats(self) -> float:
        """One snap cell, but never shorter than a note can be."""
        return max(self.snap, MIN_DURATION)

    def _snap_beats(self, x: float) -> float:
        return round(x / self.snap) * self.snap

    def _snap_floor_beats(self, x: float) -> float:
        return math.floor(x / self.snap) * self.snap

    def _snap_ceil_beats(self, x: float) -> float:
        return math.ceil(x / self.snap) * self.snap

    def _clear_selection(self) -> None:
        for note in self.notes():
            note.setSelected(False)

    def mouseDoubleClickEvent(self, event) -> None:
        # Qt files the second of two quick clicks as a double-click, and that press has to seek and
        # audition like any other: the roll has no double-click gesture for it to mean instead
        self.mousePressEvent(event)

    def mousePressEvent(self, event) -> None:
        pos = event.position().toPoint()
        scene_pos = self.mapToScene(pos)

        if event.button() == Qt.MouseButton.MiddleButton:
            self._mode = "pan"
            self._press_pos = pos
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if event.button() == Qt.MouseButton.LeftButton:
            if not self.playing:
                # seeking is independent of the mode and of the tool, so every press in the roll moves
                # the playhead, over a note as well as over the grid. While the file is playing the roll
                # keeps its cursor and stays an editor; the time ruler is the one that seeks then
                self.seek_requested.emit(self.seconds_at_viewport_x(pos.x()))
            # and it sounds the row it lands on, whatever the mode, so a click is heard while editing
            # and while only listening
            self._preview_pitch = self._pitch_at(scene_pos.y())
            self.note_preview.emit(self._preview_pitch)
            if not self.edit_mode:
                self._mode = "seek"  # dragging on, the playhead is what follows the pointer
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
            pitch = self._pitch_at(scene_pos.y())
            start = max(0.0, self._snap_floor_beats(scene_pos.x()))
            note = self.add_note(pitch, start, self._cell_beats())
            self._clear_selection()
            note.setSelected(True)
            self._grab_note = note
            self._mode = "draw"
            self._snapshot = {note: (note.start, note.pitch, note.duration)}
            self.view_changed.emit()
            return

        if shift:
            # Shift splits a note into two halves: the left one moves its start, the right one its end.
            # Ctrl-click is what adds to the selection now.
            self._clear_selection()
            note.setSelected(True)
            self._grab_note = note
            self._mode = "trim"
            self._trim_edge = "start" if scene_pos.x() < note.start + note.duration / 2 else "end"
            self._snapshot = {note: (note.start, note.pitch, note.duration)}
            return

        if ctrl:
            note.setSelected(not note.isSelected())
        elif not note.isSelected():
            self._clear_selection()
            note.setSelected(True)
        if not note.isSelected():
            return

        self._grab_note = note
        edge = self.GRAB_PX / self._zoom_x
        # a press on either edge changes the duration: the left one moves the start, the right one the end
        if scene_pos.x() >= note.end - edge:
            self._mode, self._trim_edge = "trim", "end"
        elif scene_pos.x() <= note.start + edge:
            self._mode, self._trim_edge = "trim", "start"
        else:
            self._mode = "move"
        self._snapshot = {n: (n.start, n.pitch, n.duration) for n in self.selected_notes()}

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        scene_pos = self.mapToScene(pos)
        self.set_hover_pitch(self._pitch_at(scene_pos.y()))

        if self._mode is not None and self._mode != "pan":
            self._follow(pos, scene_pos)

        if self._mode is None:
            note = self._note_at(scene_pos) if self.edit_mode else None
            shift = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            edge = self.GRAB_PX / self._zoom_x
            if note is not None and (shift or scene_pos.x() >= note.end - edge or scene_pos.x() <= note.start + edge):
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

        if self._mode == "trim":
            start, pitch, duration = self._snapshot[self._grab_note]
            if self._trim_edge == "start":
                new_start = min(max(0.0, self._snap_beats(scene_pos.x())), start + duration - MIN_DURATION)
                self._grab_note.set_range(new_start, pitch)
                self._grab_note.set_duration(start + duration - new_start)
            else:
                self._grab_note.set_duration(self._snap_beats(scene_pos.x()) - start)
            self.view_changed.emit()
            return

        if self._mode == "draw":
            anchor = self._anchor.x()
            left = max(0.0, self._snap_floor_beats(min(anchor, scene_pos.x())))
            right = max(left + self._cell_beats(), self._snap_ceil_beats(max(anchor, scene_pos.x())))
            self._grab_note.set_range(left, self._pitch_at(scene_pos.y()))  # the row follows the pointer too
            self._grab_note.set_duration(right - left)
            return

        origin_start = self._snapshot[self._grab_note][0]
        delta_x = self._snap_beats(origin_start + scene_pos.x() - self._anchor.x()) - origin_start
        delta_row = round(scene_pos.y() - self._anchor.y())
        for note, (start, pitch, _duration) in self._snapshot.items():
            note.set_range(start + delta_x, pitch - delta_row)
        self.view_changed.emit()

    def _follow(self, pos: QPoint, scene_pos: QPointF) -> None:
        """A drag carries the playhead along and sounds every row it crosses, like a glissando."""
        if not self.playing:
            self.seek_requested.emit(self.seconds_at_viewport_x(pos.x()))
        pitch = self._pitch_at(scene_pos.y())
        if pitch != self._preview_pitch:
            self._preview_pitch = pitch
            self.note_preview.emit(pitch)

    def mouseReleaseEvent(self, event) -> None:
        if self._rubber.isVisible():
            self._rubber.hide()
        self._mode = None
        self._grab_note = None
        self._trim_edge = ""
        self._preview_pitch = None
        self._snapshot = {}
        self.viewport().unsetCursor()
        self._update_scene()
        self.view_changed.emit()

    def leaveEvent(self, event) -> None:
        self.set_hover_pitch(None)
        super().leaveEvent(event)

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
            vbar = self.verticalScrollBar()
            vbar.setValue(vbar.value() - delta)
        else:
            hbar = self.horizontalScrollBar()
            hbar.setValue(hbar.value() - delta)
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
        if not self.edit_mode and key in (
            Qt.Key.Key_Delete,
            Qt.Key.Key_Backspace,
            Qt.Key.Key_A,
        ):
            return
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
        self._press_x = 0.0
        self._moved = False
        self.setFixedHeight(RULER_HEIGHT)
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

        left = self.view.mapToScene(QPoint(0, 0)).x()
        right = self.view.mapToScene(QPoint(viewport.width(), 0)).x()
        visible = QRectF(left, 0.0, right - left, 1.0)

        stride = 1
        while stride * BAR_BEATS * self.view.pixels_per_beat() < MEASURE_LABEL_SPACING:
            stride *= 2
        for bar in range(int(left // BAR_BEATS), int(right // BAR_BEATS) + 1):
            px = left_offset + self.view.mapFromScene(QPointF(bar * BAR_BEATS, 0.0)).x()
            painter.setPen(QPen(GRID_BAR, 1))
            painter.drawLine(px, RULER_TIME_ROW, px, self.height())
            if bar % stride == 0:
                painter.setPen(TEXT)
                painter.drawText(px + 4, RULER_TIME_ROW + 15, str(bar + 1))

        for x, level in self.view.division_lines(visible, MIN_GRID_SPACING, snap=True):
            px = left_offset + self.view.mapFromScene(QPointF(x, 0.0)).x()
            painter.setPen(QPen(GRID_LINE if level == 0 else GRID_BEAT, 1))
            painter.drawLine(px, self.height() - (4, 8, 10)[level], px, self.height())

        for x, seconds, _major in self.view.seconds_lines(visible, TIME_LABEL_SPACING):
            px = left_offset + self.view.mapFromScene(QPointF(x, 0.0)).x()
            painter.setPen(QPen(GRID_BEAT, 1))
            painter.drawLine(px, RULER_TIME_ROW - 6, px, RULER_TIME_ROW - 1)
            label = format_time(seconds)
            painter.setPen(TEXT)
            painter.drawText(QPointF(px - painter.fontMetrics().horizontalAdvance(label) / 2, 14), label)

        painter.setPen(QPen(QColor("#3a4152"), 1))
        right_edge = viewport.width() + int(left_offset)
        painter.drawLine(int(left_offset), RULER_TIME_ROW - 1, right_edge, RULER_TIME_ROW - 1)
        painter.drawLine(int(left_offset), self.height() - 1, right_edge, self.height() - 1)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._last_x = event.position().x()
            self._press_x = self._last_x
            self._moved = False

    def mouseDoubleClickEvent(self, event) -> None:
        self.mousePressEvent(event)  # a quick second click on the ruler seeks like the first

    def mouseMoveEvent(self, event) -> None:
        position = event.position().x()
        if self._last_x is None:
            return
        if abs(position - self._press_x) > CLICK_SLOP_PX:
            self._moved = True
        hbar = self.view.horizontalScrollBar()
        hbar.setValue(hbar.value() - int(position - self._last_x))
        self._last_x = position

    def mouseReleaseEvent(self, event) -> None:
        if self._last_x is not None and not self._moved:  # a click moves the cursor, a drag scrolls
            x = event.position().x() - self.origin().x()
            self.view.seek_requested.emit(self.view.seconds_at_viewport_x(x))
        self._last_x = None

    def wheelEvent(self, event) -> None:
        hbar = self.view.horizontalScrollBar()
        hbar.setValue(hbar.value() - event.angleDelta().y())


class PianoKeyboard(QWidget):
    """The keys at the left of the roll; clicking one auditions that note."""

    key_preview = pyqtSignal(int)

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
        highlighted = set(self.view.highlight_pitches())

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
            if pitch in highlighted:
                painter.fillRect(QRect(0, top, self.width(), bottom - top), HOVER_KEY)
            if pitch % 12 == 0:
                painter.setPen(QColor("#454c5a"))
                painter.drawText(4, (top + bottom) // 2 + 3, note_name(pitch))

        painter.setPen(QPen(QColor("#101318"), 1))
        for pitch in range(PITCH_MIN, PITCH_MAX + 2):
            y = top_offset + self.view.mapFromScene(QPointF(0.0, float(PITCH_MAX - pitch))).y()
            painter.drawLine(0, y, self.width(), y)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.key_preview.emit(self._pitch_at(event.position().y()))

    def mouseDoubleClickEvent(self, event) -> None:
        self.mousePressEvent(event)  # a quick second click on a key sounds it again

    def _pitch_at(self, y: float) -> int:
        scene_y = self.view.mapToScene(QPoint(0, int(y) - self.origin().y())).y()
        return self.view._pitch_at(scene_y)

    def wheelEvent(self, event) -> None:
        vbar = self.view.verticalScrollBar()
        vbar.setValue(vbar.value() - event.angleDelta().y())
