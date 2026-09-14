# SPDX-License-Identifier: AGPL-3.0-only
"""Layout checks for the control bars and the main window."""

from __future__ import annotations

import time

import numpy as np
import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QFocusEvent, QImage, QKeyEvent, QMouseEvent, QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QSlider

from namioto.beats import BeatTempo, LocalWindow
from namioto.spectrum import MIDI_OFFSET, NOTE_COUNT, NoteSpectrum
from namioto.ui.app import STYLE_SHEET, MainWindow, TempoLoader, dark_palette
from namioto.ui.audio import MidiPortOut, MidiSink, find_synth_port
from namioto.ui.controls import WEAK_COLOR, Cluster, ValueSlider
from namioto.ui.roll import (
    CONTENT_MARGIN,
    GRID_BAR,
    GRID_BEAT,
    GRID_LINE,
    HOVER_KEY,
    LENGTH_BEATS,
    MIN_DURATION,
    NOTE_EDGE_DARK,
    NOTE_EDGE_LIGHT,
    NOTE_FILL,
    NOTE_INSET,
    NOTE_SELECTED,
    NOTE_SELECTED_EDGE,
    PANEL,
    PITCH_MAX,
    PLAYHEAD,
    RULER_HEIGHT,
    RULER_TIME_ROW,
    SPECTRUM_BAR,
    SPECTRUM_BEAT,
    SPECTRUM_OCTAVE,
    SPECTRUM_TOP,
    PianoRollView,
)
from namioto.ui.spectrogram import SpectrumImage, SpectrumLoader

DEMO_NOTES = (
    (60, 0.0, 1.0),
    (64, 1.0, 1.0),
    (67, 2.0, 1.0),
    (72, 3.0, 1.0),
    (67, 4.0, 0.5),
    (69, 4.5, 0.5),
    (71, 5.0, 1.5),
    (64, 6.5, 0.5),
    (67, 7.0, 1.0),
    (55, 0.0, 8.0),
    (62, 0.0, 8.0),
)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setStyleSheet(STYLE_SHEET)
    return app


@pytest.fixture(scope="module")
def window(qt_app):
    window = MainWindow()
    window.resize(1200, 720)
    window.show()
    for pitch, start, duration in DEMO_NOTES:
        window.view.add_note(pitch, start, duration)
    qt_app.processEvents()
    yield window
    window.close()


def roll_mouse(window, kind, scene_pos: QPointF, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    """Send a mouse event to the roll at a scene position, as a real click would arrive."""
    position = window.view.mapFromScene(scene_pos)
    event = QMouseEvent(
        kind,
        QPointF(position),
        window.view.viewport().mapToGlobal(QPointF(position)),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        modifiers,
    )
    if kind == QEvent.Type.MouseButtonPress:
        window.view.mousePressEvent(event)
    elif kind == QEvent.Type.MouseButtonDblClick:
        window.view.mouseDoubleClickEvent(event)
    elif kind == QEvent.Type.MouseMove:
        window.view.mouseMoveEvent(event)
    else:
        window.view.mouseReleaseEvent(event)


def ruler_mouse(window, kind, x: float) -> None:
    """Send a mouse event to the timeline ruler, whose columns line up with the roll's."""
    position = QPointF(x, 10.0)
    event = QMouseEvent(
        kind,
        position,
        window.ruler.mapToGlobal(position),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    if kind == QEvent.Type.MouseButtonPress:
        window.ruler.mousePressEvent(event)
    elif kind == QEvent.Type.MouseMove:
        window.ruler.mouseMoveEvent(event)
    else:
        window.ruler.mouseReleaseEvent(event)


def ruler_click(window, beats: float) -> None:
    x = window.ruler.origin().x() + window.view.mapFromScene(QPointF(beats, 0.0)).x()
    ruler_mouse(window, QEvent.Type.MouseButtonPress, x)
    ruler_mouse(window, QEvent.Type.MouseButtonRelease, x)


def test_clicking_the_timeline_moves_the_playhead(window) -> None:
    window.edit.pen.click()  # the ruler seeks even while editing
    window.view.set_playhead(None)

    ruler_click(window, 3.0)
    assert window.view.playhead == pytest.approx(1.5)  # three beats at 120 BPM

    hbar = window.view.horizontalScrollBar()
    hbar.setValue(0)
    window.view.set_playhead(None)
    ruler_mouse(window, QEvent.Type.MouseButtonPress, 400.0)
    ruler_mouse(window, QEvent.Type.MouseMove, 300.0)
    ruler_mouse(window, QEvent.Type.MouseButtonRelease, 300.0)
    assert hbar.value() == 100 and window.view.playhead is None  # a drag scrolls instead
    hbar.setValue(0)
    window._stop()
    window.view.set_playhead(None)


def test_a_click_with_the_select_tool_moves_the_playhead(window) -> None:
    window.edit.select.click()  # picking a tool enters edit mode
    assert window.view.edit_mode
    window.view.clear_notes()
    window.view.set_playhead(None)
    point = QPointF(3.0, 40.0)

    roll_mouse(window, QEvent.Type.MouseButtonPress, point)
    roll_mouse(window, QEvent.Type.MouseButtonRelease, point)
    assert window.view.playhead == pytest.approx(1.5)  # the press moves the cursor
    assert not window.view.notes()

    window.edit.pen.click()
    window._stop()
    window.view.set_playhead(None)
    window.view.clear_notes()


def test_a_new_window_starts_with_editing_off(qt_app) -> None:
    window = MainWindow()
    try:
        assert not window.edit.mode.isChecked() and not window.view.edit_mode
    finally:
        window.close()


def draw_note(window, press: QPointF, release: QPointF | None = None) -> None:
    window.view.centerOn(QPointF((press.x() + (release or press).x()) / 2, press.y()))
    roll_mouse(window, QEvent.Type.MouseButtonPress, press)
    if release is not None:
        roll_mouse(window, QEvent.Type.MouseMove, release)
    roll_mouse(window, QEvent.Type.MouseButtonRelease, release or press)


def clusters(window) -> list[tuple[str, Cluster, int]]:
    found = []
    for bar in (window.transport, window.edit, window.mix):
        for cluster in bar.findChildren(Cluster):
            found.append((bar.windowTitle(), cluster, bar.height()))
    return found


def test_bars_have_room_for_every_cluster(window) -> None:
    for bar_name, cluster, bar_height in clusters(window):
        caption = cluster.caption.text()
        assert cluster.height() >= cluster.sizeHint().height(), f"{bar_name}/{caption} is squeezed"
        assert cluster.geometry().bottom() < bar_height, f"{bar_name}/{caption} overflows {bar_name}"


def test_clusters_do_not_overlap(window) -> None:
    for bar in (window.transport, window.edit, window.mix):
        boxes = sorted((c.geometry() for c in bar.findChildren(Cluster)), key=lambda box: box.left())
        for left, right in zip(boxes, boxes[1:], strict=False):
            assert right.left() >= left.right(), f"{bar.windowTitle()}: clusters overlap"


def test_bars_fit_the_default_window(window) -> None:
    width = window.width()
    for bar in (window.transport, window.edit, window.mix):
        assert bar.sizeHint().width() <= width, f"{bar.windowTitle()} needs {bar.sizeHint().width()}px of {width}px"


def test_value_sliders_keep_their_caption_next_to_them(window) -> None:
    for slider in window.mix.findChildren(ValueSlider):
        assert slider.width() == slider.sizeHint().width()
        assert slider.caption.x() < slider.slider.x() < slider.value_label.x()


def test_tool_buttons_switch_the_roll_mode(window) -> None:
    window.edit.select.click()
    assert window.view.tool == "select"
    window.edit.pen.click()
    assert window.view.tool == "pen"


def test_mode_buttons_are_icons_not_text(window) -> None:
    buttons = (
        window.edit.mode,
        window.edit.pen,
        window.edit.select,
        window.edit.division_beats,
        window.edit.division_seconds,
        window.transport.play_pause,
    )
    for button in buttons:
        assert not button.text(), f"{button.objectName()} still has a text label"
        assert not button.icon().isNull(), f"{button.objectName()} has no icon"
        assert button.toolTip(), f"{button.objectName()} has no tooltip"


def test_scrollbars_move_the_view(window) -> None:
    hbar, vbar = window.view.horizontalScrollBar(), window.view.verticalScrollBar()
    assert not hbar.isHidden() and not vbar.isHidden()
    assert hbar.maximum() > 0 and vbar.maximum() > 0

    before = window.view.mapToScene(window.view.viewport().rect().center())
    hbar.setValue(hbar.value() + 120)
    assert window.view.mapToScene(window.view.viewport().rect().center()).x() > before.x()
    vbar.setValue(vbar.value() + 120)
    assert window.view.mapToScene(window.view.viewport().rect().center()).y() > before.y()


def test_ruler_marks_line_up_with_the_roll(window) -> None:
    window.view.clear_notes()
    ruler, viewport = window.ruler, window.view.viewport()
    ruler_image, view_image = ruler.grab().toImage(), window.view.grab().toImage()
    row = viewport.mapTo(window.view, QPoint(0, viewport.height() // 2)).y()
    ruler_marks = [x for x in range(ruler_image.width()) if ruler_image.pixelColor(x, RULER_TIME_ROW + 2) == GRID_BAR]
    roll_marks = [x for x in range(view_image.width()) if view_image.pixelColor(x, row) == GRID_BAR]
    offset = ruler.mapToGlobal(QPoint(0, 0)).x() - window.view.mapToGlobal(QPoint(0, 0)).x()
    assert ruler_marks and roll_marks
    assert [x + offset for x in ruler_marks] == roll_marks


def test_keyboard_rows_line_up_with_the_roll(window) -> None:
    window.view.clear_notes()
    keyboard, viewport = window.keyboard, window.view.viewport()
    keyboard_image, view_image = keyboard.grab().toImage(), window.view.grab().toImage()
    column = viewport.mapTo(window.view, QPoint(viewport.width() // 2, 0)).x()
    white_keys = [y for y in range(keyboard_image.height()) if keyboard_image.pixelColor(2, y) == QColor("#d8dde6")]
    white_rows = [y for y in range(view_image.height()) if view_image.pixelColor(column, y) == QColor("#262b34")]
    offset = keyboard.mapToGlobal(QPoint(0, 0)).y() - window.view.mapToGlobal(QPoint(0, 0)).y()
    assert white_keys and white_rows
    assert [y + offset for y in white_keys] == white_rows


def test_division_buttons_are_exclusive(window) -> None:
    window.edit.division_seconds.click()
    assert window.edit.division_seconds.isChecked()
    assert not window.edit.division_beats.isChecked()
    window.edit.division_beats.click()
    assert window.edit.division_beats.isChecked()
    assert not window.edit.division_seconds.isChecked()


def text_columns(image: QImage, top: int, bottom: int) -> set[int]:
    """Columns holding label glyphs in a ruler row; the rows are otherwise flat colours."""
    flat = {PANEL.name(), GRID_LINE.name(), GRID_BEAT.name(), GRID_BAR.name(), "#3a4152"}
    return {x for x in range(image.width()) for y in range(top, bottom) if QColor(image.pixel(x, y)).name() not in flat}


def test_ruler_shows_time_and_measures_whatever_the_division(window) -> None:
    for division in ("beats", "seconds"):
        window.view.division = division
        window.view.refresh()
        image = window.ruler.grab().toImage()
        assert text_columns(image, 0, RULER_TIME_ROW), f"no time labels with the {division} division"
        assert text_columns(image, RULER_TIME_ROW, RULER_HEIGHT), f"no measure numbers with {division}"
    window.view.division = "beats"


def test_ruler_time_row_ignores_the_division(window) -> None:
    labels = {}
    for division in ("beats", "seconds"):
        window.view.division = division
        window.view.refresh()
        labels[division] = text_columns(window.ruler.grab().toImage(), 0, RULER_TIME_ROW)
    window.view.division = "beats"
    assert labels["beats"] and labels["beats"] == labels["seconds"]


def test_spectrum_grid_lines_follow_the_division() -> None:
    view = PianoRollView()
    view.resize(900, 500)
    view.bpm = 120.0
    view.set_spectrum(make_spectrum(frames=400, value=0.0))  # black cells, so only the lines show
    view.centerOn(20.0, float(PITCH_MAX - 60) + 0.5)
    columns = {}
    for division in ("beats", "seconds"):
        view.division = division
        view.refresh()
        image = view.grab().toImage()
        y = device_point(view, 0.0, float(PITCH_MAX - 60) + 0.5).y()
        columns[division] = {x for x in range(image.width()) if image.pixelColor(x, y) in (SPECTRUM_BEAT, SPECTRUM_BAR)}
    assert columns["beats"] and columns["seconds"]
    assert columns["beats"] != columns["seconds"]


def test_defaults_of_the_control_bars(window) -> None:
    assert window.transport.bpm.value() == 120.0
    assert window.transport.latency.value() == 0
    assert window.transport.position.text() == "00:00.000"
    assert window.transport.speed.value() == 1.0
    assert window.mix.gain.value() == 240.0
    assert window.mix.contrast.value() == 1.0
    assert window.mix.audio_volume.value() == 80.0
    assert window.edit.snap.currentData() == 0.5 and window.view.snap == 0.5  # 1/8 by default
    assert window.edit.division_beats.isChecked()


def test_transport_position_is_a_clock(window) -> None:
    window.transport.set_position(63.25)
    assert window.transport.position.text() == "01:03.250"
    window.transport.set_position(0.0)


def test_the_tempo_hides_a_zero_decimal(window) -> None:
    for value, text in ((120.0, "120"), (96.4, "96.4"), (100.0, "100"), (97.5, "97.5")):
        window.transport.bpm.setValue(value)
        assert window.transport.bpm.text() == text
    window.transport.bpm.lineEdit().setText("96.4")  # typing still goes in as a value
    window.transport.bpm.interpretText()
    assert window.transport.bpm.value() == 96.4
    window.transport.bpm.setValue(120.0)


def test_the_tempo_and_latency_fields_select_their_text(window) -> None:
    for field in (window.transport.bpm, window.transport.latency):
        field.focusInEvent(QFocusEvent(QEvent.Type.FocusIn))
        assert field.lineEdit().selectedText() == field.cleanText() != ""
        field.lineEdit().deselect()
        field.mousePressEvent(
            QMouseEvent(
                QEvent.Type.MouseButtonPress,
                QPointF(6.0, 6.0),
                QPointF(field.mapToGlobal(QPoint(6, 6))),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        assert field.lineEdit().selectedText() == field.cleanText()


def test_the_latency_field_shows_its_unit_beside_it(window) -> None:
    field = window.transport.latency
    assert field.suffix() == ""
    unit = next(label for label in window.transport.findChildren(QLabel) if label.text() == "ms")
    assert unit.x() >= field.x() + field.width()  # the unit is a label behind the field
    image = field.grab().toImage()
    lit = [
        x for x in range(image.width()) if any(image.pixelColor(x, y).lightness() > 120 for y in range(image.height()))
    ]
    assert lit and min(lit) < image.width() * 0.25  # the number stays at the left of the field


def test_the_tempo_can_be_doubled_and_halved(window) -> None:
    box = window.transport.bpm
    box.setValue(120.0)
    box.double_action.trigger()
    assert box.value() == 240.0
    box.half_action.trigger()
    assert box.value() == 120.0
    box.setValue(300.0)
    assert not box.context_menu().actions()[0].isEnabled()  # the range stops the doubling
    assert box.context_menu().actions()[1].isEnabled()
    box.setValue(120.0)

    menu = box.context_menu()
    assert [action.text() for action in menu.actions()] == ["Double tempo  (*)", "Halve tempo  (/)"]
    assert all(action.isEnabled() for action in menu.actions())

    box.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Asterisk, Qt.KeyboardModifier.NoModifier))
    assert box.value() == 240.0
    box.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Slash, Qt.KeyboardModifier.NoModifier))
    assert box.value() == 120.0


def fake_estimate(bpm: float = 96.0, windows: int = 10, agree: int = 6) -> BeatTempo:
    local = tuple(LocalWindow(i * 6.0, i * 6.0 + 12.0, bpm if i < agree else bpm * 1.2) for i in range(windows))
    return BeatTempo(bpm=bpm, beats=(0.0, 60.0 / bpm), local=local, residual=0.02)


def test_tempo_estimate_is_only_a_suggestion(window) -> None:
    window.transport.bpm.setValue(120.0)
    window._on_tempo_loaded(fake_estimate())
    assert window.transport.tempo.isVisible()
    assert window.transport.bpm.value() == 120.0  # nothing is applied by itself
    assert "60% of them agree" in window.transport.tempo.toolTip()

    window.transport.tempo.apply_button.click()
    assert window.transport.bpm.value() == 96.0
    assert not window.transport.tempo.isVisible()
    window.transport.bpm.setValue(120.0)


def test_tempo_suggestion_can_be_dismissed_without_applying(window) -> None:
    window.transport.bpm.setValue(120.0)
    window._on_tempo_loaded(fake_estimate(bpm=100.0))
    window.transport.tempo.dismiss_button.click()
    assert not window.transport.tempo.isVisible()
    assert window.transport.bpm.value() == 120.0


def test_typing_a_tempo_drops_the_suggestion(window) -> None:
    window._on_tempo_loaded(fake_estimate())
    window.transport.bpm.setValue(140.0)
    assert not window.transport.tempo.isVisible()
    window.transport.bpm.setValue(120.0)


def test_a_weak_tempo_estimate_is_dimmed(window) -> None:
    window._on_tempo_loaded(fake_estimate(windows=10, agree=2))
    assert WEAK_COLOR in window.transport.tempo.label.styleSheet()
    window.transport.tempo.hide()


def test_tempo_loader_reports_a_bad_file(window, tmp_path) -> None:
    broken = tmp_path / "broken.wav"
    broken.write_text("not audio")
    messages: list[str] = []
    loader = TempoLoader(broken)
    loader.failed.connect(messages.append)
    loader.run()
    assert len(messages) == 1 and "Error" in messages[0]


def test_hover_marks_the_row_and_its_overtones(window) -> None:
    if not window.view.edit_mode:
        window.edit.pen.click()
    window.view.clear_notes()
    window.view.centerOn(QPointF(8.0, float(PITCH_MAX - 65)))  # both G3 and its twelfth in view
    window.view.set_hover_pitch(55)  # G3
    assert window.view.highlight_pitches() == [55, 67, 74]  # its octave and its twelfth
    assert window.cursor_note.text() == "G3   196.00 Hz"

    def row_brightness(pitch: int) -> int:
        return pixel_at(window.view, window.view.grab().toImage(), 8.0, PITCH_MAX - pitch + 0.5).lightness()

    rows = (54, 55, 67, 74)
    marked = {pitch: row_brightness(pitch) for pitch in rows}
    window.view.set_hover_pitch(None)
    plain = {pitch: row_brightness(pitch) for pitch in rows}
    assert all(marked[pitch] > plain[pitch] for pitch in (55, 67, 74))
    assert marked[54] == plain[54]  # the row above stays as it was
    assert window.cursor_note.text() == ""


def test_hover_turns_the_key_of_that_row_red_in_either_mode(window) -> None:
    window.view.centerOn(QPointF(8.0, float(PITCH_MAX - 65)))

    def red_rows(pitch: int) -> set[int]:
        window.view.set_hover_pitch(pitch)
        image = window.keyboard.grab().toImage()
        window.view.set_hover_pitch(None)
        return {y for y in range(image.height()) if image.pixelColor(2, y) == HOVER_KEY}

    def red_bands(pitch: int) -> int:
        rows = sorted(red_rows(pitch))
        return sum(1 for index, y in enumerate(rows) if index == 0 or y != rows[index - 1] + 1)

    if window.view.edit_mode:
        window.edit.mode.click()
    assert red_bands(55) == 1 and red_bands(54) == 1  # G3 is a white key, F#3 a black one: both mark

    window.view.set_hover_pitch(None)
    plain = window.keyboard.grab().toImage()
    assert not {y for y in range(plain.height()) if plain.pixelColor(2, y) == HOVER_KEY}

    window.edit.pen.click()  # editing adds the octave and the twelfth, on the keyboard as well
    assert red_bands(55) == 3
    window.edit.mode.click()


def test_playhead_is_drawn_at_the_play_position(window) -> None:
    window.view.set_playhead(2.0)  # 2 s at 120 BPM = 4 beats
    image = window.view.grab().toImage()
    row = window.view.viewport().mapTo(window.view, QPoint(0, window.view.viewport().height() // 2)).y()
    marks = [x for x in range(image.width()) if image.pixelColor(x, row) == PLAYHEAD]
    window.view.set_playhead(None)
    assert marks
    assert min(abs(x - device_point(window.view, 4.0, 0.0).x()) for x in marks) <= 1


class FakeOutput:
    """Stands in for a playback backend, so the transport can be tested without sound."""

    def __init__(self) -> None:
        self.gain = 1.0
        self.duration = 0.0
        self.position = 0.0
        self.is_playing = False
        self.calls: list[str] = []
        self.programs: list[tuple] = []
        self.previews: list[int] = []

    def set_program(self, notes, speed) -> None:
        self.programs.append((tuple(notes), speed))
        self.duration = max((start + duration for _pitch, start, duration in notes), default=0.0) + 0.5
        self.calls.append("set_program")

    def preview(self, pitch: int, seconds: float = 0.6) -> None:
        self.previews.append(pitch)

    def play(self, seconds: float = 0.0) -> None:
        self.position = seconds
        self.is_playing = True
        self.calls.append("play")

    def pause(self) -> None:
        self.is_playing = False
        self.calls.append("pause")

    def stop(self) -> None:
        self.is_playing = False
        self.position = 0.0
        self.calls.append("stop")

    def seek(self, seconds: float) -> None:
        self.position = seconds
        self.calls.append("seek")


class FakeSong:
    """Stands in for the audio file layer, so the transport can be tested without a device."""

    def __init__(self, duration: float = 30.0) -> None:
        self.gain = 1.0
        self.samples = np.zeros(0, dtype=np.float32)  # nothing to rerender, so no stretch thread starts
        self.stretch = 1.0
        self.is_loaded = True
        self.duration = duration
        self.position = 0.0
        self.is_playing = False
        self.calls: list[str] = []

    def set_stretched(self, buffer, stretch: float) -> None:
        self.stretch = stretch
        self.calls.append("set_stretched")

    def play(self, seconds: float = 0.0) -> None:
        self.position = seconds
        self.is_playing = True
        self.calls.append("play")

    def pause(self) -> None:
        self.is_playing = False
        self.calls.append("pause")

    def stop(self) -> None:
        self.is_playing = False
        self.position = 0.0
        self.calls.append("stop")

    def seek(self, seconds: float) -> None:
        self.position = seconds
        self.calls.append("seek")


def test_a_speed_change_reaches_the_notes_while_they_play(window, monkeypatch) -> None:
    song, notes = FakeSong(), FakeOutput()
    monkeypatch.setattr(window, "song", song)
    monkeypatch.setattr(window, "player", notes)
    window.view.clear_notes()
    window.view.add_note(69, 0.0, 2.0)
    window.transport.speed.set_value(1.0)
    song.stretch = 1.0
    window.transport.play_pause.click()
    assert notes.is_playing and notes.programs[-1][1] == 1.0
    song.position = 1.0  # the song is the master clock, and it is a second in

    window.transport.speed.set_value(1.5)
    window._apply_speed()  # what the settle timer does
    assert notes.programs[-1][1] == 1.5  # the notes are handed over at the new speed
    assert notes.is_playing and notes.position == pytest.approx(1.0)  # and carry on from there

    song.position = 1.4
    window.transport.speed.set_value(0.8)
    window._apply_speed()
    assert notes.programs[-1][1] == 0.8 and notes.position == pytest.approx(1.4)
    assert notes.calls.count("set_program") == 3

    window.transport.speed.set_value(1.0)
    window._stop()
    window.view.clear_notes()


def test_a_speed_the_song_has_not_been_rendered_at_defers_the_play(window, monkeypatch) -> None:
    song, notes = FakeSong(), FakeOutput()
    monkeypatch.setattr(window, "song", song)
    monkeypatch.setattr(window, "player", notes)
    rendered: list[bool] = []
    monkeypatch.setattr(window, "_start_stretch", lambda: rendered.append(True))

    window.transport.speed.set_value(1.5)
    window.transport.play_pause.click()

    assert rendered == [True] and window.pending_play  # the rerender was asked for
    assert song.calls == [] and notes.calls == []  # and nothing plays before it lands

    window._on_stretch_loaded(np.zeros(10, dtype=np.float32), 1.5)
    assert song.calls == ["set_stretched", "play"] and not window.pending_play
    assert notes.calls == ["set_program", "play"]

    window.transport.speed.set_value(1.0)  # put the slider back for the tests that follow
    window._stop()


def test_the_roll_waits_for_a_pause_before_it_seeks(window, monkeypatch) -> None:
    fake = FakeOutput()
    monkeypatch.setattr(window, "player", fake)
    window.view.clear_notes()
    window.view.add_note(69, 0.0, 1.0)
    window.view.centerOn(QPointF(2.5, 20.0))

    window.transport.play_pause.click()
    assert window.view.playhead == pytest.approx(0.0)

    scene_pos = QPointF(2.5, 20.0)
    roll_mouse(window, QEvent.Type.MouseButtonPress, scene_pos)  # playing: the roll keeps its cursor
    roll_mouse(window, QEvent.Type.MouseButtonRelease, scene_pos)
    assert window.view.playhead == pytest.approx(0.0)
    assert fake.calls.count("seek") == 0  # the click did not reach the transport

    ruler_click(window, 4.0)  # the ruler seeks while the file runs
    assert window.view.playhead == pytest.approx(2.0)
    assert fake.calls.count("seek") == 1
    assert fake.is_playing and window.view.playing

    window.transport.play_pause.click()  # pause
    assert not window.view.playing
    roll_mouse(window, QEvent.Type.MouseButtonPress, scene_pos)
    roll_mouse(window, QEvent.Type.MouseButtonRelease, scene_pos)
    assert window.view.playhead == pytest.approx(1.25)  # now the roll seeks again

    window._stop()
    window.view.set_playhead(None)
    window.view.clear_notes()


def test_the_volume_sliders_reach_their_layers(window, monkeypatch) -> None:
    song, notes = FakeSong(), FakeOutput()
    monkeypatch.setattr(window, "song", song)
    monkeypatch.setattr(window, "player", notes)

    window.mix.audio_volume.set_value(30)
    window.mix.midi_volume.set_value(20)

    assert song.gain == pytest.approx(0.3)  # what the audio file is streamed at
    assert notes.gain == pytest.approx(0.2)  # what the notes sound at


def test_transport_buttons_drive_the_player(window, monkeypatch) -> None:
    fake = FakeOutput()
    monkeypatch.setattr(window, "player", fake)
    window.view.clear_notes()
    window.view.add_note(69, 0.0, 1.0)

    window.transport.play_pause.click()
    assert fake.calls == ["set_program", "play"] and fake.is_playing
    assert fake.programs == [(((69, 0.0, 0.5),), 1.0)]  # one beat at 120 BPM, handed over in seconds
    assert window.view.playhead is not None

    window.transport.play_pause.click()  # the same button pauses
    assert not fake.is_playing
    window.transport.forward.click()
    assert fake.position == pytest.approx(fake.duration)
    window.transport.rewind.click()
    assert fake.position == 0.0
    window.transport.stop.click()
    assert not fake.is_playing and fake.position == 0.0

    window.view.set_playhead(None)
    window.view.clear_notes()


def test_the_play_button_shows_what_it_will_do(window, monkeypatch) -> None:
    fake = FakeOutput()
    monkeypatch.setattr(window, "player", fake)
    window.view.clear_notes()
    window.view.add_note(69, 0.0, 2.0)

    def icon_image() -> QImage:
        return window.transport.play_pause.icon().pixmap(24, 24).toImage()

    play_icon = icon_image()
    window.transport.play_pause.click()
    assert window.transport.play_pause.toolTip() == "Pause playback (Space)"
    assert icon_image() != play_icon  # it became a pause button
    window.transport.play_pause.click()
    assert window.transport.play_pause.toolTip() == "Play from the cursor (Space)"
    assert icon_image() == play_icon
    window.view.clear_notes()


def test_play_from_the_beginning_rewinds_first(window, monkeypatch) -> None:
    fake = FakeOutput()
    monkeypatch.setattr(window, "player", fake)
    window.view.clear_notes()
    window.view.add_note(69, 0.0, 4.0)
    window.transport.play_pause.click()
    fake.position = 1.0  # pretend a second of it has played

    window.transport.play_from_start.click()
    assert "seek" in fake.calls and fake.position == 0.0 and fake.is_playing
    window.transport.stop.click()
    window.view.clear_notes()


def test_space_plays_and_pauses(window, monkeypatch) -> None:
    fake = FakeOutput()
    monkeypatch.setattr(window, "player", fake)
    window.view.clear_notes()
    window.view.add_note(69, 0.0, 2.0)
    QApplication.setActiveWindow(window)  # an offscreen window is never active on its own

    QTest.keyClick(window, Qt.Key.Key_Space)
    assert fake.is_playing
    QTest.keyClick(window, Qt.Key.Key_Space)
    assert not fake.is_playing
    window.view.clear_notes()


def test_the_audio_file_plays_alongside_the_notes(window, monkeypatch) -> None:
    song, notes = FakeSong(), FakeOutput()
    monkeypatch.setattr(window, "song", song)
    monkeypatch.setattr(window, "player", notes)
    window.view.clear_notes()
    window.view.add_note(69, 0.0, 1.0)
    window.transport.speed.set_value(0.5)
    song.stretch = 0.5  # the song has already been rerendered for this speed
    song.position = 4.0  # the playhead already sits inside the file

    window.transport.play_pause.click()
    assert song.calls == ["play"] and song.position == pytest.approx(4.0) and song.is_playing
    assert notes.calls == ["set_program", "play"] and notes.position == pytest.approx(4.0)
    assert notes.programs[0][1] == 0.5  # the note layer takes the same speed

    song.position = 6.5
    window._show_position()
    assert window.view.playhead == pytest.approx(6.5)  # the file leads the readout
    assert window.transport.position.text() == "00:06.500"

    window.transport.stop.click()
    assert song.calls == ["play", "stop"] and song.position == 0.0 and not song.is_playing
    window.transport.speed.set_value(1.0)
    window.view.clear_notes()


def test_the_audio_file_keeps_playing_when_the_notes_end(window, monkeypatch) -> None:
    song, notes = FakeSong(), FakeOutput()
    monkeypatch.setattr(window, "song", song)
    monkeypatch.setattr(window, "player", notes)
    window.position_timer.start()
    song.is_playing = True

    window._on_playback_finished()  # the note layer ran out first
    assert window.position_timer.isActive()

    song.is_playing = False
    window._on_playback_finished()  # and now the file is over too
    assert not window.position_timer.isActive()
    window.view.set_playhead(None)


def test_clicking_the_roll_moves_the_playhead(window) -> None:
    if window.view.edit_mode:
        window.edit.mode.click()  # outside edit mode the roll is a seek bar
    assert not window.view.edit_mode
    scene_pos = QPointF(2.0, 40.0)  # scene units: two beats across, forty rows down

    roll_mouse(window, QEvent.Type.MouseButtonPress, scene_pos)
    roll_mouse(window, QEvent.Type.MouseButtonRelease, scene_pos)
    assert window.view.playhead == pytest.approx(1.0)  # two beats at 120 BPM
    assert window.player.position == pytest.approx(1.0)

    window.edit.pen.click()
    window.view.set_playhead(None)
    window.view.clear_notes()


def test_the_pen_draws_where_the_playhead_lands(window) -> None:
    window.edit.pen.click()  # picking a tool enters edit mode
    assert window.view.edit_mode and window.view.tool == "pen"
    window.view.set_playhead(None)
    window.view.clear_notes()
    left = QPointF(2.0, 40.0)
    right = QPointF(4.0, 40.0)

    draw_note(window, left, right)
    assert len(window.view.notes()) == 1
    assert window.view.playhead == pytest.approx(2.0)  # the drag carried the cursor to 4 beats
    window._stop()
    window.view.set_playhead(None)
    window.view.clear_notes()


def test_clicking_a_key_previews_the_pitch_under_it(window, monkeypatch) -> None:
    fake = FakeOutput()
    monkeypatch.setattr(window, "player", fake)
    keyboard = window.keyboard
    for y in (keyboard.height() // 4, keyboard.height() // 2, keyboard.height() * 3 // 4):
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(20.0, float(y)),
            keyboard.mapToGlobal(QPointF(20.0, float(y))),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        window.keyboard.mousePressEvent(event)
    assert len(fake.previews) == 3
    assert fake.previews == sorted(fake.previews, reverse=True)  # lower on screen is a lower note


def test_clicking_a_note_previews_it(window, monkeypatch) -> None:
    fake = FakeOutput()
    monkeypatch.setattr(window, "player", fake)
    window.view.clear_notes()
    window.view.add_note(69, 2.0, 1.0)
    window.view.centerOn(QPointF(2.5, float(PITCH_MAX - 69) + 0.5))
    position = window.view.mapFromScene(QPointF(2.5, float(PITCH_MAX - 69) + 0.5))
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(position),
        window.view.viewport().mapToGlobal(QPointF(position)),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.view.mousePressEvent(event)
    assert fake.previews == [69]
    window.view.mouseReleaseEvent(event)
    window.view.clear_notes()


def test_the_second_of_two_quick_clicks_still_sounds_and_seeks(window, monkeypatch) -> None:
    """Qt hands the second quick click over as a double-click, and that press counts as well."""
    fake = FakeOutput()
    monkeypatch.setattr(window, "player", fake)
    if window.view.edit_mode:
        window.edit.mode.click()
    window.view.clear_notes()
    row = float(PITCH_MAX - 60) + 0.5

    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
        window.view.set_playhead(None)
        roll_mouse(window, kind, QPointF(2.5, row))
        assert fake.previews[-1] == 60
        assert window.view.playhead == pytest.approx(1.25)  # 2.5 beats at 120 BPM
        roll_mouse(window, QEvent.Type.MouseButtonRelease, QPointF(2.5, row))

    assert fake.previews == [60, 60]
    window._stop()
    window.view.set_playhead(None)
    window.view.clear_notes()


def test_a_drag_in_view_mode_scrubs_the_playhead(window, monkeypatch) -> None:
    fake = FakeOutput()
    monkeypatch.setattr(window, "player", fake)
    if window.view.edit_mode:
        window.edit.mode.click()
    window.view.clear_notes()
    window.view.set_playhead(None)
    window.view.centerOn(QPointF(3.0, float(PITCH_MAX - 60) + 0.5))

    roll_mouse(window, QEvent.Type.MouseButtonPress, QPointF(2.0, float(PITCH_MAX - 60) + 0.5))
    for beats, pitch in ((3.0, 62), (4.0, 64)):
        roll_mouse(window, QEvent.Type.MouseMove, QPointF(float(beats), float(PITCH_MAX - pitch) + 0.5))
        assert window.view.playhead == pytest.approx(beats / 2.0)  # two beats a second at 120 BPM
    roll_mouse(window, QEvent.Type.MouseButtonRelease, QPointF(4.0, float(PITCH_MAX - 64) + 0.5))

    assert fake.previews == [60, 62, 64]  # the drag sounds the rows it crosses
    window._stop()
    window.view.set_playhead(None)
    window.view.clear_notes()


def test_a_drag_glides_the_playhead_and_sounds_every_row_it_crosses(window, monkeypatch) -> None:
    fake = FakeOutput()
    monkeypatch.setattr(window, "player", fake)
    window.edit.pen.click()  # the pen draws and auditions while it slides
    window.view.clear_notes()
    window.view.set_playhead(None)
    window.view.centerOn(QPointF(2.5, float(PITCH_MAX - 60) + 0.5))

    roll_mouse(window, QEvent.Type.MouseButtonPress, QPointF(2.5, float(PITCH_MAX - 60) + 0.5))
    for pitch in (62, 64, 64, 65):  # two moves inside one row must not sound it twice
        roll_mouse(window, QEvent.Type.MouseMove, QPointF(2.5 + (pitch - 60) * 0.5, float(PITCH_MAX - pitch) + 0.5))
    assert fake.previews == [60, 62, 64, 65]  # a glissando up the rows
    assert window.view.playhead == pytest.approx(2.5)  # the drag reached 5 beats at 120 BPM
    roll_mouse(window, QEvent.Type.MouseButtonRelease, QPointF(5.0, float(PITCH_MAX - 65) + 0.5))

    assert fake.previews == [60, 62, 64, 65]  # no stray preview after the release
    roll_mouse(window, QEvent.Type.MouseMove, QPointF(7.0, float(PITCH_MAX - 70) + 0.5))
    assert fake.previews == [60, 62, 64, 65]  # and none while merely hovering either
    assert window.view.playhead == pytest.approx(2.5)

    window._stop()
    window.view.set_playhead(None)
    window.view.clear_notes()


def test_a_click_sounds_the_row_it_lands_on_in_either_mode(window, monkeypatch) -> None:
    fake = FakeOutput()
    monkeypatch.setattr(window, "player", fake)
    if window.view.edit_mode:
        window.edit.mode.click()  # nobody has picked a tool: this is the listening mode
    window.view.clear_notes()
    window.view.add_note(64, 2.0, 1.0)
    empty_row = float(PITCH_MAX - 60) + 0.5
    note_row = float(PITCH_MAX - 64) + 0.5

    draw_note(window, QPointF(2.5, empty_row))
    assert fake.previews == [60]  # an empty row sounds the pitch it is on
    draw_note(window, QPointF(2.5, note_row))
    assert fake.previews == [60, 64]  # a note sounds its own

    window._stop()
    window.view.set_playhead(None)
    window.view.clear_notes()


def test_a_press_on_a_note_moves_the_playhead_as_well(window, monkeypatch) -> None:
    fake = FakeOutput()
    monkeypatch.setattr(window, "player", fake)
    window.edit.pen.click()
    window.view.clear_notes()
    note = window.view.add_note(69, 2.0, 1.0)
    row = float(PITCH_MAX - 69) + 0.5
    window.view.centerOn(QPointF(2.5, row))
    window.view.set_playhead(None)

    roll_mouse(window, QEvent.Type.MouseButtonPress, QPointF(2.5, row))
    assert window.view.playhead == pytest.approx(1.25)  # 2.5 beats at 120 BPM, note or not
    assert fake.previews == [69]  # and the note is still the one being edited
    roll_mouse(window, QEvent.Type.MouseMove, QPointF(6.0, row))
    assert (note.start, note.end) == (5.5, 6.5)
    assert window.view.playhead == pytest.approx(3.0)  # the playhead travels with the drag
    roll_mouse(window, QEvent.Type.MouseButtonRelease, QPointF(6.0, row))

    window._stop()
    window.view.set_playhead(None)
    window.view.clear_notes()


def test_drawing_a_note_covers_the_cells_it_passed_through(window, monkeypatch) -> None:
    fake = FakeOutput()
    monkeypatch.setattr(window, "player", fake)
    window.view.clear_notes()
    row = float(PITCH_MAX - 69) + 0.5
    press = QPointF(8.3, row)  # a third of the way into the cell that starts at 8.0
    draw_note(window, press, QPointF(9.6, row))
    notes = window.view.notes()
    assert len(notes) == 1
    note = notes[0]
    assert (note.pitch, note.start, note.end) == (69, 8.0, 10.0)
    assert note.start <= press.x() <= note.end  # the note is where the click was
    assert fake.previews == [69]
    window.view.clear_notes()


def test_dragging_left_draws_the_note_to_the_left(window) -> None:
    window.view.clear_notes()
    row = float(PITCH_MAX - 69) + 0.5
    draw_note(window, QPointF(9.4, row), QPointF(7.6, row))
    note = window.view.notes()[0]
    assert (note.start, note.end) == (7.5, 9.5)
    window.view.clear_notes()


def test_a_click_without_a_drag_draws_one_snap_cell(window) -> None:
    row = float(PITCH_MAX - 69) + 0.5
    for with_move in (False, True):  # Qt hands out move events even when the mouse barely moved
        window.view.clear_notes()
        press = QPointF(8.3, row)
        draw_note(window, press, press if with_move else None)
        note = window.view.notes()[0]
        assert (note.start, note.end) == (8.0, 8.5)
    window.view.clear_notes()


def test_drawing_slides_the_note_to_the_row_the_pointer_moves_to(window) -> None:
    window.view.clear_notes()
    draw_note(
        window,
        QPointF(8.3, float(PITCH_MAX - 69) + 0.5),
        QPointF(9.6, float(PITCH_MAX - 76) + 0.5),  # slid seven rows up while drawing
    )
    note = window.view.notes()[0]
    assert (note.pitch, note.start, note.end) == (76, 8.0, 10.0)
    window.view.clear_notes()


def test_dragging_either_edge_of_a_note_changes_its_duration(window) -> None:
    window.view.clear_notes()
    note = window.view.add_note(69, 2.0, 2.0)  # spans 2.0 to 4.0
    row = float(PITCH_MAX - 69) + 0.5
    window.view.centerOn(QPointF(3.0, row))

    roll_mouse(window, QEvent.Type.MouseButtonPress, QPointF(2.05, row))  # within the left grab band
    roll_mouse(window, QEvent.Type.MouseMove, QPointF(1.0, row))
    assert (note.start, note.end) == (1.0, 4.0)  # the start moved, the end stayed
    roll_mouse(window, QEvent.Type.MouseButtonRelease, QPointF(1.0, row))

    roll_mouse(window, QEvent.Type.MouseButtonPress, QPointF(3.95, row))  # within the right grab band
    roll_mouse(window, QEvent.Type.MouseMove, QPointF(5.0, row))
    assert (note.start, note.end) == (1.0, 5.0)  # the end moved, the start stayed
    roll_mouse(window, QEvent.Type.MouseButtonRelease, QPointF(5.0, row))
    window.view.clear_notes()


def test_shift_dragging_a_note_moves_the_edge_that_was_grabbed(window) -> None:
    window.view.clear_notes()
    note = window.view.add_note(69, 2.0, 2.0)  # spans 2.0 to 4.0, so it is split at 3.0
    row = float(PITCH_MAX - 69) + 0.5
    start = Qt.KeyboardModifier.ShiftModifier

    window.view.centerOn(QPointF(3.0, row))
    roll_mouse(window, QEvent.Type.MouseButtonPress, QPointF(2.5, row), start)  # left half
    roll_mouse(window, QEvent.Type.MouseMove, QPointF(1.6, row), start)
    assert (note.start, note.end) == (1.5, 4.0)  # the start moved, the end stayed
    roll_mouse(window, QEvent.Type.MouseButtonRelease, QPointF(1.6, row), start)

    roll_mouse(window, QEvent.Type.MouseButtonPress, QPointF(3.5, row), start)  # right half
    roll_mouse(window, QEvent.Type.MouseMove, QPointF(4.9, row), start)
    assert (note.start, note.end) == (1.5, 5.0)  # the end moved, the start stayed
    roll_mouse(window, QEvent.Type.MouseButtonRelease, QPointF(4.9, row), start)

    roll_mouse(window, QEvent.Type.MouseButtonPress, QPointF(2.9, row), start)
    roll_mouse(window, QEvent.Type.MouseMove, QPointF(9.0, row), start)  # drag the start past the end
    assert note.duration == pytest.approx(MIN_DURATION)
    roll_mouse(window, QEvent.Type.MouseButtonRelease, QPointF(9.0, row), start)
    window.view.clear_notes()


def test_ctrl_click_is_what_adds_to_the_selection(window) -> None:
    window.view.clear_notes()
    first = window.view.add_note(69, 2.0, 1.0)
    second = window.view.add_note(72, 4.0, 1.0)
    first_row = float(PITCH_MAX - 69) + 0.5
    second_row = float(PITCH_MAX - 72) + 0.5
    window.view.centerOn(QPointF(4.0, first_row))
    for scene_pos, modifiers in (
        (QPointF(2.5, first_row), Qt.KeyboardModifier.NoModifier),
        (QPointF(4.5, second_row), Qt.KeyboardModifier.ControlModifier),
    ):
        roll_mouse(window, QEvent.Type.MouseButtonPress, scene_pos, modifiers)
        roll_mouse(window, QEvent.Type.MouseButtonRelease, scene_pos, modifiers)
    assert first.isSelected() and second.isSelected()
    window.view.clear_notes()


def roll_wheel(window, delta: int, modifiers=Qt.KeyboardModifier.NoModifier) -> None:
    view = window.view
    position = QPointF(view.viewport().rect().center())
    event = QWheelEvent(
        position,
        QPointF(view.viewport().mapToGlobal(QPointF(position).toPoint())),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        modifiers,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    view.wheelEvent(event)


def slider_wheel(slider, delta: int) -> None:
    position = QPointF(slider.width() / 2, slider.height() / 2)
    event = QWheelEvent(
        position,
        QPointF(slider.mapToGlobal(position.toPoint())),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    slider.wheelEvent(event)


def test_a_click_on_a_slider_track_lands_where_it_was_aimed(window) -> None:
    speed = window.transport.speed
    slider = speed.slider

    speed.set_value(1.0)
    QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=QPoint(slider.width() * 3 // 4, slider.height() // 2))
    assert speed.value() > 1.5  # near the right end, not one page step (0.20) from where it was

    QTest.mouseClick(slider, Qt.MouseButton.LeftButton, pos=QPoint(slider.width() // 10, slider.height() // 2))
    assert speed.value() < 0.3  # and near the left end
    speed.set_value(1.0)


def test_the_slider_wheel_walks_the_other_way(window) -> None:
    speed = window.transport.speed
    speed.set_value(1.0)

    slider_wheel(speed.slider, 120)  # wheel up
    assert speed.value() == pytest.approx(0.95)  # turns the value down by one step (5%)
    slider_wheel(speed.slider, 120)
    slider_wheel(speed.slider, -120)  # wheel down
    assert speed.value() == pytest.approx(0.95)
    slider_wheel(speed.slider, -120)
    assert speed.value() == pytest.approx(1.0)
    speed.set_value(1.0)


def test_the_speed_slider_lands_on_five_percent_steps(window) -> None:
    speed = window.transport.speed
    seen: list[float] = []
    speed.value_changed.connect(seen.append)

    speed.slider.setValue(153)  # what a drag would hand over
    assert speed.value() == pytest.approx(1.55) and speed.value_label.text() == "1.55x"

    speed.slider.setValue(41)
    assert speed.value() == pytest.approx(0.40)
    speed.slider.triggerAction(QSlider.SliderAction.SliderSingleStepAdd)
    assert speed.value() == pytest.approx(0.45)

    speed.slider.setValue(0)
    assert speed.value() == pytest.approx(0.10)  # the slowest it goes
    speed.set_value(1.0)  # the reset button, on the grid as well
    assert speed.value() == pytest.approx(1.0)
    assert seen == [
        pytest.approx(1.55),
        pytest.approx(0.40),
        pytest.approx(0.45),
        pytest.approx(0.10),
        pytest.approx(1.0),
    ]
    assert all(abs(value * 20 - round(value * 20)) < 1e-9 for value in seen)  # every value is a step


def test_the_wheel_scrolls_the_timeline_and_shift_the_pitches(window) -> None:
    window.view.horizontalScrollBar().setValue(200)
    window.view.verticalScrollBar().setValue(200)
    horizontal = window.view.horizontalScrollBar().value()
    vertical = window.view.verticalScrollBar().value()

    roll_wheel(window, -120)
    assert window.view.horizontalScrollBar().value() == horizontal + 120
    assert window.view.verticalScrollBar().value() == vertical

    roll_wheel(window, -120, Qt.KeyboardModifier.ShiftModifier)
    assert window.view.horizontalScrollBar().value() == horizontal + 120
    assert window.view.verticalScrollBar().value() == vertical + 120


def test_the_roll_only_edits_in_edit_mode(window) -> None:
    row = float(PITCH_MAX - 69) + 0.5
    window.view.clear_notes()
    note = window.view.add_note(69, 8.0, 1.0)
    note.setSelected(True)

    if not window.view.edit_mode:
        window.edit.mode.click()
    window.edit.mode.click()  # leaving edit mode
    assert not window.view.edit_mode
    assert window.view.tool is None
    assert not window.edit.snap.isEnabled() and not window.edit.clear.isEnabled()

    draw_note(window, QPointF(8.3, row), QPointF(9.6, row))
    assert [n.pitch for n in window.view.notes()] == [69]  # the pen drew nothing
    window.view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier))
    assert len(window.view.notes()) == 1  # and delete did nothing either
    window.view.set_hover_pitch(69)
    assert window.view.highlight_pitches() == [69]  # only the row under the mouse, no overtones
    window.view.set_hover_pitch(None)
    window._stop()

    window.edit.mode.click()  # back in
    assert window.view.edit_mode and window.view.tool == "pen"
    assert window.edit.snap.isEnabled() and window.edit.clear.isEnabled()
    draw_note(window, QPointF(12.3, row), QPointF(13.6, row))  # elsewhere: the first row is taken
    assert len(window.view.notes()) == 2
    window.view.clear_notes()


def test_picking_a_tool_turns_on_edit_mode(window) -> None:
    if window.view.edit_mode:
        window.edit.mode.click()
    assert not window.view.edit_mode
    window.edit.select.click()
    assert window.view.edit_mode and window.edit.mode.isChecked() and window.view.tool == "select"
    window.edit.mode.click()
    window.edit.mode.click()  # entering the mode always lands on the pen
    assert window.view.tool == "pen" and window.edit.pen.isChecked()


def test_playing_an_empty_roll_says_so(window) -> None:
    window.view.clear_notes()
    window.transport.play_pause.click()
    assert not window.player.is_playing
    assert "Nothing to play" in window.statusBar().currentMessage()


def test_changing_the_tempo_keeps_the_audio_at_the_same_size_on_screen(window) -> None:
    window.view.clear_notes()
    note = window.view.add_note(69, 4.0, 2.0)
    window.view.centerOn(QPointF(20.0, 0.5))
    before = window.view.mapFromScene(QPointF(note.start, 0.0)).x()
    per_second = window.view.pixels_per_second()
    window.view.bpm = 93.0
    assert window.view.mapFromScene(QPointF(note.start, 0.0)).x() == before  # nothing moves
    assert window.view.pixels_per_second() == pytest.approx(per_second)
    window.view.bpm = 120.0
    window.view.clear_notes()


def test_changing_the_tempo_keeps_the_notes_where_they_are_in_time(window) -> None:
    window.view.clear_notes()
    note = window.view.add_note(69, 4.0, 2.0)  # at 120 BPM: 2 s in, 1 s long
    window.view.bpm = 60.0  # the grid halves, the note does not move in the audio
    assert (note.start, note.duration) == pytest.approx((2.0, 1.0))
    window.view.bpm = 93.0  # and it survives a tempo that does not divide the old one
    assert note.start * 60.0 / 93.0 == pytest.approx(2.0)
    assert note.duration * 60.0 / 93.0 == pytest.approx(1.0)
    window.view.bpm = 120.0
    assert (note.start, note.duration) == pytest.approx((4.0, 2.0))
    window.view.clear_notes()


def test_note_times_follow_the_tempo(window, monkeypatch) -> None:
    fake = FakeOutput()
    monkeypatch.setattr(window, "player", fake)
    window.view.clear_notes()
    window.view.add_note(69, 0.0, 2.0)  # two beats, one second at 120 BPM
    window.view.bpm = 60.0  # one beat per second now, so it is one beat and still one second
    window.transport.play_pause.click()
    assert fake.duration == pytest.approx(1.5)  # one second plus the release tail
    window.transport.stop.click()
    window.view.bpm = 120.0
    window.view.clear_notes()


def test_midi_volume_scales_the_output(window) -> None:
    window.mix.midi_volume.set_value(40.0)
    assert window.player.gain == pytest.approx(0.4)
    window.mix.midi_volume.set_value(80.0)


def test_midi_sink_keeps_the_position_without_playing() -> None:
    sink = MidiSink()
    sink.set_program([(69, 0.0, 1.0)], 1.0)
    assert sink.duration == pytest.approx(1.5)  # the note plus its release tail
    assert sink.position == 0.0 and not sink.is_playing
    sink.seek(0.75)
    assert sink.position == pytest.approx(0.75)
    sink.stop()
    assert sink.position == 0.0
    sink.set_program([(69, 0.0, 1.0)], 2.0)  # at double speed the mix is half as long
    assert len(sink.mix) == round(0.75 * 44100)
    assert sink.duration == pytest.approx(1.5)  # the timeline itself does not change


def test_a_synth_port_is_preferred_over_the_loopback() -> None:
    loopback = "Midi Through:Midi Through Port-0 14:0"
    assert find_synth_port([]) is None
    assert find_synth_port([loopback]) is None
    assert find_synth_port([loopback, "TiMidity:TiMidity port 0 129:0"]) == 1
    assert find_synth_port(["Midi Through:Midi Through Port-0 14:0", "FLUID Synth (qsynth)"]) == 1


class FakePort:
    def __init__(self) -> None:
        self.messages: list[list[int]] = []
        self.times: list[float] = []

    def send_message(self, message) -> None:
        self.messages.append(list(message))
        self.times.append(time.monotonic())


def test_the_port_player_schedules_notes_and_silences_them_on_stop() -> None:
    port = FakePort()
    player = MidiPortOut(port)
    player.set_program([(69, 0.0, 1.0), (76, 0.5, 1.0)], 1.0)
    assert [0xC0, 0x00] in port.messages  # the synth is asked for its piano first
    assert [0xB0, 0x07, 127] in port.messages  # and told how loud to be
    assert player.duration == pytest.approx(1.5)

    player.play()
    assert player.is_playing
    time.sleep(0.15)
    assert [0x90, 69, 100] in port.messages  # the first note is on
    assert [0x80, 69, 0] not in port.messages  # and not yet off

    player.stop()
    assert not player.is_playing
    assert [0x80, 69, 0] in port.messages  # stopping silences what was sounding


def test_the_port_player_reports_when_the_notes_are_done() -> None:
    def app_events() -> None:
        QApplication.instance().processEvents()

    player = MidiPortOut(FakePort())
    player.set_program([(69, 0.0, 0.1)], 1.0)
    done: list[bool] = []
    player.finished.connect(lambda: done.append(True))
    player.play()
    for _ in range(100):
        if done:
            break
        app_events()
        time.sleep(0.02)
    assert done
    assert not player.is_playing
    assert player.position == pytest.approx(player.duration)


def test_a_preview_plays_the_note_and_releases_it() -> None:
    port = FakePort()
    player = MidiPortOut(port)
    player.preview(72, seconds=0.05)
    assert [0x90, 72, 100] in port.messages
    time.sleep(0.15)
    assert [0x80, 72, 0] in port.messages


def make_spectrum(frames: int = 4, value: float = 1.0) -> NoteSpectrum:
    table = np.full((frames, NOTE_COUNT), value, dtype=np.float32)
    return NoteSpectrum(table=table, frame_ms=50.0, sample_rate=44100, fft_points=8192, hop=2205, a4=440.0, sigma=1.0)


def device_point(view: PianoRollView, scene_x: float, scene_y: float) -> QPoint:
    return view.viewport().mapTo(view, view.mapFromScene(QPointF(scene_x, scene_y)))


def pixel_at(view: PianoRollView, image: QImage, scene_x: float, scene_y: float) -> QColor:
    return image.pixelColor(device_point(view, scene_x, scene_y))


def row_colors(image: QImage, row: int) -> list[int]:
    return [image.pixel(x, row) for x in range(image.width())]


def test_spectrum_image_follows_the_colour_ramp() -> None:
    table = np.array([[0.0], [0.2], [0.5], [1.0], [9.0]], dtype=np.float32).repeat(NOTE_COUNT, axis=1)
    spectrum = NoteSpectrum(table, 50.0, 44100, 8192, 2205, 440.0, 1.0)
    image = SpectrumImage(spectrum).image(240.0, 1.0)

    assert (image.width(), image.height()) == (5, NOTE_COUNT)
    assert image.pixel(0, 40) == 0xFF000000  # silence is black
    assert image.pixel(4, 40) == 0xFFFF0000  # full energy saturates to red

    quiet, middle, hot = (image.pixelColor(column, 40) for column in (1, 2, 3))
    assert quiet.blue() > quiet.red()  # the ramp starts as blue
    assert middle.green() > middle.red()  # climbs through green
    assert (hot.red(), hot.green(), hot.blue()) == (255, 2, 0)  # and ends red


def test_spectrum_image_is_cached_until_the_parameters_change() -> None:
    spectrum = SpectrumImage(make_spectrum())
    first = spectrum.image(240.0, 1.0)
    assert spectrum.image(240.0, 1.0) is first
    assert spectrum.image(300.0, 1.0) is not first


def test_spectrum_row_zero_is_the_highest_band() -> None:
    table = np.zeros((2, NOTE_COUNT), dtype=np.float32)
    table[:, NOTE_COUNT - 1] = 9.0  # B7, the top band
    image = SpectrumImage(NoteSpectrum(table, 50.0, 44100, 8192, 2205, 440.0, 1.0)).image(240.0, 1.0)
    assert image.pixel(0, 0) == 0xFFFF0000
    assert image.pixel(0, NOTE_COUNT - 1) == 0xFF000000


def test_spectrum_frames_map_onto_the_beat_grid() -> None:
    view = PianoRollView()
    view.bpm = 120.0
    view.set_spectrum(make_spectrum())
    assert view.frame_width() == pytest.approx(0.1)  # 50 ms at 120 BPM
    view.bpm = 60.0
    assert view.frame_width() == pytest.approx(0.05)


def test_timeline_grows_with_the_spectrum() -> None:
    view = PianoRollView()
    assert view.content_beats() == pytest.approx(LENGTH_BEATS + CONTENT_MARGIN)

    view.set_spectrum(make_spectrum(frames=2000))  # 2000 frames of 50 ms is 100 s, 200 beats at 120 BPM
    assert view.content_beats() == pytest.approx(200.0 + CONTENT_MARGIN)
    assert view.sceneRect().width() == pytest.approx(view.content_beats())

    view.bpm = 60.0  # the same audio spans half as many beats
    assert view.content_beats() == pytest.approx(100.0 + CONTENT_MARGIN)

    view.set_spectrum(None)
    assert view.sceneRect().width() == pytest.approx(LENGTH_BEATS + CONTENT_MARGIN)


def test_timeline_covers_a_five_minute_song() -> None:
    view = PianoRollView()
    view.set_spectrum(make_spectrum(frames=6000))  # 5 minutes of 50 ms frames
    assert view.content_beats() * 60 / view.bpm >= 299.0


def test_timeline_grows_with_notes() -> None:
    view = PianoRollView()
    view.add_note(60, 100.0, 4.0)
    assert view.content_beats() == pytest.approx(104.0 + CONTENT_MARGIN)
    view.clear_notes()
    assert view.content_beats() == pytest.approx(LENGTH_BEATS + CONTENT_MARGIN)


def test_spectrum_sits_on_the_pitch_axis() -> None:
    assert SPECTRUM_TOP == PITCH_MAX - (MIDI_OFFSET + NOTE_COUNT - 1) == 1


def test_octave_lines_sit_on_the_b_and_c_boundary() -> None:
    view = PianoRollView()
    view.resize(900, 500)
    view.set_spectrum(make_spectrum(frames=400, value=0.0))  # black cells, so only the lines show
    view.centerOn(20.0, float(PITCH_MAX - MIDI_OFFSET + 1))
    image = view.grab().toImage()

    def line_fraction(scene_y: float) -> float:
        y = device_point(view, 0.0, scene_y).y()
        row = [image.pixelColor(x, y) for x in range(3, image.width() - 3, 7)]
        return sum(colour == SPECTRUM_OCTAVE for colour in row) / len(row)

    assert line_fraction(PITCH_MAX - MIDI_OFFSET + 1) > 0.8  # below C1, where B0 ends
    assert line_fraction(PITCH_MAX - MIDI_OFFSET) < 0.05  # the C1 / C#1 edge stays black
    assert line_fraction(PITCH_MAX - MIDI_OFFSET - 12 + 1) > 0.8  # still there an octave up


def test_roll_paints_the_spectrum(window) -> None:
    if window.view.edit_mode:
        window.edit.mode.click()  # full colour, so the edit-mode fade has to be off
    window.view.set_spectrum(make_spectrum(frames=200, value=9.0))
    image = window.view.grab().toImage()
    assert not image.isNull()
    assert any(
        image.pixelColor(x, y).red() > 200 and image.pixelColor(x, y).green() < 40
        for x in range(200, image.width(), 40)
        for y in range(20, image.height(), 20)
    )
    window.view.set_spectrum(None)


def test_editing_fades_the_spectrum_behind_the_notes(window) -> None:
    if window.view.edit_mode:
        window.edit.mode.click()
    window.view.set_hover_pitch(None)
    window.view.set_spectrum(make_spectrum(frames=200, value=9.0))
    window.view.centerOn(QPointF(2.0, 40.5))

    plain = pixel_at(window.view, window.view.grab().toImage(), 2.0, 40.5)
    window.edit.pen.click()  # the spectrum steps back so a note draws attention over it
    dimmed = pixel_at(window.view, window.view.grab().toImage(), 2.0, 40.5)
    assert dimmed.lightness() < plain.lightness()

    window.edit.mode.click()
    assert pixel_at(window.view, window.view.grab().toImage(), 2.0, 40.5).lightness() == plain.lightness()
    window.view.set_spectrum(None)


def test_notes_are_drawn_over_the_spectrum(window) -> None:
    if window.view.edit_mode:
        window.edit.mode.click()
    window.view.clear_notes()
    window.view.set_spectrum(make_spectrum(frames=400, value=9.0))
    note = window.view.add_note(60, 1.0, 4.0)
    middle = (note.start + note.duration / 2, PITCH_MAX - note.pitch + 0.5)
    window.view.centerOn(QPointF(*middle))
    image = window.view.grab().toImage()
    assert pixel_at(window.view, image, *middle) == NOTE_FILL
    assert pixel_at(window.view, image, note.start - 0.5, middle[1]) == QColor(255, 0, 0)  # the spectrum beside it
    window.view.clear_notes()
    window.view.set_spectrum(None)


def test_notes_are_flat_red_with_a_bevel(window) -> None:
    window.view.clear_notes()
    window.view.set_spectrum(make_spectrum(frames=400, value=0.0))  # black cells, nothing else is red
    note = window.view.add_note(60, 2.0, 2.0)
    top = PITCH_MAX - note.pitch + NOTE_INSET
    bottom = PITCH_MAX - note.pitch + 1.0 - NOTE_INSET
    window.view.centerOn(QPointF(note.start + note.duration / 2, (top + bottom) / 2))
    image = window.view.grab().toImage()
    top_left = device_point(window.view, note.start, top)
    bottom_right = device_point(window.view, note.end, bottom)

    mid_x = (top_left.x() + bottom_right.x()) // 2
    column = [image.pixelColor(mid_x, y) for y in range(top_left.y(), bottom_right.y())]
    assert NOTE_EDGE_LIGHT in column[:2]  # the light bevel is on top
    assert NOTE_EDGE_DARK in column[-2:]  # the dark one below
    assert {colour.name() for colour in column[2:-2]} == {NOTE_FILL.name()}

    row = [image.pixelColor(x, (top_left.y() + bottom_right.y()) // 2) for x in range(top_left.x(), bottom_right.x())]
    assert NOTE_EDGE_LIGHT in row[:2]  # and light on the left, dark on the right
    assert NOTE_EDGE_DARK in row[-2:]
    assert {colour.name() for colour in row[2:-2]} == {NOTE_FILL.name()}


def test_selected_notes_use_the_wavetone_highlight(window) -> None:
    window.view.clear_notes()
    note = window.view.add_note(60, 2.0, 2.0)
    top = PITCH_MAX - note.pitch + NOTE_INSET
    bottom = PITCH_MAX - note.pitch + 1.0 - NOTE_INSET
    window.view.centerOn(QPointF(note.start + note.duration / 2, (top + bottom) / 2))
    note.setSelected(True)
    image = window.view.grab().toImage()
    top_left = device_point(window.view, note.start, top)
    bottom_right = device_point(window.view, note.end, bottom)
    assert pixel_at(window.view, image, note.start + 1.0, (top + bottom) / 2) == NOTE_SELECTED

    mid_x = (top_left.x() + bottom_right.x()) // 2
    column = [image.pixelColor(mid_x, y) for y in range(top_left.y(), bottom_right.y())]
    assert {colour.name() for colour in column} == {NOTE_SELECTED.name(), NOTE_SELECTED_EDGE.name()}


def test_spectrum_loader_reports_a_bad_file(window, tmp_path) -> None:
    broken = tmp_path / "broken.wav"
    broken.write_text("not audio")
    messages: list[str] = []
    loader = SpectrumLoader(broken)
    loader.failed.connect(messages.append)
    loader.run()
    assert len(messages) == 1 and "Error" in messages[0]
