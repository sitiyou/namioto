# SPDX-License-Identifier: AGPL-3.0-only
"""Piano-roll widgets: note items, grid background, interactive view, ruler and keyboard."""

from __future__ import annotations

import math
from contextlib import contextmanager
from dataclasses import dataclass

from PyQt6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen, QTransform, QUndoCommand, QUndoStack
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QMenu,
    QWidget,
)

from namioto.channels import Channel, free_channel
from namioto.document import MIN_DURATION, PITCH_COUNT, PITCH_MAX, PITCH_MIN, Document, Note
from namioto.i18n import tr
from namioto.interaction import Interaction, Tool
from namioto.karaoke.timeline import Mora, MoraLine, mora_ok
from namioto.spectrum import MIDI_OFFSET, NOTE_COUNT, NoteSpectrum
from namioto.ui import theme
from namioto.ui.spectrogram import SpectrumImage
from namioto.ui.text import format_time, note_name

LENGTH_BEATS = 64
CONTENT_MARGIN = 4.0
NOTE_INSET = 0.06
BAR_BEATS = 4.0
PAGE_TURN_MARGIN = 0.15  # how close to the right edge the playhead may get before the page turns
PAGE_TURN_LEAD = 0.2  # where on the fresh page the playhead then sits

SNAP_CHOICES = (("1/1", 4.0), ("1/2", 2.0), ("1/4", 1.0), ("1/8", 0.5), ("1/16", 0.25), ("1/32", 0.125))

# The colours are read while painting rather than once at import: a window outlives a theme switch,
# and a name read early would keep painting the theme that was in force when it was read.
SPECTRUM_TOP = PITCH_MAX - (MIDI_OFFSET + NOTE_COUNT - 1)
MIN_LINE_SPACING = 16.0
OVERTONES = (2, 3, 4)  # f, 2f, 3f and 4f: the four partials WaveTone marks over the row under the mouse
EDIT_DIM = 0.65  # the spectrum steps back while editing so the notes stand out over it
MIN_GRID_SPACING = 9.0
CLICK_SLOP_PX = 4
HISTORY_LIMIT = 50  # snapshots of the whole document, so the depth trades memory for how far back undo goes
NEW_CHANNEL = -1  # the channel menu's "make one" entry, since no channel number is negative
RULER_TIME_ROW = 24
RULER_HEIGHT = 46
MORA_HEIGHT = 44
MORA_GRAB_PX = 6
MORA_MIN_PX = 6
MORA_MARGIN = 5
TIME_LABEL_SPACING = 84.0
MEASURE_LABEL_SPACING = 30.0
TIME_STEPS = (0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 30.0, 60.0, 120.0, 300.0)


def is_black_key(pitch: int) -> bool:
    return pitch % 12 in (1, 3, 6, 8, 10)


def _is_multiple(value: float, step: float) -> bool:
    return abs(value / step - round(value / step)) < 1e-6


def time_step(pixels_per_second: float, minimum: float) -> float:
    """Smallest 1-2-5 step that keeps the time grid at least `minimum` pixels apart."""
    for step in TIME_STEPS:
        if step * pixels_per_second >= minimum:
            return step
    return TIME_STEPS[-1]


@dataclass(frozen=True)
class _RollState:
    """A document snapshot as one undo step: channels, notes and what was selected.

    Note times are seconds, not the beats the roll draws in: a tempo change rescales every beat
    coordinate, so a snapshot in beats would restore a note at the beat it used to sit on rather
    than the time it still sounds at.
    """

    channels: tuple[Channel, ...]
    notes: tuple[tuple[int, float, float, int], ...]
    selected: frozenset[int]
    active_channel: int
    lyrics: tuple = ()
    lyric_times: tuple = ()


def _state_data(state: _RollState) -> tuple:
    return (state.channels, state.notes, state.lyric_times)


class _RollEdit(QUndoCommand):
    """One undo step, holding the state before and after it and restoring either by rebuilding.

    `push` runs `redo` once with the edit already applied, so the first call stays a no-op and only
    a following undo/redo pair moves the document.
    """

    def __init__(self, view: PianoRollView, before: _RollState, after: _RollState, text: str):
        super().__init__(text)
        self._view = view
        self._before = before
        self._after = after
        self._applied = False

    def undo(self) -> None:
        self._view._restore_state(self._before)

    def redo(self) -> None:
        if self._applied:
            self._view._restore_state(self._after)
        self._applied = True


class NoteItem(QGraphicsRectItem):
    """The drawn body of one `Note`; scene units are beats (x) and semitone rows (y).

    It is a view of the note, not a copy: the data lives in `namioto.document`, and the item reads
    and writes it. The note's channel is also the stacking order: a higher channel draws over, and
    wins the hit test, the way noteDigger files its channels.
    """

    def __init__(self, note: Note):
        super().__init__()
        self.note = note
        self.fill, self.edge_light, self.edge_dark = theme.note_shades(QColor(theme.NOTE_PALETTE[0]))
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self._sync()

    @property
    def pitch(self) -> int:
        return self.note.pitch

    @property
    def start(self) -> float:
        return self.note.start

    @property
    def duration(self) -> float:
        return self.note.duration

    @property
    def channel(self) -> int:
        return self.note.channel

    @property
    def end(self) -> float:
        return self.note.end

    def set_duration(self, duration: float) -> None:
        self.note.set_duration(duration)
        self._sync()

    def set_range(self, start: float, pitch: int) -> None:
        self.note.set_range(start, pitch)
        self._sync()

    def _sync(self) -> None:
        self.setRect(0.0, 0.0, self.duration, 1.0 - 2 * NOTE_INSET)
        self.setPos(self.start, PITCH_MAX - self.pitch + NOTE_INSET)
        self.setZValue(self.channel)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        selected = self.isSelected()
        colors = theme.canvas()
        body = colors.note_selected if selected else self.fill
        light = colors.note_selected_edge if selected else self.edge_light
        dark = colors.note_selected_edge if selected else self.edge_dark
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


class _SelectionBox(QWidget):
    """The rectangle dragged over the roll, in the accent the palette carries.

    Painted, not styled: it belongs to the roll, which this app draws itself.
    """

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        accent = self.palette().highlight().color()
        fill = QColor(accent)
        fill.setAlpha(40)
        painter.fillRect(self.rect(), fill)
        painter.setPen(accent)
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))


class PianoRollView(QGraphicsView):
    """The roll: the note grid, the interaction with it and the drawn extras (spectrum, cursor)."""

    view_changed = pyqtSignal()
    notes_changed = pyqtSignal()
    channels_changed = pyqtSignal()
    active_channel_changed = pyqtSignal(int)
    hover_changed = pyqtSignal(object)
    note_preview = pyqtSignal(int)
    seek_requested = pyqtSignal(float)
    lyrics_changed = pyqtSignal()

    GRAB_PX = 7
    MIN_ZOOM_X, MAX_ZOOM_X = 12.0, 900.0
    MIN_ZOOM_Y, MAX_ZOOM_Y = 8.0, 64.0

    def __init__(self, parent=None):
        scene = QGraphicsScene()
        scene.setSceneRect(QRectF(0.0, 0.0, LENGTH_BEATS, PITCH_COUNT))
        super().__init__(scene, parent)
        self._scene = scene
        self.snap = 0.25
        self._interaction = Interaction.viewing()
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
        self._grid_offset = 0.0  # seconds the drawn grid slides by; the notes and the playhead keep their time
        self._mode: str | None = None
        self._anchor = QPointF()
        self._press_pos = QPoint()
        self._press_item: NoteItem | None = None  # the note a press landed on, while it stays a click
        self._press_at = QPoint()
        self._trim_edge = ""
        self._grab_note: NoteItem | None = None
        self._snapshot: dict[NoteItem, tuple[float, int, float]] = {}
        self.document = Document(channels=[Channel(channel=0, color=theme.NOTE_PALETTE[0])])
        self._items: list[NoteItem] = []
        self._clipboard: tuple[tuple[int, float, float, int], ...] = ()
        self.active_channel = 0
        self._stack = QUndoStack(self)
        self._stack.setUndoLimit(HISTORY_LIMIT)
        self._gesture_before: _RollState | None = None
        self._gesture_text = ""
        self._history_depth = 0
        self._lines: tuple[MoraLine, ...] = ()
        self._lyric_times: tuple[tuple[tuple[float | None, float | None], ...], ...] = ()

        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.viewport().setMouseTracking(True)  # the row under the mouse is highlighted
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setTransform(QTransform.fromScale(self._zoom_x, self._zoom_y))

        self._rubber = _SelectionBox(self.viewport())
        self._rubber.hide()
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

    def add_note(self, pitch: int, start: float, duration: float, channel: int | None = None) -> NoteItem:
        note = Note(pitch, start, duration, self.active_channel if channel is None else channel)
        with self._edit("Draw note"):
            self.document.add_note(note)
            item = self._add_item(note)
            self._update_scene()
            self.notes_changed.emit()
        return item

    def set_notes(self, notes) -> None:
        """Replace every note in one go: a project or an extraction arrives all at once.

        A note may carry a fourth element, the channel it plays on.
        """
        with self._edit("Replace notes"):
            self._drop_items()
            self.document.replace_notes(
                Note(item[0], item[1], item[2], item[3] if len(item) > 3 else 0) for item in notes
            )
            for note in self.document.notes:
                self._add_item(note)
            self._sync_channel_visuals()
            self._update_scene()
            self.notes_changed.emit()

    def replace(self, channels, notes, text: str = "Replace notes") -> None:
        """Put a whole document in over the running one - a MIDI import, say - as a single step."""
        with self._edit(text):
            self.set_channels(channels)
            self.set_notes(notes)

    def clear_notes(self) -> None:
        with self._edit("Clear notes"):
            self._drop_items()
            self.document.clear_notes()
            self._update_scene()
            self.notes_changed.emit()
            self.view_changed.emit()

    # --- lyrics -----------------------------------------------------------

    @property
    def lyric_lines(self) -> tuple[MoraLine, ...]:
        return self._lines

    @property
    def lyric_times(self) -> tuple[tuple[tuple[float | None, float | None], ...], ...]:
        return self._lyric_times

    def set_lyrics(self, lines, times) -> None:
        """Take a whole aligned `.krc` over: every line's morae and their times, as one step."""
        with self._edit("Align lyrics"):
            self.load_lyrics(lines, times)

    def load_lyrics(self, lines, times) -> None:
        """The lyrics a `.krc` or a project brings in, with no undo step of their own."""
        self._lines = tuple(lines)
        self._lyric_times = tuple(tuple(span) for span in times)
        self.lyrics_changed.emit()
        self.view_changed.emit()

    def set_mora_span(self, row: int, column: int, start: float, end: float, mode: str = "move", base=None) -> None:
        """Put one mora's block between `start` and `end`, the way a note is moved or trimmed.

        The morae of a line keep the order they were read in and never overlap, so a block stops at
        a neighbour's far edge rather than crossing it. `mode` names what the pointer holds: a
        trimmed edge takes room from the neighbour it reaches, that neighbour's near edge giving way
        and the rest of the line staying where it was, while a block dragged by its middle keeps its
        length and needs the room to be free - it stays put when it is not.

        `base` is the line as a drag found it; every move of that drag is worked out from there, so
        dragging back to where it started leaves the line exactly as it was.
        """
        if not 0 <= row < len(self._lyric_times):
            return
        spans = list(self._lyric_times[row] if base is None else base)
        if not 0 <= column < len(spans):
            return
        if start is None or end is None:
            return
        start, end = max(0.0, start), max(start, end)
        previous, following = _mora_edge(spans, column, -1), _mora_edge(spans, column, 1)
        if mode == "move":
            length = end - start
            low, high = _mora_room(spans, column, column)
            if length > high - low:
                return
            start = min(max(start, low), high - length)
            end = start + length
        elif mode == "left" and previous is not None and start < spans[previous][1]:
            start = max(start, spans[previous][0])
            spans[previous] = (spans[previous][0], start)
        elif mode == "right" and following is not None and end > spans[following][0]:
            end = min(end, spans[following][1])
            spans[following] = (end, spans[following][1])
        spans[column] = (start, max(start, end))
        self._settle_morae(spans)
        self._store_morae(row, spans)

    def move_morae(self, row: int, first: int, last: int, delta: float, base=None) -> bool:
        """Shift the run `first..last` of a line by `delta` seconds, and say whether it moved.

        The run keeps its own shape; the room it may move in is what is free around it, so a line
        that packs its morae together lets nothing through. `base`, when a drag hands it over, is the
        line as that drag found it, so the shift is measured from there rather than from the last
        place the pointer was.
        """
        if not 0 <= row < len(self._lyric_times):
            return False
        spans = list(self._lyric_times[row] if base is None else base)
        if not 0 <= first <= last < len(spans):
            return False
        moved = [span for span in spans[first : last + 1] if None not in span]
        if not moved:
            return False
        low, high = _mora_room(spans, first, last)
        start = min(span[0] for span in moved)
        end = max(span[1] for span in moved)
        if end - start > high - low:
            return False
        delta = min(max(delta, low - start), high - end)
        for index in range(first, last + 1):
            if None in spans[index]:
                continue
            spans[index] = (spans[index][0] + delta, spans[index][1] + delta)
        self._settle_morae(spans)
        self._store_morae(row, spans)
        return True

    def restore_mora(self, row: int, column: int) -> bool:
        """Give a mora of no length the shortest one can be, and say whether it got one.

        The room in front of it comes first; with none there, the cell is taken out of the block it
        follows, which gives up that much and, when it had only a cell to start with, is left with
        no length at all - a mora nothing is sung on, and a block nothing is drawn for.
        """
        if not 0 <= row < len(self._lyric_times):
            return False
        spans = list(self._lyric_times[row])
        if not 0 <= column < len(spans):
            return False
        start, end = spans[column]
        if start is None or end is None or end > start:
            return False
        minimum = self._cell_beats() * self.seconds_per_beat
        low, _high = _mora_room(spans, column, column)
        if start - low >= minimum:
            self.set_mora_span(row, column, start - minimum, start)
        else:
            self.set_mora_span(row, column, start, start + minimum, "right")
        return True

    def _store_morae(self, row: int, spans: list) -> None:
        self._lyric_times = self._lyric_times[:row] + (tuple(spans),) + self._lyric_times[row + 1 :]
        self.lyrics_changed.emit()

    def _settle_morae(self, spans: list) -> None:
        """A mora of no length is a point on the line, and the point rides on the mora after it."""
        for index in range(len(spans) - 2, -1, -1):
            start, end = spans[index]
            after = spans[index + 1][0]
            if start is not None and start == end and after is not None:
                spans[index] = (after, after)

    def note_seconds(self) -> list[tuple[float, float]]:
        per_beat = self.seconds_per_beat
        return [(note.start * per_beat, note.end * per_beat) for note in self.notes()]

    def notes(self) -> list[NoteItem]:
        return list(self._items)

    def _add_item(self, note: Note) -> NoteItem:
        item = NoteItem(note)
        item.fill, item.edge_light, item.edge_dark = theme.note_shades(self._channel_color(note.channel))
        channel = self._channel(note.channel)
        item.setVisible(self.edit_mode and (channel.visible if channel else True))
        self._scene.addItem(item)
        self._items.append(item)
        return item

    def _drop_item(self, item: NoteItem) -> None:
        self._scene.removeItem(item)
        self._items.remove(item)

    def _drop_items(self) -> None:
        for item in list(self._items):
            self._scene.removeItem(item)
        self._items.clear()

    # --- history ----------------------------------------------------------

    @property
    def undo_stack(self) -> QUndoStack:
        return self._stack

    def undo(self) -> None:
        self._stack.undo()

    def redo(self) -> None:
        self._stack.redo()

    def _capture(self) -> _RollState:
        per_beat = self.seconds_per_beat
        return _RollState(
            channels=tuple(self.channels),
            notes=tuple(
                (note.pitch, note.start * per_beat, note.duration * per_beat, note.channel) for note in self.notes()
            ),
            selected=frozenset(index for index, item in enumerate(self._items) if item.isSelected()),
            active_channel=self.active_channel,
            lyrics=self._lines,
            lyric_times=self._lyric_times,
        )

    def _push(self, before: _RollState, after: _RollState, text: str) -> bool:
        """Record one step, and say whether it changed anything worth a step."""
        if _state_data(before) == _state_data(after):
            return False
        self._stack.push(_RollEdit(self, before, after, text))
        return True

    @contextmanager
    def _edit(self, text: str):
        """Record one discrete edit as one step; nested edits join the outer one, and a gesture
        records itself in `_commit_gesture`."""
        if self._history_depth or self._gesture_before is not None:
            yield
            return
        before = self._capture()
        self._history_depth += 1
        try:
            yield
        finally:
            self._history_depth -= 1
        self._push(before, self._capture(), text)

    def _begin_gesture(self, text: str) -> None:
        if self._gesture_before is None:
            self._gesture_before = self._capture()
            self._gesture_text = text

    def _commit_gesture(self) -> None:
        before, text = self._gesture_before, self._gesture_text
        if before is None:
            return
        self._gesture_before = None
        self._gesture_text = ""
        if self._push(before, self._capture(), text):
            self.notes_changed.emit()  # a move or a trim is a change of the notes like any other

    def _restore_state(self, state: _RollState) -> None:
        """Put a snapshot back in one rebuild, selecting the same notes by position again.

        The notes go in first: a channel list only holds the numbers its notes play on, so a channel
        that is about to lose its notes would come straight back if it were set while they were
        still there.
        """
        self._history_depth += 1
        try:
            per_beat = self.seconds_per_beat
            self.set_notes(
                (pitch, start / per_beat, duration / per_beat, channel)
                for pitch, start, duration, channel in state.notes
            )
            self.set_channels(state.channels)
            for index in state.selected:
                if index < len(self._items):
                    self._items[index].setSelected(True)
            self.set_active_channel(state.active_channel)
            self._lines = state.lyrics
            self._lyric_times = state.lyric_times
            self.lyrics_changed.emit()
        finally:
            self._history_depth -= 1
        self.refresh()

    # --- channels ---------------------------------------------------------

    @property
    def channels(self) -> list[Channel]:
        """The document's channel list; the panel and the players read it, nothing holds a copy."""
        return self.document.channels

    def _channel(self, number: int) -> Channel | None:
        return next((channel for channel in self.channels if channel.channel == number), None)

    def _channel_color(self, number: int) -> QColor:
        channel = self._channel(number)
        color = QColor(channel.color) if channel is not None else QColor()
        if not color.isValid():
            color = QColor(theme.NOTE_PALETTE[number % len(theme.NOTE_PALETTE)])
        return color

    def _sync_channel_visuals(self) -> None:
        """Body colour, bevel, stacking and visibility all come from the channel list; the notes only
        show while editing, the way WaveTone keeps its graph to the spectrum outside note edit mode."""
        for note in self.notes():
            note.fill, note.edge_light, note.edge_dark = theme.note_shades(self._channel_color(note.channel))
            channel = self._channel(note.channel)
            note.setVisible(self.edit_mode and (channel.visible if channel else True))
            note.setZValue(note.channel)

    def set_channels(self, channels) -> None:
        """Replace the channel list in one go, the way a project hands it over.

        A note whose channel is missing from the list gets a plain entry back rather than being
        dropped: a channel number is always a place a note can play on.
        """
        with self._edit("Set channels"):
            self.document.set_channels(channels or [Channel(channel=0, color=theme.NOTE_PALETTE[0])])
            for channel in list(self.channels):
                if not channel.color:
                    self.document.set_channel_field(
                        channel.channel, color=theme.NOTE_PALETTE[channel.channel % len(theme.NOTE_PALETTE)]
                    )
            if self.active_channel not in {channel.channel for channel in self.channels}:
                self.active_channel = self.channels[0].channel
            self._sync_channel_visuals()
            self.channels_changed.emit()
            self.active_channel_changed.emit(self.active_channel)

    def add_channel(self, program: int = 0) -> Channel | None:
        number = free_channel(self.channels)
        if number is None:
            return None
        channel = Channel(channel=number, color=theme.NOTE_PALETTE[number % len(theme.NOTE_PALETTE)], program=program)
        with self._edit("Add channel"):
            self.document.add_channel(channel)
            self.channels_changed.emit()
        return channel

    def remove_channel(self, number: int) -> bool:
        """Drop a channel and the notes on it; the other numbers stay as they are. The last one stays."""
        if len(self.channels) <= 1:
            return False
        with self._edit("Delete channel"):
            removed = self.document.remove_channel(number)
            gone = {id(note) for note in removed or ()}
            for item in list(self._items):
                if id(item.note) in gone:
                    self._drop_item(item)
            if self.active_channel not in {channel.channel for channel in self.channels}:
                self.active_channel = self.channels[0].channel
            self._sync_channel_visuals()
            if removed:
                self.notes_changed.emit()
            self.channels_changed.emit()
            self.active_channel_changed.emit(self.active_channel)
        return True

    def set_channel_field(self, number: int, **fields) -> None:
        with self._edit("Edit channel"):
            self.document.set_channel_field(number, **fields)
            self._sync_channel_visuals()
            self.channels_changed.emit()

    def set_channel_number(self, old: int, new: int) -> bool:
        """Move a channel and its notes onto another MIDI channel; its colour follows the number."""
        with self._edit("Change channel"):
            if not self.document.set_channel_number(old, new):
                return False
            self.document.set_channel_field(new, color=theme.NOTE_PALETTE[new % len(theme.NOTE_PALETTE)])
            if self.active_channel == old:
                self.active_channel = new
            self._sync_channel_visuals()
            self.notes_changed.emit()
            self.channels_changed.emit()
            self.active_channel_changed.emit(self.active_channel)
        return True

    def set_active_channel(self, number: int) -> None:
        if number in {channel.channel for channel in self.channels} and number != self.active_channel:
            self.active_channel = number
            self.active_channel_changed.emit(number)

    def move_selection_to_channel(self, number: int | None) -> bool:
        """File the selected notes under a channel; None makes a fresh one for them instead.

        Only what the lock leaves alone moves, the notes keep their place and take the target's
        colour and stacking order, and creating the channel is part of the same undo step.
        """
        if not self.edit_mode:
            return False
        selection = [item for item in self.selected_notes() if not self._locked(item.channel)]
        if not selection:
            return False
        if number is not None and self._locked(number):
            return False
        with self._edit("Move notes to channel"):
            if number is None:
                created = self.add_channel()
                if created is None:
                    return False
                number = created.channel
            elif self._channel(number) is None:
                return False
            for item in selection:
                item.note.channel = number
            self._sync_channel_visuals()
            self.notes_changed.emit()
            self.view_changed.emit()
        return True

    def _locked(self, number: int) -> bool:
        channel = self._channel(number)
        return channel.lock if channel is not None else False

    def selected_notes(self) -> list[NoteItem]:
        return [note for note in self.notes() if note.isSelected()]

    def copy_selection(self) -> bool:
        """Take the selected notes as one block, timed from the earliest of them."""
        if not self.edit_mode:
            return False
        selection = sorted(self.selected_notes(), key=lambda note: note.start)
        if not selection:
            return False
        anchor = selection[0].start
        self._clipboard = tuple((note.pitch, note.start - anchor, note.duration, note.channel) for note in selection)
        return True

    def paste_notes(self) -> bool:
        """Drop the copied block at the playhead, its earliest note on the snapped grid and the
        spacing between them quantised to the same cell, so the block arrives on the current snap.
        """
        if not self.edit_mode or not self._clipboard:
            return False
        known = {channel.channel for channel in self.channels}
        anchor = self._snap_beats((self.playhead or 0.0) * self.bpm / 60.0)
        with self._edit("Paste notes"):
            self._clear_selection()
            for pitch, offset, duration, channel in self._clipboard:
                place = channel if channel in known else self.active_channel
                note = Note(pitch, anchor + self._snap_beats(offset), duration, place)
                self.document.add_note(note)
                self._add_item(note).setSelected(True)
            self._update_scene()
            self.notes_changed.emit()
            self.view_changed.emit()
        return True

    def quantize_notes(self) -> bool:
        """Put the starts and ends of the notes on the snap grid, so they sit on the current beats.

        The selection when there is one and the whole roll otherwise; a locked channel is never
        touched; a note that rounds away keeps one cell. The grid is a setting and this is the one
        command that applies it, so both stay usable outside edit mode.
        """
        cell = self._cell_beats()
        targets = [note for note in (self.selected_notes() or self.notes()) if not self._locked(note.channel)]
        moved = False
        with self._edit("Quantize notes"):
            for note in targets:
                start = self._snap_beats(note.start)
                duration = max(self._snap_beats(note.end), start + cell) - start
                if (start, duration) == (note.start, note.duration):
                    continue
                note.set_range(start, note.pitch)
                note.set_duration(duration)
                moved = True
            if moved:
                self.notes_changed.emit()
                self.view_changed.emit()
        return moved

    def set_spectrum(self, spectrum: NoteSpectrum | None) -> None:
        self.spectrum = SpectrumImage(spectrum) if spectrum is not None else None
        self._update_scene()
        self.refresh()

    def refresh(self) -> None:
        self.viewport().update()
        self.view_changed.emit()

    def canvas(self) -> theme.Canvas:
        """The colours the roll paints with: the fixed dark set once a spectrum covers it, else the
        desktop's own light or dark."""
        return theme.canvas("dark") if self.spectrum is not None else theme.canvas()

    @property
    def interaction(self) -> Interaction:
        return self._interaction

    @property
    def edit_mode(self) -> bool:
        return self._interaction.editing

    @property
    def tool(self) -> Tool | None:
        return self._interaction.tool

    def apply_interaction(self, state: Interaction) -> None:
        """The one place a mode and tool land: it decides what is drawn and how it answers."""
        self._interaction = state
        self._sync_channel_visuals()
        self.refresh()

    def frame_width(self) -> float:
        """Scene width of one spectrum frame, in beats."""
        return self.spectrum.spectrum.frame_ms / 1000.0 * self.bpm / 60.0

    def highlight_pitches(self) -> list[int]:
        """The rows to tint behind the notes: the row under the mouse, and - with the overtone
        highlight on - f, 2f, 3f and 4f above it, the WaveTone hint about where a note would double."""
        if self.hover_pitch is None:
            return []
        if not self.overtone_highlight:
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

    def follow_playhead(self, seconds: float) -> None:
        """Leave the page alone until the playhead reaches its right edge, then turn it."""
        x = seconds * self.bpm / 60.0
        page = self.mapToScene(self.viewport().rect()).boundingRect()
        if page.left() <= x <= page.right() - page.width() * PAGE_TURN_MARGIN:
            return
        self.centerOn(x + page.width() * PAGE_TURN_LEAD, page.center().y())

    @property
    def seconds_per_beat(self) -> float:
        """What one scene unit is worth in time: the one conversion every caller has to get right."""
        return 60.0 / self.bpm

    @property
    def offset(self) -> float:
        """How far the drawn grid slides, in seconds; `+` to the right, `-` to the left."""
        return self._grid_offset

    @property
    def offset_beats(self) -> float:
        """Where the grid's own zero sits, in scene units: `set_offset`'s seconds as beats."""
        return self._grid_offset * self.bpm / 60.0

    def set_offset(self, seconds: float) -> None:
        """Slide the drawn grid and its snapping by `seconds`: `+` to the right, `-` to the left.

        Only the grid moves - the notes, the spectrum and the playhead stay where their timestamps
        put them.
        """
        self._grid_offset = float(seconds)
        self.refresh()

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
        offset = self.offset_beats
        value = math.floor((rect.left() - offset) / (step * beats_per_second)) * step
        lines = []
        while value * beats_per_second + offset <= rect.right():
            lines.append((value * beats_per_second + offset, value, _is_multiple(value, major)))
            value += step
        return lines

    def division_lines(self, rect: QRectF, minimum: float, snap: bool = False) -> list[tuple[float, int]]:
        """Time-axis grid lines across `rect` as (x, level) pairs, 0 minor, 1 beat, 2 prominent.

        The division picks the basis: beats of the tempo map, or seconds. `snap` follows the snap
        grid on the beats basis, for the canvas that has no spectrum behind it.
        """
        if self.division == "seconds":
            return [(x, 2 if major else 0) for x, _seconds, major in self.seconds_lines(rect, minimum)]
        offset = self.offset_beats
        step = self.grid_step() if snap else 1.0
        value = math.floor((rect.left() - offset) / step) * step + offset
        lines = []
        while value <= rect.right():
            grid = value - offset
            level = 2 if _is_multiple(grid, BAR_BEATS) else 1 if _is_multiple(grid, 1.0) else 0
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
        colors = self.canvas()

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)  # keeps 1px grid lines on one pixel

        if self.spectrum is None:
            painter.fillRect(rect, colors.background)
            for row in range(first_row, last_row):
                color = colors.row_black if is_black_key(PITCH_MAX - row) else colors.row_white
                painter.fillRect(QRectF(rect.left(), float(row), rect.width(), 1.0), color)
            painter.setPen(QPen(colors.grid_line, 0))
            for row in range(first_row, last_row + 1):
                y = float(row)
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            for x, level in self.division_lines(rect, MIN_GRID_SPACING, snap=True):
                painter.setPen(QPen((colors.grid_line, colors.grid_beat, colors.grid_bar)[level], 0))
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            self._draw_cursor(painter, rect)
            painter.restore()
            return

        painter.fillRect(rect, colors.spectrum_background)
        self._draw_spectrum(painter, rect)
        painter.setPen(QPen(colors.spectrum_octave, 0))
        for pitch in range(MIDI_OFFSET, MIDI_OFFSET + NOTE_COUNT + 1, 12):
            y = float(PITCH_MAX - pitch + 1)  # the C row's lower edge (B sits below C)
            if first_row <= y <= last_row:
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        for x, level in self.division_lines(rect, MIN_LINE_SPACING):
            painter.setPen(QPen(colors.spectrum_bar if level == 2 else colors.spectrum_beat, 0))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        self._draw_cursor(painter, rect)
        painter.restore()

    def _draw_cursor(self, painter: QPainter, rect: QRectF) -> None:
        """The rows highlighted under the mouse; the playback position goes over the notes."""
        painter.setPen(Qt.PenStyle.NoPen)
        colors = self.canvas()
        # the spectrum is dark in either canvas, so the row over it takes the band that shows there
        band = colors.spectrum_hover_band if self.spectrum is not None else colors.hover_band
        painter.setBrush(band)
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
        painter.setPen(QPen(self.canvas().playhead, 0))
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

    def pitch_at(self, y: float) -> int:
        row = min(PITCH_COUNT - 1, max(0, int(math.floor(y))))
        return PITCH_MAX - row

    def _cell_beats(self) -> float:
        """One snap cell, but never shorter than a note can be."""
        return max(self.snap, MIN_DURATION)

    def _snap_beats(self, x: float) -> float:
        offset = self.offset_beats
        return round((x - offset) / self.snap) * self.snap + offset

    def _snap_floor_beats(self, x: float) -> float:
        offset = self.offset_beats
        return math.floor((x - offset) / self.snap) * self.snap + offset

    def _snap_ceil_beats(self, x: float) -> float:
        offset = self.offset_beats
        return math.ceil((x - offset) / self.snap) * self.snap + offset

    def _clear_selection(self) -> None:
        for note in self.notes():
            note.setSelected(False)

    def channel_menu(self, scene_pos: QPointF) -> QMenu | None:
        """The menu that moves the selected notes onto another channel, a fresh one included.

        A right click over an unselected note selects it first, so a note can be filed without a
        trip to the select tool. Nothing comes back outside edit mode or with nothing to move.
        """
        if not self.edit_mode:
            return None
        note = self._note_at(scene_pos)
        if note is not None and not note.isSelected() and not self._locked(note.channel):
            self._clear_selection()
            note.setSelected(True)
        selection = [item for item in self.selected_notes() if not self._locked(item.channel)]
        if not selection:
            return None
        current = {item.channel for item in selection}
        menu = QMenu(self)
        others = [channel for channel in self.channels if channel.channel not in current and not channel.lock]
        for channel in others:
            menu.addAction(channel.label).setData(channel.channel)
        if others:
            menu.addSeparator()
        new = menu.addAction(tr("New channel"))
        new.setData(NEW_CHANNEL)
        new.setEnabled(free_channel(self.channels) is not None)
        return menu

    def contextMenuEvent(self, event) -> None:
        menu = self.channel_menu(self.mapToScene(event.pos()))
        if menu is None:
            return
        action = menu.exec(event.globalPos())
        if action is not None:
            number = action.data()
            self.move_selection_to_channel(None if number == NEW_CHANNEL else number)

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
            self._preview_pitch = self.pitch_at(scene_pos.y())
            self.note_preview.emit(self._preview_pitch)
            if not self.edit_mode:
                self._mode = "seek"  # dragging on, the playhead is what follows the pointer
                return

        if event.button() == Qt.MouseButton.RightButton:
            # the context menu is what a right click opens; without this the scene would drop the
            # selection before `contextMenuEvent` ever sees it
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        note = self._note_at(scene_pos)
        self._press_item = None  # set again below when the press lands on a note that is selected
        modifier = event.modifiers()
        ctrl = bool(modifier & Qt.KeyboardModifier.ControlModifier)
        shift = bool(modifier & Qt.KeyboardModifier.ShiftModifier)
        self._anchor = scene_pos
        if note is not None and self._locked(note.channel):
            return
        if note is not None:
            self.set_active_channel(note.channel)  # picking a note files the pen under its channel

        if note is None:
            if ctrl or self.tool is Tool.SELECT:
                if not shift:
                    self._clear_selection()
                self._mode = "select"
                self._rubber_origin = pos
                self._rubber.setGeometry(QRect(pos, pos))
                self._rubber.show()
                return
            if self._locked(self.active_channel):
                return
            pitch = self.pitch_at(scene_pos.y())
            start = max(0.0, self._snap_floor_beats(scene_pos.x()))
            self._begin_gesture("Draw note")
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
            self._begin_gesture("Trim note")
            return

        if ctrl:
            note.setSelected(not note.isSelected())
        elif not note.isSelected():
            self._clear_selection()
            note.setSelected(True)
        else:
            # the press keeps the selection a drag would carry; if it stays a click, the release
            # narrows it to this one note
            self._press_item = note
            self._press_at = pos
            for item in self.selected_notes():
                if item.channel != note.channel:
                    item.setSelected(False)
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
        self._begin_gesture("Trim note" if self._mode == "trim" else "Move notes")

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        scene_pos = self.mapToScene(pos)
        if self._press_item is not None and (pos - self._press_at).manhattanLength() > CLICK_SLOP_PX:
            self._press_item = None  # the press turned into a drag, so the release is not a click
        self.set_hover_pitch(self.pitch_at(scene_pos.y()))

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
                # only the channel being edited answers a frame, the way noteDigger frames its channels
                if (
                    isinstance(item, NoteItem)
                    and item.channel == self.active_channel
                    and not self._locked(item.channel)
                ):
                    item.setSelected(True)
            return

        if self._grab_note is None or self._grab_note not in self._snapshot:
            return

        if self._mode == "trim":
            start, pitch, duration = self._snapshot[self._grab_note]
            cell = self._cell_beats()  # one snap cell is the shortest the pointer may leave behind
            if self._trim_edge == "start":
                new_start = min(max(0.0, self._snap_beats(scene_pos.x())), start + duration - cell)
                self._grab_note.set_range(new_start, pitch)
                self._grab_note.set_duration(start + duration - new_start)
            else:
                self._grab_note.set_duration(max(self._snap_beats(scene_pos.x()), start + cell) - start)
            self.view_changed.emit()
            return

        if self._mode == "draw":
            anchor = self._anchor.x()
            left = max(0.0, self._snap_floor_beats(min(anchor, scene_pos.x())))
            right = max(left + self._cell_beats(), self._snap_ceil_beats(max(anchor, scene_pos.x())))
            self._grab_note.set_range(left, self.pitch_at(scene_pos.y()))  # the row follows the pointer too
            self._grab_note.set_duration(right - left)
            return

        # the movement is what snaps, not the note's place on the grid: the step is always a whole
        # cell, after the same half cell of dragging, and a note sits off the grid stays off it
        moved = scene_pos.x() - self._anchor.x()
        delta_x = round(moved / self.snap) * self.snap
        delta_row = round(scene_pos.y() - self._anchor.y())
        for note, (start, pitch, _duration) in self._snapshot.items():
            note.set_range(start + delta_x, pitch - delta_row)
        self.view_changed.emit()

    def _follow(self, pos: QPoint, scene_pos: QPointF) -> None:
        """A drag carries the playhead along and sounds every row it crosses, like a glissando."""
        if not self.playing:
            self.seek_requested.emit(self.seconds_at_viewport_x(pos.x()))
        pitch = self.pitch_at(scene_pos.y())
        if pitch != self._preview_pitch:
            self._preview_pitch = pitch
            self.note_preview.emit(pitch)

    def mouseReleaseEvent(self, event) -> None:
        self._commit_gesture()
        if self._press_item is not None:
            # the press stayed a click, and a click is what picks the one note out of the selection
            self._clear_selection()
            self._press_item.setSelected(True)
            self._press_item = None
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
            with self._edit("Delete notes"):
                for item in selection:
                    self.document.remove_note(item.note)
                    self._drop_item(item)
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


def _mora_edge(spans: list, column: int, step: int) -> int | None:
    """The nearest block drawn beside `column`: a mora of no length is a point the line reads past."""
    for index in range(column + step, len(spans) if step > 0 else -1, step):
        start, end = spans[index]
        if start is not None and end is not None and end > start:
            return index
    return None


def _mora_room(spans: list, first: int, last: int) -> tuple[float, float]:
    """The room a run of a line may move in: what is free around it, its own morae aside.

    A mora of no length takes up none of it, so the room runs to the nearest block that is drawn
    rather than to the neighbour that happens to stand next in the line.
    """
    low, high = 0.0, math.inf
    before, after = _mora_edge(spans, first, -1), _mora_edge(spans, last, 1)
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


class TimelineRuler(_ViewportStrip):
    def __init__(self, view: PianoRollView):
        super().__init__(view)
        self._last_x: float | None = None
        self._press_x = 0.0
        self._moved = False
        self.setFixedHeight(RULER_HEIGHT)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        view.view_changed.connect(self.update)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        colors = theme.canvas()
        painter.fillRect(self.rect(), colors.panel)
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
        offset = self.view.offset_beats
        for bar in range(int((left - offset) // BAR_BEATS), int((right - offset) // BAR_BEATS) + 1):
            px = left_offset + self.view.mapFromScene(QPointF(bar * BAR_BEATS + offset, 0.0)).x()
            painter.setPen(QPen(colors.grid_bar, 1))
            painter.drawLine(px, RULER_TIME_ROW, px, self.height())
            if bar % stride == 0:
                painter.setPen(colors.text)
                painter.drawText(px + 4, RULER_TIME_ROW + 15, str(bar + 1))

        for x, level in self.view.division_lines(visible, MIN_GRID_SPACING, snap=True):
            px = left_offset + self.view.mapFromScene(QPointF(x, 0.0)).x()
            painter.setPen(QPen(colors.grid_line if level == 0 else colors.grid_beat, 1))
            painter.drawLine(px, self.height() - (4, 8, 10)[level], px, self.height())

        for x, seconds, _major in self.view.seconds_lines(visible, TIME_LABEL_SPACING):
            px = left_offset + self.view.mapFromScene(QPointF(x, 0.0)).x()
            painter.setPen(QPen(colors.grid_beat, 1))
            painter.drawLine(px, RULER_TIME_ROW - 6, px, RULER_TIME_ROW - 1)
            label = format_time(seconds)
            painter.setPen(colors.text)
            painter.drawText(QPointF(px - painter.fontMetrics().horizontalAdvance(label) / 2, 14), label)

        painter.setPen(QPen(colors.ruler_line, 1))
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
        event.accept()


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
            low, high = _mora_room(lines, first, last)
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


class PianoKeyboard(_ViewportStrip):
    """The keys at the left of the roll; clicking one auditions that note."""

    key_preview = pyqtSignal(int)

    def __init__(self, view: PianoRollView):
        super().__init__(view)
        self.setFixedWidth(66)
        view.view_changed.connect(self.update)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        colors = theme.canvas("light")  # a piano's keys stay white, whatever the desktop or the spectrum
        painter.fillRect(self.rect(), colors.panel)
        viewport = self.view.viewport()
        top_offset = self.origin().y()
        painter.setClipRect(QRect(0, int(top_offset), self.width(), viewport.height()))
        font = QFont()
        font.setPixelSize(9)
        painter.setFont(font)
        white = colors.key_white
        black = colors.key_black
        highlighted = set(self.view.highlight_pitches())

        for pitch in range(PITCH_MIN, PITCH_MAX + 1):
            row = PITCH_MAX - pitch
            top = top_offset + self.view.mapFromScene(QPointF(0.0, float(row))).y()
            bottom = top_offset + self.view.mapFromScene(QPointF(0.0, float(row) + 1.0)).y()
            if bottom < top_offset or top > viewport.height() + top_offset:
                continue
            color = black if is_black_key(pitch) else white
            painter.fillRect(QRect(0, top, self.width(), bottom - top), color)
            if pitch in highlighted:
                painter.fillRect(QRect(0, top, self.width(), bottom - top), colors.hover_key)
            if pitch % 12 == 0:
                painter.setPen(colors.key_text)
                painter.drawText(4, (top + bottom) // 2 + 3, note_name(pitch))

        painter.setPen(QPen(colors.key_line, 1))
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
        return self.view.pitch_at(scene_y)

    def wheelEvent(self, event) -> None:
        vbar = self.view.verticalScrollBar()
        vbar.setValue(vbar.value() - event.angleDelta().y())
        event.accept()
