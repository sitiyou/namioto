# SPDX-License-Identifier: AGPL-3.0-only
"""Layout checks for the control bars and the main window."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pytest
from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QFocusEvent, QFont, QImage, QKeyEvent, QMouseEvent, QWheelEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QLabel,
    QMessageBox,
    QSlider,
    QTabWidget,
    QToolButton,
)

from namioto import midi, project
from namioto import settings as store
from namioto.beats import BeatTempo, LocalWindow
from namioto.channels import Channel
from namioto.interaction import Interaction, Tool
from namioto.spectrum import MIDI_OFFSET, NOTE_COUNT, NoteSpectrum
from namioto.ui import theme
from namioto.ui.app import MIDI_FILTER, MainWindow, TempoLoader
from namioto.ui.audio import MidiPortOut, MidiSink, find_port, find_synth_port
from namioto.ui.controls import Cluster, EditBar, TransportBar, ValueSlider
from namioto.ui.midi_dialog import MidiImportDialog
from namioto.ui.roll import (
    CONTENT_MARGIN,
    GRID_BAR,
    GRID_BEAT,
    GRID_LINE,
    HOVER_KEY,
    LENGTH_BEATS,
    MIN_DURATION,
    NOTE_INSET,
    NOTE_SELECTED,
    NOTE_SELECTED_EDGE,
    PANEL,
    PITCH_MAX,
    PLAYHEAD,
    RULER_HEIGHT,
    RULER_TIME_ROW,
    SNAP_CHOICES,
    SPECTRUM_BAR,
    SPECTRUM_BEAT,
    SPECTRUM_OCTAVE,
    SPECTRUM_TOP,
    PianoRollView,
)
from namioto.ui.settings_dialog import SettingsDialog
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
    theme.apply(app)
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


def roll_mouse(
    window, kind, scene_pos: QPointF, modifiers=Qt.KeyboardModifier.NoModifier, button=Qt.MouseButton.LeftButton
) -> None:
    """Send a mouse event to the roll at a scene position, as a real click would arrive."""
    position = window.view.mapFromScene(scene_pos)
    event = QMouseEvent(
        kind,
        QPointF(position),
        window.view.viewport().mapToGlobal(QPointF(position)),
        button,
        button,
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


def clusters(window) -> list[tuple[str, Cluster]]:
    return [(cluster.name, cluster) for cluster in window.controls.findChildren(Cluster)]


def card(window, name: str) -> Cluster:
    return next(cluster for _, cluster in clusters(window) if cluster.name == name)


def grid_rows(window) -> dict[int, list[Cluster]]:
    rows: dict[int, list[Cluster]] = {}
    for _, cluster in clusters(window):
        rows.setdefault(cluster.geometry().top(), []).append(cluster)
    return rows


def test_the_roll_holds_the_keyboard_when_the_window_opens(window) -> None:
    assert window.focusWidget() is window.view, "the bar would open with a focus ring on its first button"


def test_bars_have_room_for_every_cluster(window) -> None:
    for name, cluster in clusters(window):
        assert cluster.height() >= cluster.sizeHint().height(), f"{name} is squeezed"
        assert cluster.geometry().bottom() < window.controls.height(), f"{name} overflows the control area"


def test_clusters_do_not_overlap(window) -> None:
    for row, boxes in grid_rows(window).items():
        boxes = sorted(boxes, key=lambda cluster: cluster.geometry().left())
        for left, right in zip(boxes, boxes[1:], strict=False):
            assert right.geometry().left() >= left.geometry().right(), f"row at {row}: clusters overlap"


def test_the_blocks_line_up_on_one_grid(window) -> None:
    area = window.controls
    left = area.grid.contentsMargins().left()
    project, playback = card(window, "project"), card(window, "playback")
    tools, bpm = card(window, "tools"), card(window, "bpm")
    assert project.geometry().left() == tools.geometry().left() == left
    assert playback.geometry().right() == bpm.geometry().right(), "the two rows end on different lines"
    assert tools.geometry().top() > project.geometry().top()

    mix = [card(window, name) for name in ("spectrum", "volume", "speed")]
    assert [cluster.geometry().left() for cluster in mix] == sorted(cluster.geometry().left() for cluster in mix)
    assert all(cluster.geometry().top() == project.geometry().top() for cluster in mix)
    assert all(cluster.geometry().bottom() == tools.geometry().bottom() for cluster in mix), (
        "the mix blocks cover both rows, so they are as tall as the two rows beside them"
    )
    assert playback.geometry().right() < mix[0].geometry().left()


def test_bars_fit_the_default_window(window) -> None:
    assert window.controls.sizeHint().width() <= window.width(), (
        f"the controls need {window.controls.sizeHint().width()}px of {window.width()}px"
    )


def test_value_sliders_keep_their_caption_next_to_them(window) -> None:
    for slider in window.mix.findChildren(ValueSlider):
        assert slider.caption.x() < slider.slider.x() < slider.value_label.x()
        assert slider.slider.width() >= slider.slider.minimumWidth()


def test_the_sliders_of_one_block_line_up_in_columns(window) -> None:
    for name in ("spectrum", "volume"):
        sliders = card(window, name).findChildren(ValueSlider)
        assert len(sliders) > 1
        for part in ("caption", "slider", "value_label"):
            columns = {getattr(slider, part).mapTo(window.controls, QPoint(0, 0)).x() for slider in sliders}
            assert len(columns) == 1, f"{name}: {part} starts in {len(columns)} different places"


def test_control_widths_grow_with_the_theme_font(qt_app) -> None:
    original = qt_app.font()
    try:
        font = QFont(original)
        font.setPointSizeF(original.pointSizeF() + 6)
        qt_app.setFont(font)
        slider = ValueSlider("Speed", 0.1, 2.0, 1.0, suffix="x", scale=100)
        bar = TransportBar()
        edit = EditBar(SNAP_CHOICES)
        for field in (bar.bpm, bar.latency, edit.snap):
            assert field.width() >= field.sizeHint().width(), f"{type(field).__name__} is narrower than its text"
        metrics = slider.value_label.fontMetrics()
        for value in (slider.slider.minimum(), slider.slider.maximum()):
            slider.slider.setValue(value)
            assert metrics.horizontalAdvance(slider.value_label.text()) <= slider.value_label.width()
    finally:
        qt_app.setFont(original)


def test_tool_buttons_switch_the_roll_mode(window) -> None:
    window.edit.select.click()
    assert window.view.tool is Tool.SELECT
    window.edit.pen.click()
    assert window.view.tool is Tool.PEN


def test_one_action_emits_one_state_and_the_bar_renders_it(window) -> None:
    seen: list[Interaction] = []
    window.edit.interaction_changed.connect(seen.append)
    try:
        window.edit.set_interaction(Interaction.viewing())
        seen.clear()

        window.edit.select.click()  # one click, one state - no edit-mode signal beside it
        assert seen == [Interaction.editing_with(Tool.SELECT)]
        assert window.edit.select.isChecked() and not window.edit.pen.isChecked()
        assert window.edit.snap.isEnabled()

        window.edit.mode.click()
        assert seen == [Interaction.editing_with(Tool.SELECT), Interaction.viewing()]
        assert not (window.edit.pen.isChecked() or window.edit.select.isChecked())
        assert not window.edit.snap.isEnabled()
    finally:
        window.edit.interaction_changed.disconnect(seen.append)


def test_mode_buttons_are_icons_not_text(window) -> None:
    buttons = (
        window.edit.mode,
        window.edit.pen,
        window.edit.select,
        window.edit.division,
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


def test_the_division_button_flips_between_beats_and_seconds(window) -> None:
    assert window.edit.division.isChecked()  # beats by default
    window.edit.division.click()
    assert not window.edit.division.isChecked()
    assert window.view.division == "seconds"
    window.edit.division.click()
    assert window.edit.division.isChecked()
    assert window.view.division == "beats"


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
    assert window.edit.division.isChecked()


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
    unit = next(label for label in window.controls.findChildren(QLabel) if label.text() == "ms")
    # both in the control area's coordinates: the label is a sibling of the field, not a child of it
    assert unit.mapTo(window.controls, QPoint(0, 0)).x() >= field.mapTo(window.controls, field.rect().topRight()).x()
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
    assert window.transport.suggestion.isVisible()
    assert window.transport.bpm.value() == 120.0  # nothing is applied by itself
    assert "60% of them agree" in window.transport.suggestion.toolTip()

    window.transport.suggestion.apply_button.click()
    assert window.transport.bpm.value() == 96.0
    assert not window.transport.suggestion.isVisible()
    window.transport.bpm.setValue(120.0)


def test_tempo_already_in_the_field_is_not_offered_again(window) -> None:
    window.transport.bpm.setValue(120.0)
    window._on_tempo_loaded(fake_estimate(bpm=120.0, agree=10))
    assert not window.transport.suggestion.isVisible()


def test_a_tempo_of_its_own_keeps_the_suggestion_away(window) -> None:
    window.transport.bpm.setValue(96.0)  # the user typed one, or took an earlier estimate
    window._on_tempo_loaded(fake_estimate(bpm=140.0))
    assert not window.transport.suggestion.isVisible()
    assert window.transport.bpm.value() == 96.0
    window.transport.bpm.setValue(120.0)


def test_tempo_suggestion_can_be_dismissed_without_applying(window) -> None:
    window.transport.bpm.setValue(120.0)
    window._on_tempo_loaded(fake_estimate(bpm=100.0))
    window.transport.suggestion.dismiss_button.click()
    assert not window.transport.suggestion.isVisible()
    assert window.transport.bpm.value() == 120.0


def test_typing_a_tempo_drops_the_suggestion(window) -> None:
    window._on_tempo_loaded(fake_estimate())
    window.transport.bpm.setValue(140.0)
    assert not window.transport.suggestion.isVisible()
    window.transport.bpm.setValue(120.0)


def test_the_tempo_suggestion_floats_without_widening_the_bar(window) -> None:
    before = window.controls.sizeHint().width()
    window._on_tempo_loaded(fake_estimate(windows=10, agree=2))
    assert window.transport.suggestion.isVisible()
    assert window.controls.sizeHint().width() == before, "the suggestion is not worth a wider row"
    window.transport.suggestion.hide()


def test_the_tempo_suggestion_does_not_take_the_keyboard(window) -> None:
    suggestion = window.transport.suggestion
    QApplication.setActiveWindow(window)
    window.view.setFocus()
    window._on_tempo_loaded(fake_estimate())

    assert suggestion.isVisible()
    assert not suggestion.isWindow(), "a window of its own takes the keyboard and closes on a click"
    assert suggestion.parentWidget() is window
    assert QApplication.activeWindow() is window
    assert window.view.hasFocus(), "the roll keeps the keyboard the bar gave it"
    suggestion.hide()


def test_working_in_the_roll_leaves_the_suggestion_up(window) -> None:
    window._on_tempo_loaded(fake_estimate())
    assert window.transport.suggestion.isVisible()

    window.edit.set_interaction(Interaction.editing_with(Tool.PEN))  # a drawing gesture, in the spectrum
    draw_note(window, QPointF(4.0, 60.0), QPointF(8.0, 60.0))
    window.edit.set_interaction(Interaction.viewing())
    draw_note(window, QPointF(4.0, 60.0))  # and a plain click, which moves the playhead
    assert window.transport.suggestion.isVisible()

    window.view.set_notes(())
    window.transport.suggestion.dismiss_button.click()
    assert not window.transport.suggestion.isVisible()


def drawn_pixels(window, image: QImage, button: QToolButton) -> list[QColor]:
    """The pixels one button draws, read out of the window's own grab."""
    top = button.mapTo(window, QPoint(0, 0))
    return [image.pixelColor(top.x() + x, top.y() + y) for x in range(button.width()) for y in range(button.height())]


def test_the_buttons_that_do_something_carry_a_frame(window) -> None:
    window._on_tempo_loaded(fake_estimate())
    assert window.transport.suggestion.isVisible()

    image = window.grab().toImage()
    frame = QColor(theme.TOKENS["BUTTON_BG"])
    commands = (
        window.transport.open,
        window.transport.save,
        window.transport.settings_button,
        window.transport.detect,
        window.transport.speed_reset,
        window.transport.suggestion.dismiss_button,
    )
    for button in commands:
        assert frame in drawn_pixels(window, image, button), f"{button.toolTip()} wears no frame"
    window.transport.suggestion.hide()


def test_the_switches_and_the_transport_stay_bare(window) -> None:
    window.edit.channels.setChecked(True)  # the cards, and their switches, have to be drawn
    image = window.grab().toImage()
    frame = QColor(theme.TOKENS["BUTTON_BG"])
    card_switches = tuple(
        button
        for button in window.channel_panel.findChildren(QToolButton)
        if button.toolTip().startswith(("Lock:", "Show or hide", "Mute this channel"))
    )
    assert card_switches, "the cards are drawn"
    bare = (
        window.transport.rewind,
        window.transport.play_pause,
        window.transport.auto_page,
        window.transport.overtone,
        window.edit.mode,
        window.edit.select,
        window.edit.channels,
        window.edit.division,
        *card_switches,
    )
    for button in bare:
        assert frame not in drawn_pixels(window, image, button), f"{button.toolTip()} wears a frame"

    tinted = drawn_pixels(window, image, window.edit.division)
    assert max(colour.blue() - colour.red() for colour in tinted) > 40, "a switch that is on keeps the accent"
    window.edit.channels.setChecked(False)


def test_a_button_that_cannot_be_clicked_reads_as_off(window) -> None:
    button = window.transport.detect
    button.setEnabled(True)
    lit = drawn_pixels(window, window.grab().toImage(), button)
    button.setEnabled(False)
    assert drawn_pixels(window, window.grab().toImage(), button) != lit, "it has to look unclickable"


def test_tempo_loader_reports_a_bad_file(window, tmp_path) -> None:
    broken = tmp_path / "broken.wav"
    broken.write_text("not audio")
    messages: list[str] = []
    loader = TempoLoader(broken)
    loader.failed.connect(messages.append)
    loader.run()
    assert len(messages) == 1 and "Error" in messages[0]


def test_hover_marks_the_row_and_its_overtones(window) -> None:
    window.view.overtone_highlight = True  # in either mode, editing or not
    window.view.clear_notes()
    window.view.centerOn(QPointF(8.0, float(PITCH_MAX - 66)))  # G3 and its overtones in view
    window.view.set_hover_pitch(55)  # G3
    assert window.view.highlight_pitches() == [55, 67, 74, 79]  # 2f, 3f and 4f above it
    assert window.cursor_note.text() == "G3   196.00 Hz"

    def row_brightness(pitch: int) -> int:
        return pixel_at(window.view, window.view.grab().toImage(), 8.0, PITCH_MAX - pitch + 0.5).lightness()

    rows = (54, 55, 67, 74, 79)
    marked = {pitch: row_brightness(pitch) for pitch in rows}
    window.view.set_hover_pitch(None)
    plain = {pitch: row_brightness(pitch) for pitch in rows}
    assert all(marked[pitch] > plain[pitch] for pitch in (55, 67, 74, 79))
    assert marked[54] == plain[54]  # the row above stays as it was
    assert window.cursor_note.text() == ""
    window.view.overtone_highlight = False


def test_hover_turns_the_key_of_that_row_red_in_either_mode(window) -> None:
    window.view.centerOn(QPointF(8.0, float(PITCH_MAX - 65)))
    window.view.overtone_highlight = True

    def red_rows(pitch: int) -> set[int]:
        window.view.set_hover_pitch(pitch)
        image = window.keyboard.grab().toImage()
        window.view.set_hover_pitch(None)
        return {y for y in range(image.height()) if image.pixelColor(2, y) == HOVER_KEY}

    def red_bands(pitch: int) -> int:
        rows = sorted(red_rows(pitch))
        return sum(1 for index, y in enumerate(rows) if index == 0 or y != rows[index - 1] + 1)

    window.view.overtone_highlight = False
    if window.view.edit_mode:
        window.edit.mode.click()
    assert red_bands(55) == 1 and red_bands(54) == 1  # G3 is a white key, F#3 a black one: both mark

    window.view.set_hover_pitch(None)
    plain = window.keyboard.grab().toImage()
    assert not {y for y in range(plain.height()) if plain.pixelColor(2, y) == HOVER_KEY}

    window.view.overtone_highlight = True  # the overtones mark the keyboard as well, in either mode
    assert red_bands(55) == 4
    window.edit.pen.click()
    assert red_bands(55) == 4  # and the same while editing
    window.edit.mode.click()
    window.view.overtone_highlight = False


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

    def set_program(self, notes, speed, channels=()) -> None:
        self.programs.append((tuple(notes), speed))
        self.duration = max((start + duration for _pitch, start, duration, *_rest in notes), default=0.0) + 0.5
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
        self.speed = 1.0
        self.is_loaded = True
        self.duration = duration
        self.position = 0.0
        self.is_playing = False
        self.calls: list[str] = []

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
    assert song.speed == 0.8  # the song takes the same speed, with nothing to rerender first
    assert notes.calls.count("set_program") == 3

    window.transport.speed.set_value(1.0)
    window._stop()
    window.view.clear_notes()


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
    assert fake.programs == [(((69, 0.0, 0.5, 0),), 1.0)]  # one beat at 120 BPM, handed over in seconds
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
    assert window.view.edit_mode and window.view.tool is Tool.PEN
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


def test_a_drawn_note_is_one_undo_step(window) -> None:
    window.edit.pen.click()
    window.view.set_channels((Channel(channel=0),))
    window.view.clear_notes()
    window.view.undo_stack.clear()

    draw_note(window, QPointF(2.0, 40.0), QPointF(3.0, 40.0))
    assert len(window.view.notes()) == 1
    assert window.view.undo_stack.count() == 1
    window.view.undo()
    assert window.view.notes() == []
    assert window.view.undo_stack.canRedo()
    window.view.redo()
    assert len(window.view.notes()) == 1
    window.view.clear_notes()


def test_moving_notes_is_one_undo_step(window) -> None:
    window.edit.pen.click()
    window.view.set_channels((Channel(channel=0),))
    window.view.clear_notes()
    window.view.add_note(69, 2.0, 2.0)
    window.view.undo_stack.clear()

    row = float(PITCH_MAX - 69) + 0.5
    window.view.centerOn(QPointF(3.0, row))
    roll_mouse(window, QEvent.Type.MouseButtonPress, QPointF(3.0, row))
    roll_mouse(window, QEvent.Type.MouseMove, QPointF(5.0, row - 2))
    roll_mouse(window, QEvent.Type.MouseButtonRelease, QPointF(5.0, row - 2))
    assert window.view.undo_stack.count() == 1
    assert (window.view.notes()[0].start, window.view.notes()[0].pitch) == (4.0, 71)

    window.view.undo()
    assert (window.view.notes()[0].start, window.view.notes()[0].pitch) == (2.0, 69)
    window.view.redo()
    assert (window.view.notes()[0].start, window.view.notes()[0].pitch) == (4.0, 71)
    window.view.clear_notes()


def test_trimming_a_note_is_one_undo_step(window) -> None:
    window.edit.pen.click()
    window.view.set_channels((Channel(channel=0),))
    window.view.clear_notes()
    window.view.add_note(69, 2.0, 2.0)  # spans 2.0 to 4.0
    window.view.undo_stack.clear()

    row = float(PITCH_MAX - 69) + 0.5
    window.view.centerOn(QPointF(3.0, row))
    roll_mouse(window, QEvent.Type.MouseButtonPress, QPointF(3.95, row))  # within the right grab band
    roll_mouse(window, QEvent.Type.MouseMove, QPointF(5.0, row))
    roll_mouse(window, QEvent.Type.MouseButtonRelease, QPointF(5.0, row))
    assert window.view.undo_stack.count() == 1
    assert window.view.notes()[0].end == 5.0

    window.view.undo()
    assert window.view.notes()[0].end == 4.0
    window.view.clear_notes()


def test_deleting_the_selection_is_one_undo_step(window) -> None:
    window.edit.pen.click()
    window.view.set_channels((Channel(channel=0),))
    window.view.clear_notes()
    window.view.add_note(60, 0.0, 1.0)
    window.view.add_note(62, 1.0, 1.0)
    window.view.undo_stack.clear()

    for item in window.view.notes():
        item.setSelected(True)
    window.view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier))
    assert window.view.notes() == []
    assert window.view.undo_stack.count() == 1
    window.view.undo()
    assert [note.pitch for note in window.view.notes()] == [60, 62]
    window.view.clear_notes()


def test_a_right_click_delete_can_be_undone(window) -> None:
    window.edit.pen.click()
    window.view.set_channels((Channel(channel=0),))
    window.view.clear_notes()
    window.view.add_note(69, 2.0, 2.0)
    window.view.undo_stack.clear()

    row = float(PITCH_MAX - 69) + 0.5
    scene_pos = QPointF(3.0, row)
    roll_mouse(window, QEvent.Type.MouseButtonPress, scene_pos, button=Qt.MouseButton.RightButton)
    roll_mouse(window, QEvent.Type.MouseButtonRelease, scene_pos, button=Qt.MouseButton.RightButton)
    assert window.view.notes() == []
    window.view.undo()
    assert [note.pitch for note in window.view.notes()] == [69]
    window.view.clear_notes()


def test_clearing_every_note_can_be_undone(window) -> None:
    window.edit.pen.click()
    window.view.set_channels((Channel(channel=0),))
    window.view.clear_notes()
    window.view.add_note(60, 0.0, 1.0)
    window.view.add_note(62, 1.0, 1.0)
    window.view.undo_stack.clear()

    window.view.clear_notes()
    assert window.view.notes() == []
    window.view.undo()
    assert [note.pitch for note in window.view.notes()] == [60, 62]
    window.view.clear_notes()


def test_selecting_a_note_is_not_an_undo_step(window) -> None:
    window.edit.select.click()
    window.view.set_channels((Channel(channel=0),))
    window.view.clear_notes()
    note = window.view.add_note(69, 2.0, 1.0)
    window.view.undo_stack.clear()

    row = float(PITCH_MAX - 69) + 0.5
    window.view.centerOn(QPointF(2.5, row))
    roll_mouse(window, QEvent.Type.MouseButtonPress, QPointF(2.5, row))
    roll_mouse(window, QEvent.Type.MouseButtonRelease, QPointF(2.5, row))
    assert note.isSelected()
    assert window.view.undo_stack.count() == 0
    window.view.clear_notes()


def test_copy_and_paste_drops_the_selection_at_the_playhead() -> None:
    view = PianoRollView()
    view.apply_interaction(Interaction.editing_with(Tool.PEN))
    view.set_channels((Channel(channel=0),))
    view.add_note(60, 2.0, 1.0)
    view.add_note(64, 3.0, 0.5)
    for note in view.notes():
        note.setSelected(True)
    view.set_playhead(3.0)  # six beats at 120 BPM
    view.undo_stack.clear()

    assert view.copy_selection()
    assert view.paste_notes()
    assert [(note.pitch, note.start, note.duration) for note in view.notes()] == [
        (60, 2.0, 1.0),
        (64, 3.0, 0.5),
        (60, 6.0, 1.0),
        (64, 7.0, 0.5),
    ]
    assert [note.pitch for note in view.selected_notes()] == [60, 64]  # the paste is what is selected
    assert view.undo_stack.count() == 1
    view.undo()
    assert len(view.notes()) == 2


def test_paste_snaps_the_anchor_and_the_spacing() -> None:
    view = PianoRollView()
    view.apply_interaction(Interaction.editing_with(Tool.PEN))
    view.set_channels((Channel(channel=0),))
    view.snap = 0.5  # the 1/8 default
    view.add_note(60, 2.0, 1.0)
    view.add_note(62, 3.3, 0.5)  # off the grid, so the paste has to align it
    for note in view.notes():
        note.setSelected(True)
    view.set_playhead(1.05)  # 2.1 beats, which snap to 2.0

    assert view.copy_selection()
    assert view.paste_notes()
    assert [(note.pitch, note.start) for note in view.notes()[2:]] == [(60, 2.0), (62, 3.5)]


def test_paste_without_a_copy_does_nothing() -> None:
    view = PianoRollView()
    view.apply_interaction(Interaction.editing_with(Tool.PEN))
    view.set_playhead(1.0)
    assert not view.paste_notes()
    assert view.notes() == []


def test_copy_and_paste_need_edit_mode() -> None:
    view = PianoRollView()
    view.set_channels((Channel(channel=0),))
    view.add_note(60, 2.0, 1.0).setSelected(True)
    assert not view.copy_selection()
    assert not view.paste_notes()


def test_ctrl_c_and_ctrl_v_carry_the_selection(window) -> None:
    window.edit.pen.click()
    window.view.set_channels((Channel(channel=0),))
    window.view.clear_notes()
    window.view.add_note(69, 2.0, 1.0).setSelected(True)
    window.view.set_playhead(2.0)  # four beats at 120 BPM

    QApplication.setActiveWindow(window)  # an offscreen window is never active on its own
    QTest.keyClick(window, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    QTest.keyClick(window, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert [(note.pitch, note.start) for note in window.view.notes()] == [(69, 2.0), (69, 4.0)]
    window.view.set_playhead(None)
    window.view.clear_notes()


def test_a_new_edit_drops_the_redo_branch(window) -> None:
    window.edit.pen.click()
    window.view.set_channels((Channel(channel=0),))
    window.view.clear_notes()
    window.view.undo_stack.clear()

    draw_note(window, QPointF(2.0, 40.0), QPointF(3.0, 40.0))
    window.view.undo()
    assert window.view.undo_stack.canRedo()
    draw_note(window, QPointF(6.0, 40.0), QPointF(7.0, 40.0))
    assert not window.view.undo_stack.canRedo()
    window.view.clear_notes()


def test_channel_edits_and_a_removal_can_be_undone(window) -> None:
    window.view.set_channels((Channel(channel=0),))
    window.view.clear_notes()
    window.view.add_channel(program=4)
    window.view.add_note(60, 0.0, 1.0, 0)
    window.view.add_note(64, 1.0, 1.0, 1)
    window.view.undo_stack.clear()

    window.view.set_channel_field(1, mute=True)
    assert window.view.channels[1].mute is True
    window.view.undo()
    assert window.view.channels[1].mute is False

    assert window.view.remove_channel(0)
    assert [note.pitch for note in window.view.notes()] == [64]
    window.view.undo()
    assert [(note.pitch, note.channel) for note in window.view.notes()] == [(60, 0), (64, 1)]
    window.view.set_channels((Channel(channel=0),))
    window.view.clear_notes()


def test_a_whole_document_replacement_is_one_undo_step(window) -> None:
    window.view.set_channels((Channel(channel=0),))
    window.view.clear_notes()
    window.view.undo_stack.clear()

    window.view.replace((Channel(channel=0),), [(60, 0.0, 1.0, 0), (64, 1.0, 1.0, 0)], "Import MIDI")
    assert window.view.undo_stack.count() == 1
    window.view.undo()
    assert window.view.notes() == []


def test_undo_keeps_a_note_on_the_audio_across_a_tempo_change(window) -> None:
    window.edit.pen.click()
    window.transport.bpm.setValue(120.0)
    window.view.set_channels((Channel(channel=0),))
    window.view.clear_notes()
    window.view.add_note(69, 2.0, 2.0)
    window.view.undo_stack.clear()
    seconds = 2.0 * window.view.seconds_per_beat

    row = float(PITCH_MAX - 69) + 0.5
    window.view.centerOn(QPointF(3.0, row))
    roll_mouse(window, QEvent.Type.MouseButtonPress, QPointF(3.0, row))
    roll_mouse(window, QEvent.Type.MouseMove, QPointF(4.0, row))
    roll_mouse(window, QEvent.Type.MouseButtonRelease, QPointF(4.0, row))

    window.transport.bpm.setValue(60.0)  # beats halve, seconds do not
    window.view.undo()
    restored = window.view.notes()[0]
    assert restored.start * window.view.seconds_per_beat == pytest.approx(seconds)
    window.transport.bpm.setValue(120.0)
    window.view.clear_notes()


def test_ctrl_z_and_ctrl_shift_z_drive_the_history(window) -> None:
    window.edit.pen.click()
    window.view.set_channels((Channel(channel=0),))
    window.view.clear_notes()
    window.view.undo_stack.clear()
    draw_note(window, QPointF(2.0, 40.0), QPointF(3.0, 40.0))

    QApplication.setActiveWindow(window)  # an offscreen window is never active on its own
    QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert window.view.notes() == []
    QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert len(window.view.notes()) == 1
    QTest.keyClick(window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert window.view.notes() == []
    QTest.keyClick(window, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert len(window.view.notes()) == 1
    window.view.clear_notes()


def test_opening_a_project_starts_the_history_over(own_window, tmp_path) -> None:
    own_window.view.set_channels((Channel(channel=0),))
    own_window.view.add_note(60, 0.0, 1.0)
    assert own_window.view.undo_stack.canUndo()

    path = tmp_path / "song.nto"
    project.save(
        project.Project(values=store.project_values(store.Settings()), notes=(project.Note(1.0, 0.5, 62),)),
        path,
    )
    assert own_window.load_project(path)
    assert not own_window.view.undo_stack.canUndo()
    assert not own_window.view.undo_stack.canRedo()


def test_dragging_a_box_fills_the_selection_under_any_style(window) -> None:
    QApplication.setStyle("Windows")  # its own rubber band ignores a stylesheet, and the box is ours
    window.edit.select.click()
    window.view.centerOn(QPointF(5.0, 63.0))
    try:
        roll_mouse(window, QEvent.Type.MouseButtonPress, QPointF(2.0, 60.0))
        roll_mouse(window, QEvent.Type.MouseMove, QPointF(8.0, 66.0))
        rubber = window.view._rubber
        assert rubber.isVisible()
        area = rubber.geometry().intersected(window.view.viewport().rect())
        image = window.view.viewport().grab().toImage()
        inside = image.pixelColor(area.center())
        outside = image.pixelColor(area.left() - 20, area.center().y())
        assert inside.blue() - inside.red() > outside.blue() - outside.red(), "the region is tinted"
        border = image.pixelColor(area.topLeft())
        assert border.blue() > border.red(), "the outline is the accent, not the style's own"
        roll_mouse(window, QEvent.Type.MouseButtonRelease, QPointF(8.0, 66.0))
    finally:
        QApplication.setStyle("Fusion")
        window.edit.mode.click()


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
    assert not window.edit.snap.isEnabled()

    draw_note(window, QPointF(8.3, row), QPointF(9.6, row))
    assert [n.pitch for n in window.view.notes()] == [69]  # the pen drew nothing
    window.view.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier))
    assert len(window.view.notes()) == 1  # and delete did nothing either
    window.view.set_hover_pitch(69)
    assert window.view.highlight_pitches() == [69]  # only the row under the mouse, no overtones
    window.view.set_hover_pitch(None)
    window._stop()

    window.edit.mode.click()  # back in
    assert window.view.edit_mode and window.view.tool is Tool.PEN
    assert window.edit.snap.isEnabled()
    draw_note(window, QPointF(12.3, row), QPointF(13.6, row))  # elsewhere: the first row is taken
    assert len(window.view.notes()) == 2
    window.view.clear_notes()


def test_picking_a_tool_turns_on_edit_mode(window) -> None:
    if window.view.edit_mode:
        window.edit.mode.click()
    assert not window.view.edit_mode
    window.edit.select.click()
    assert window.view.edit_mode and window.edit.mode.isChecked() and window.view.tool is Tool.SELECT
    window.edit.mode.click()
    window.edit.mode.click()  # entering the mode always lands on the pen
    assert window.view.tool is Tool.PEN and window.edit.pen.isChecked()


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
    window.edit.pen.click()  # notes are only drawn while editing, and the spectrum fades then
    window.view.clear_notes()
    window.view.set_spectrum(make_spectrum(frames=400, value=9.0))
    note = window.view.add_note(60, 1.0, 4.0)
    middle = (note.start + note.duration / 2, PITCH_MAX - note.pitch + 0.5)
    window.view.centerOn(QPointF(*middle))
    image = window.view.grab().toImage()
    assert pixel_at(window.view, image, *middle) == note.fill
    beside = pixel_at(window.view, image, note.start - 0.5, middle[1])
    assert beside != note.fill and beside.red() > 0  # the spectrum is behind the note, dimmed by the mode
    window.view.clear_notes()
    window.view.set_spectrum(None)
    window.edit.mode.click()


def test_notes_are_flat_red_with_a_bevel(window) -> None:
    window.edit.pen.click()  # a note is only on screen while editing
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
    assert note.edge_light in column[:2]  # the light bevel is on top
    assert note.edge_dark in column[-2:]  # the dark one below
    assert {colour.name() for colour in column[2:-2]} == {note.fill.name()}

    row = [image.pixelColor(x, (top_left.y() + bottom_right.y()) // 2) for x in range(top_left.x(), bottom_right.x())]
    assert note.edge_light in row[:2]  # and light on the left, dark on the right
    assert note.edge_dark in row[-2:]
    assert {colour.name() for colour in row[2:-2]} == {note.fill.name()}
    window.edit.mode.click()


def test_selected_notes_use_the_wavetone_highlight(window) -> None:
    window.edit.pen.click()  # a note is only on screen while editing
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
    window.edit.mode.click()


def test_notes_are_drawn_only_in_edit_mode(window) -> None:
    if window.view.edit_mode:
        window.edit.mode.click()
    window.view.clear_notes()
    note = window.view.add_note(60, 1.0, 2.0)
    assert not note.isVisible()  # outside it the roll is the graph, the way WaveTone keeps it

    window.edit.pen.click()
    assert note.isVisible()

    window.edit.mode.click()
    assert not note.isVisible()
    window.view.clear_notes()


def test_the_item_is_a_view_of_the_document_note(window) -> None:
    window.view.clear_notes()
    item = window.view.add_note(60, 1.0, 2.0)
    note = item.note
    assert window.view.document.notes == [note] and window.view.notes() == [item]

    note.set_range(4.0, 67)  # the model moves and the item follows it
    assert (item.start, item.pitch) == (4.0, 67)
    item.set_duration(3.0)  # and the item writes back into the model
    assert note.duration == 3.0

    window.view.clear_notes()
    assert window.view.document.notes == [] and window.view.notes() == []


def test_spectrum_loader_reports_a_bad_file(window, tmp_path) -> None:
    broken = tmp_path / "broken.wav"
    broken.write_text("not audio")
    messages: list[str] = []
    loader = SpectrumLoader(broken)
    loader.failed.connect(messages.append)
    loader.run()
    assert len(messages) == 1 and "Error" in messages[0]


@pytest.fixture
def own_window(tmp_path, monkeypatch):
    """A window with a settings file of its own, for the tests that read or write one."""
    monkeypatch.setenv("NAMIOTO_SETTINGS", str(tmp_path / "settings.json"))
    opened = MainWindow()
    opened.resize(1200, 720)
    yield opened
    opened.project_dirty = False  # a test may leave unsaved notes: closing must not ask about them
    opened.close()


def row_writer(dialog, section: str, name: str):
    """The write half of one row of the settings window, to change it the way a widget would."""
    for row_section, field, _read, write in dialog._rows:
        if (row_section, field.name) == (section, name):
            return write
    raise AssertionError(f"the settings window has no row for {section}.{name}")


def test_the_gear_button_opens_the_settings_window(own_window, monkeypatch) -> None:
    opened: list[SettingsDialog] = []
    monkeypatch.setattr(SettingsDialog, "exec", lambda self: opened.append(self) or 0)
    own_window.transport.settings_button.click()
    assert len(opened) == 1
    assert opened[0].parent() is own_window


def test_the_settings_window_lists_every_visible_field(own_window) -> None:
    dialog = SettingsDialog(own_window.settings, parent=own_window)
    names = {(section, field.name) for section, field, _read, _write in dialog._rows}
    expected = {
        (section.name, field.name) for section in store.SECTIONS for field in section.fields if not field.hidden
    }
    assert names == expected
    pages = [dialog.findChild(QTabWidget).tabText(index) for index in range(dialog.findChild(QTabWidget).count())]
    assert pages == ["Analysis", "Tempo", "Advanced"]  # the rest of the spec is what the program remembers
    dialog.close()


def test_applying_the_settings_window_reaches_the_window_and_the_file(own_window) -> None:
    dialog = SettingsDialog(own_window.settings, parent=own_window)
    dialog.applied.connect(own_window.settings_store.apply)  # the window wires this up when it opens it
    row_writer(dialog, "analysis", "t_num")(25.0)
    row_writer(dialog, "tempo", "window_seconds")(20.0)
    row_writer(dialog, "midi", "wavetone")(False)
    dialog.apply()

    assert own_window.settings.analysis.t_num == 25.0
    assert own_window.settings.tempo.window_seconds == 20.0
    assert own_window.settings.midi.wavetone is False
    saved = json.loads(store.default_path().read_text())
    assert saved["analysis"]["t_num"] == 25.0
    assert saved["tempo"]["window_seconds"] == 20.0
    assert saved["midi"]["wavetone"] is False
    dialog.close()


def test_the_channels_row_offers_the_analysis_modes(own_window) -> None:
    dialog = SettingsDialog(own_window.settings, parent=own_window)
    combos = [combo for combo in dialog.findChildren(QComboBox) if combo.findData("side") >= 0]
    assert len(combos) == 1
    combos[0].setCurrentIndex(combos[0].findData("side"))
    assert store.get_value(dialog.values(), "analysis", "channels") == "side"
    dialog.close()


def test_restoring_defaults_puts_every_widget_back(own_window) -> None:
    dialog = SettingsDialog(own_window.settings, parent=own_window)
    row_writer(dialog, "analysis", "t_num")(12.0)
    row_writer(dialog, "tempo", "window_seconds")(8.0)
    row_writer(dialog, "midi", "wavetone")(False)
    assert store.get_value(dialog.values(), "midi", "wavetone") is False

    dialog.restore_defaults()
    values = dialog.values()
    assert store.get_value(values, "midi", "wavetone") is True
    assert store.get_value(values, "analysis", "t_num") == 40.0
    assert store.get_value(values, "tempo", "window_seconds") == 12.0
    dialog.close()


def test_closing_the_window_remembers_the_session(own_window) -> None:
    own_window.view.set_zoom(96.0, 20.0)
    own_window.close()

    saved = store.load()
    assert saved.session.geometry
    assert saved.editor.zoom_x == 96.0
    assert saved.editor.zoom_y == 20.0
    assert saved.session.center_x > 0.0


def test_the_settings_are_read_when_the_window_starts(tmp_path, monkeypatch) -> None:
    path = tmp_path / "settings.json"
    monkeypatch.setenv("NAMIOTO_SETTINGS", str(path))
    saved = store.Settings()
    store.set_value(saved, "spectrum", "contrast", 2.5)
    store.set_value(saved, "editor", "snap", 0.25)
    store.set_value(saved, "editor", "zoom_y", 24.0)
    store.set_value(saved, "tempo", "bpm", 84.0)
    store.set_value(saved, "playback", "speed", 1.25)
    store.set_value(saved, "editor", "auto_page", True)
    store.save(saved)

    opened = MainWindow()
    assert opened.view.contrast == 2.5
    assert opened.view.snap == 0.25
    assert opened.view.zoom[1] == 24.0
    assert opened.transport.bpm.value() == 84.0
    assert opened.transport.speed.value() == 1.25
    assert opened.transport.auto_page.isChecked() is True
    opened.close()


def test_the_command_line_seeds_the_run_without_writing_itself_back(own_window) -> None:
    before = store.load()
    own_window.apply_overrides(gain=300.0, contrast=2.0)
    assert own_window.view.gain == 300.0
    own_window.close()

    saved = store.load()
    assert saved.spectrum.gain == before.spectrum.gain
    assert saved.spectrum.contrast == before.spectrum.contrast


def test_every_bar_setting_has_one_binding(own_window) -> None:
    bound = {(binding.section, binding.name) for binding in own_window._bindings}
    assert bound <= set(store.FIELD_SPECS), f"a binding names no setting: {sorted(bound - set(store.FIELD_SPECS))}"
    # zoom lives on the roll, the last directory on the chooser and the session on the window: no bar
    elsewhere = {("editor", "zoom_x"), ("editor", "zoom_y"), ("paths", "last_audio_dir")}
    elsewhere |= {("session", name) for name in ("geometry", "center_x", "center_y")}
    missing = (
        {(section.name, item.name) for section in store.SECTIONS for item in section.fields if item.hidden}
        - bound
        - elsewhere
    )
    assert not missing, f"a bar setting with no binding: {sorted(missing)}"


def test_a_command_line_channel_beats_the_settings(own_window) -> None:
    store.set_value(own_window.settings, "analysis", "channels", "side")
    own_window.overrides["channels"] = "left"
    own_window.overrides["t_num"] = None
    options = own_window._analysis_options()
    assert options["channels"] == "left"
    assert options["t_num"] == 40.0
    assert options["fft_points"] == 8192
    assert options["a4"] == 440.0


def test_the_spectrum_loader_takes_every_analysis_parameter(tmp_path) -> None:
    loader = SpectrumLoader(tmp_path / "song.wav", channels="side", t_num=25.0, fft_points=2048, a4=442.0)
    assert (loader.channels, loader.t_num, loader.fft_points, loader.a4) == ("side", 25.0, 2048, 442.0)
    assert loader.path == tmp_path / "song.wav"


class _Signal:
    """A signal nothing is connected to: the loaders a file starts are faked in the tests below."""

    def connect(self, *_args) -> None:
        pass


def fake_loaders(monkeypatch) -> list[dict]:
    """Replace the loaders a file starts with ones that only record what they were asked to run."""
    captured: list[dict] = []

    class FakeLoader:
        def __init__(self, *args, parent=None, **kwargs):
            self.progress = self.loaded = self.failed = _Signal()
            captured.append(kwargs)

        def start(self) -> None:
            pass

    for name in ("SpectrumLoader", "SongLoader", "TempoLoader"):
        monkeypatch.setattr(f"namioto.ui.app.{name}", FakeLoader)
    return captured


def test_loading_a_file_hands_the_settings_to_the_analysers(own_window, monkeypatch) -> None:
    captured = fake_loaders(monkeypatch)
    store.set_value(own_window.settings, "analysis", "fft_points", 4096)
    store.set_value(own_window.settings, "analysis", "a4", 441.0)
    store.set_value(own_window.settings, "tempo", "window_seconds", 8.0)
    own_window.overrides["channels"] = "left"

    own_window.load_audio("/tmp/song.wav")

    analysis, _song, tempo = captured
    assert analysis == {"channels": "left", "t_num": 40.0, "fft_points": 4096, "a4": 441.0}
    assert tempo == {"window_seconds": 8.0, "window_hop_seconds": 6.0}
    assert store.get_value(own_window.settings, "paths", "last_audio_dir") == "/tmp"


def test_the_tempo_loader_follows_the_settings(own_window, monkeypatch) -> None:
    own_window.audio_path = "song.wav"
    store.set_value(own_window.settings, "tempo", "window_seconds", 8.0)
    store.set_value(own_window.settings, "tempo", "window_hop_seconds", 3.0)
    monkeypatch.setattr(TempoLoader, "start", lambda self: None)
    own_window._start_tempo()
    assert own_window.tempo_loader.window_seconds == 8.0
    assert own_window.tempo_loader.window_hop_seconds == 3.0


def test_the_tempo_and_the_latency_are_not_remembered_between_runs(own_window) -> None:
    own_window.transport.bpm.setValue(93.0)
    own_window.transport.latency.setValue(120)
    own_window.close()

    saved = store.load()
    assert saved.tempo.bpm == 120.0  # what a new song starts from
    assert saved.playback.latency_ms == 0


def test_another_song_starts_from_the_default_tempo_and_latency(own_window, monkeypatch) -> None:
    fake_loaders(monkeypatch)
    own_window.transport.bpm.setValue(93.0)
    own_window.transport.latency.setValue(120)

    own_window.load_audio("/tmp/other.wav")
    assert own_window.transport.bpm.value() == 120.0
    assert own_window.transport.latency.value() == 0

    own_window.transport.bpm.setValue(93.0)
    own_window.load_audio("/tmp/other.wav")  # the same file again: a re-analysis keeps what was typed
    assert own_window.transport.bpm.value() == 93.0


def test_a_project_brings_its_tempo_and_latency_back(own_window, tmp_path) -> None:
    other = store.Settings()
    other.tempo.bpm = 93.0
    other.playback.latency_ms = 120
    path = tmp_path / "song.nto"
    project.save(project.Project(values=store.project_values(other)), path)

    assert own_window.load_project(path) is True
    assert own_window.transport.bpm.value() == 93.0
    assert own_window.transport.latency.value() == 120
    own_window.settings_store.flush()
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["tempo"]["bpm"] == 120.0  # the song's tempo stays in the document
    assert saved["playback"]["latency_ms"] == 0


def test_the_overtone_highlight_can_be_turned_off(window) -> None:
    window.edit.set_interaction(Interaction.viewing())
    window.view.overtone_highlight = True
    window.view.set_hover_pitch(60)
    assert window.view.highlight_pitches() == [60, 72, 79, 84]  # no edit mode needed

    window.view.overtone_highlight = False
    assert window.view.highlight_pitches() == [60]

    window.edit.set_interaction(Interaction.editing_with(Tool.PEN))
    assert window.view.highlight_pitches() == [60]  # and the switch still governs it while editing
    window.edit.set_interaction(Interaction.viewing())
    window.view.set_hover_pitch(None)


def test_the_zoom_can_be_set_from_outside(window) -> None:
    window.view.set_zoom(96.0, 32.0)
    assert window.view.zoom == (96.0, 32.0)
    window.view.set_zoom(1.0, 500.0)  # both ends are held to what the roll can draw
    assert window.view.zoom == (window.view.MIN_ZOOM_X, window.view.MAX_ZOOM_Y)
    window.view.set_zoom(48.0, 16.0)


def test_the_song_buffer_follows_the_settings(own_window) -> None:
    own_window.song.buffer_ms = 200
    assert own_window.song.buffer_ms == 200
    own_window.song.buffer_ms = 1
    assert own_window.song.buffer_ms == 10


def test_a_midi_port_is_matched_by_name() -> None:
    ports = ("Midi Through:Midi Through Port-0 14:0", "TiMidity:TiMidity port 0 128:0")
    assert find_port(ports, "TiMidity:TiMidity port 0 128:0") == 1
    assert find_port(ports, "TiMidity") == 1  # the client number changes between sessions
    assert find_port(ports, "Nonesuch") == 1  # nothing matches, so the first synth is used
    assert find_port(("Midi Through:0",), "") is None


def test_the_transport_carries_the_project_buttons() -> None:
    bar = TransportBar()
    seen: list[str] = []
    bar.open_requested.connect(lambda: seen.append("open"))
    bar.save_requested.connect(lambda: seen.append("save"))
    bar.export_midi_requested.connect(lambda: seen.append("export"))
    bar.open.click()
    bar.save.click()
    bar.export_midi.click()
    assert seen == ["open", "save", "export"]


def test_saving_a_project_takes_the_notes_and_the_values_with_it(own_window, tmp_path) -> None:
    own_window.transport.bpm.setValue(120.0)
    own_window.view.set_notes([(64, 2.0, 1.0), (67, 3.0, 0.5)])  # beats, as the roll holds them
    own_window.mix.gain.set_value(300.0)
    path = tmp_path / "song.nto"
    assert own_window.save_project(path) is True
    assert own_window.project_path == path
    assert own_window.project_dirty is False
    assert own_window._document_name() == "song"
    saved = project.load(path)  # beat 2 at 120 BPM is one second in, and it is seconds that are kept
    assert saved.notes == (project.Note(1.0, 0.5, 64), project.Note(1.5, 0.25, 67))
    assert saved.values["tempo"]["bpm"] == 120.0
    assert saved.values["spectrum"]["gain"] == 300.0
    assert saved.values["editor"]["snap"] == own_window.view.snap
    assert "Saved song.nto" in own_window.statusBar().currentMessage()


def test_loading_a_project_brings_the_notes_and_the_values_back(own_window, tmp_path, monkeypatch) -> None:
    other = store.Settings()
    other.tempo.bpm = 120.0  # not 60: at 120 BPM a beat and a second differ, so the conversion shows
    other.analysis.a4 = 432.0
    other.spectrum.gain = 300.0
    other.editor.snap = 0.25
    path = tmp_path / "song.nto"
    opened = project.Project(
        values=store.project_values(other),
        audio="vocal.wav",
        notes=(project.Note(2.0, 0.5, 64), project.Note(3.5, 0.5, 67)),
    )
    (tmp_path / "vocal.wav").write_bytes(b"")
    project.save(opened, path)
    played: list[str] = []  # the analysis and the audio device are not what this checks
    monkeypatch.setattr(type(own_window), "load_audio", lambda _self, path: played.append(path))

    assert own_window.load_project(path) is True
    assert sorted(note.pitch for note in own_window.view.notes()) == [64, 67]
    beats = sorted((note.start, note.duration) for note in own_window.view.notes())
    assert beats[0] == pytest.approx((4.0, 1.0))  # 2 s at 120 BPM is beat 4, and half a second is a beat
    assert beats[1] == pytest.approx((7.0, 1.0))
    assert own_window.view.bpm == 120.0
    assert own_window.settings.analysis.a4 == 432.0
    assert own_window.view.gain == 300.0
    assert own_window.view.snap == 0.25
    assert played == [str(tmp_path / "vocal.wav")]
    assert own_window.project_dirty is False
    assert own_window.project_path == path
    assert own_window._document_name() == "song"
    assert "Opened song.nto — 2 notes" in own_window.statusBar().currentMessage()

    own_window.view.set_notes([(60, 0.0, 1.0)])
    assert own_window._document_name() == "song*"


def test_a_project_without_its_audio_still_opens(own_window, tmp_path) -> None:
    path = tmp_path / "song.nto"
    project.save(
        project.Project(
            values=store.project_values(store.Settings()),
            audio="gone.wav",
            notes=(project.Note(1.0, 0.5, 60),),
        ),
        path,
    )
    assert own_window.load_project(path) is True
    assert [note.pitch for note in own_window.view.notes()] == [60]
    assert own_window.audio_path is None
    assert own_window.song.is_loaded is False
    assert "audio not found" in own_window.statusBar().currentMessage()


def test_a_file_that_is_not_a_project_says_so(own_window, tmp_path) -> None:
    path = tmp_path / "not.nto"
    path.write_text("{}")
    assert own_window.load_project(path) is False
    assert "could not be opened" in own_window.statusBar().currentMessage()
    assert own_window.project_path is None


def test_opening_a_project_does_not_rewrite_the_app_defaults(own_window, tmp_path) -> None:
    other = store.Settings()
    other.spectrum.gain = 300.0
    other.analysis.a4 = 432.0
    path = tmp_path / "song.nto"
    project.save(project.Project(values=store.project_values(other)), path)

    assert own_window.load_project(path) is True
    assert own_window.settings.spectrum.gain == 300.0
    assert own_window.mix.gain.value() == 300.0
    assert not (tmp_path / "settings.json").exists()  # opening is not saving

    own_window.settings_store.flush()
    written = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert written["spectrum"]["gain"] == store.Settings().spectrum.gain
    assert written["analysis"]["a4"] == store.Settings().analysis.a4
    assert written["playback"]["speed"] == store.Settings().playback.speed


def test_a_change_made_with_a_project_open_still_leaves_the_default_alone(own_window, tmp_path) -> None:
    other = store.Settings()
    other.spectrum.gain = 300.0
    path = tmp_path / "song.nto"
    project.save(project.Project(values=store.project_values(other)), path)
    assert own_window.load_project(path) is True

    own_window.mix.gain.set_value(340.0)  # the document's gain, not the app's
    own_window.settings_store.flush()
    written = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert own_window.settings.spectrum.gain == 340.0
    assert written["spectrum"]["gain"] == store.Settings().spectrum.gain


def test_drawing_marks_the_document_that_has_a_name(own_window) -> None:
    assert own_window.project_dirty is False
    own_window.transport.bpm.setValue(90.0)
    assert own_window.project_dirty is True
    assert own_window._document_name() == "Untitled*"  # nothing to ask about until it has a file


def test_unsaved_notes_are_asked_about_once(own_window, monkeypatch) -> None:
    asked: list[tuple] = []

    def warning(*args, **_kwargs):
        asked.append(args)
        return QMessageBox.StandardButton.Discard

    monkeypatch.setattr(QMessageBox, "warning", warning)
    assert own_window._confirm_discard() is True  # a sketch is not worth interrupting anyone over
    assert asked == []

    own_window.view.set_notes([(64, 0.0, 1.0)])
    own_window.project_path = Path("song.nto")
    assert own_window._confirm_discard() is True
    assert len(asked) == 1
    assert "song.nto" in asked[0][2]

    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    assert own_window._confirm_discard() is False
    own_window.project_dirty = False


def test_the_open_dialog_is_left_alone_when_the_notes_are_kept(own_window, monkeypatch) -> None:
    own_window.view.set_notes([(64, 0.0, 1.0)])
    own_window.project_path = Path("song.nto")
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.StandardButton.Cancel)
    asked: list[str] = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: asked.append("opened"))
    own_window._on_open()
    assert asked == []
    own_window.project_dirty = False


def test_saving_as_adds_the_suffix_when_it_is_missing(own_window, monkeypatch, tmp_path) -> None:
    own_window.view.set_notes([(64, 0.0, 1.0)])
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "mysong"), ""))
    assert own_window._on_save() is True
    assert own_window.project_path == tmp_path / "mysong.nto"
    assert (tmp_path / "mysong.nto").exists()


def test_saving_as_suggests_the_name_of_the_audio_file(own_window, monkeypatch, tmp_path) -> None:
    own_window.audio_path = str(tmp_path / "vocal.wav")
    store.set_value(own_window.settings, "paths", "last_audio_dir", str(tmp_path))
    asked: list[str] = []

    def choose(_parent, _caption, suggested, *_filters):
        asked.append(suggested)
        return (str(tmp_path / "vocal.nto"), "")

    monkeypatch.setattr(QFileDialog, "getSaveFileName", choose)
    assert own_window._on_save_as() is True
    assert Path(asked[0]) == tmp_path / "vocal.nto"


def test_a_project_beside_the_audio_is_opened_instead(own_window, monkeypatch, tmp_path) -> None:
    audio = tmp_path / "song.wav"
    audio.write_bytes(b"")
    project.save(
        project.Project(
            values=store.project_values(store.Settings()),
            audio="song.wav",
            notes=(project.Note(1.0, 0.5, 62),),
        ),
        tmp_path / "song.nto",
    )
    fake_loaders(monkeypatch)  # the project's own audio is analysed, not the file handed in

    own_window.load_audio(str(audio))

    assert own_window.project_path == tmp_path / "song.nto"
    assert [note.pitch for note in own_window.view.notes()] == [62]
    assert own_window.audio_path == str(audio)


def test_a_broken_project_beside_the_audio_does_not_hide_it(own_window, monkeypatch, tmp_path) -> None:
    audio = tmp_path / "song.wav"
    audio.write_bytes(b"")
    (tmp_path / "song.nto").write_text("{}")
    captured = fake_loaders(monkeypatch)

    own_window.load_audio(str(audio))

    assert own_window.project_path is None
    assert own_window.audio_path == str(audio)
    assert captured  # the audio is loaded all the same


def test_a_project_is_picked_up_from_the_command_line(qt_app, tmp_path) -> None:
    path = tmp_path / "song.nto"
    project.save(
        project.Project(values=store.project_values(store.Settings()), notes=(project.Note(1.0, 0.5, 62),)),
        path,
    )
    window = MainWindow(audio=str(path))
    try:
        assert window.project_path == path
        assert [note.pitch for note in window.view.notes()] == [62]
    finally:
        window.project_dirty = False
        window.close()


def reset_channels(window) -> None:
    window.view.clear_notes()
    window.view.set_channels((Channel(channel=0),))


def test_notes_land_on_the_active_channel_and_wear_its_colour(window) -> None:
    window.edit.pen.click()
    window.view.clear_notes()
    window.view.add_channel(program=4)
    window.view.set_active_channel(1)
    draw_note(window, QPointF(2.0, 40.0), QPointF(3.0, 40.0))
    drawn = window.view.notes()[0]
    assert drawn.channel == 1
    on_first = window.view.add_note(60, 0.0, 1.0, 0)
    assert on_first.fill != drawn.fill  # each channel paints its own colour
    reset_channels(window)


def test_a_locked_channel_cannot_be_edited(window) -> None:
    window.edit.pen.click()
    window.view.clear_notes()
    note = window.view.add_note(60, 2.0, 2.0)
    scene_pos = QPointF(3.0, PITCH_MAX - 60 + 0.5)
    window.view.set_channel_field(0, lock=True)

    roll_mouse(window, QEvent.Type.MouseButtonPress, scene_pos, button=Qt.MouseButton.RightButton)
    roll_mouse(window, QEvent.Type.MouseButtonRelease, scene_pos, button=Qt.MouseButton.RightButton)
    assert window.view.notes() == [note]  # a right click does not delete on a locked channel

    draw_note(window, QPointF(6.0, 30.0))  # and the pen stays silent on it too
    assert window.view.notes() == [note]
    window.view.set_channel_field(0, lock=False)
    window.view.clear_notes()


def test_an_invisible_channel_hides_its_notes(window) -> None:
    window.view.clear_notes()
    note = window.view.add_note(60, 2.0, 2.0)
    assert note.isVisible()
    window.view.set_channel_field(0, visible=False, lock=True)  # hiding also locks, noteDigger style
    assert not note.isVisible()
    window.view.set_channel_field(0, visible=True, lock=False)
    assert note.isVisible()
    window.view.clear_notes()


def test_removing_a_channel_takes_its_notes_and_leaves_the_numbers_alone(window) -> None:
    window.view.clear_notes()
    window.view.add_channel()
    window.view.add_note(60, 0.0, 1.0, 0)
    survivor = window.view.add_note(64, 1.0, 1.0, 1)
    assert window.view.remove_channel(0)
    assert [note.pitch for note in window.view.notes()] == [64]
    assert survivor.channel == 1
    reset_channels(window)


def test_a_muted_channel_is_left_out_of_the_program(window) -> None:
    window.view.clear_notes()
    window.view.add_channel(program=4)
    window.view.add_note(60, 0.0, 1.0, 0)
    window.view.add_note(64, 0.0, 1.0, 1)
    notes, channels = window._program()
    assert len(notes) == 2 and len(channels) == 2

    window.view.set_channel_field(1, mute=True)
    notes, channels = window._program()
    assert [note[0] for note in notes] == [60]
    assert [channel[0] for channel in channels] == [0]
    reset_channels(window)


def test_the_channels_button_toggles_the_sidebar(window) -> None:
    window.channel_panel.setVisible(False)
    window.edit.channels.click()
    assert window.channel_panel.isVisible()
    window.edit.channels.click()
    assert not window.channel_panel.isVisible()


def test_a_channel_switch_marks_the_exceptional_state(own_window) -> None:
    own_window.view.set_channels(
        (
            Channel(channel=0, visible=True),
            Channel(channel=1, lock=True, mute=True),
            Channel(channel=2, visible=False, lock=True),
        )
    )
    cards = own_window.channel_panel._cards
    assert cards[0].lock_button.isChecked() is False
    assert cards[0].eye_button.isChecked() is False  # visible is the normal state
    assert cards[1].lock_button.isChecked() is True
    assert cards[1].mute_button.isChecked() is True
    assert cards[1].eye_button.isChecked() is False
    assert cards[2].lock_button.isChecked() is True
    assert cards[2].eye_button.isChecked() is True  # hidden


def test_a_channel_change_refreshes_its_card_in_place(own_window) -> None:
    view = own_window.view
    view.set_channels((Channel(channel=0), Channel(channel=1)))
    panel = own_window.channel_panel
    card = panel._cards[0]

    view.set_channel_field(0, lock=True)
    assert panel._cards[0] is card  # a field edit refreshes the card instead of rebuilding it
    assert card.lock_button.isChecked() is True

    view.set_active_channel(1)
    assert panel._cards[0] is card  # and so does making another channel active
    assert card.property("active") is False

    view.add_channel()
    assert panel._cards[0] is not card  # the set of channels changed, so the cards are rebuilt


def test_the_auto_page_and_overtone_toggles_start_off_and_reach_the_settings(own_window) -> None:
    assert own_window.transport.auto_page.isChecked() is False
    assert own_window.transport.overtone.isChecked() is False
    assert own_window.view.overtone_highlight is False

    own_window.transport.overtone.click()
    assert own_window.view.overtone_highlight is True
    own_window.transport.auto_page.click()
    assert store.get_value(own_window.settings, "editor", "overtone_highlight") is True
    assert store.get_value(own_window.settings, "editor", "auto_page") is True


def test_the_auto_page_turn_follows_the_playhead(own_window, monkeypatch) -> None:
    own_window.show()
    QApplication.processEvents()
    view = own_window.view
    view.set_zoom(48.0, 16.0)
    view.clear_notes()
    view.add_note(60, 0.0, 900.0)  # the page can only turn as far as the sound goes
    monkeypatch.setattr(own_window, "_is_playing", lambda: True)
    own_window.transport.auto_page.setChecked(True)

    def page() -> tuple[float, float]:
        rect = view.mapToScene(view.viewport().rect()).boundingRect()
        return rect.left(), rect.right()

    monkeypatch.setattr(own_window, "_position", lambda: 1.0)
    own_window._show_position()
    left, right = page()
    assert left <= 1.0 * view.bpm / 60.0 <= right

    monkeypatch.setattr(own_window, "_position", lambda: 300.0)
    own_window._show_position()
    left, right = page()
    playhead = 300.0 * view.bpm / 60.0
    assert left <= playhead <= right
    assert playhead - left < (right - left) * 0.5  # the playhead lands near the left of the fresh page

    own_window.transport.auto_page.setChecked(False)
    monkeypatch.setattr(own_window, "_position", lambda: 500.0)
    own_window._show_position()
    assert page() == pytest.approx((left, right))  # with the toggle off nobody turns the page


def accept_import(monkeypatch, mode: str, mapping=()) -> None:
    """Answer the import dialog without one: the flow under test is the import, not the widget."""
    monkeypatch.setattr(MidiImportDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(MidiImportDialog, "mode", lambda self: mode)
    monkeypatch.setattr(MidiImportDialog, "mapping", lambda self: list(mapping))


def test_importing_a_midi_brings_in_its_notes_and_channels(own_window, tmp_path) -> None:
    path = tmp_path / "song.mid"
    midi.write(
        path,
        (Channel(channel=0, program=52), Channel(channel=1)),
        (project.Note(1.0, 0.5, 60, 0), project.Note(1.0, 0.5, 48, 1)),
        120.0,
    )
    assert own_window.import_midi(path) is True

    assert [channel.channel for channel in own_window.view.channels] == [0, 1]
    assert [channel.program for channel in own_window.view.channels] == [52, 0]
    assert sorted((note.pitch, note.channel) for note in own_window.view.notes()) == [(48, 1), (60, 0)]
    assert own_window.transport.bpm.value() == 120.0
    assert own_window.project_dirty is True
    assert own_window.project_path is None  # an import is a sketch until it is saved
    assert "Imported 2 notes" in own_window.statusBar().currentMessage()


def test_importing_over_notes_asks_before_replacing(own_window, monkeypatch, tmp_path) -> None:
    own_window.view.set_notes([(60, 0.0, 1.0)])
    path = tmp_path / "song.mid"
    midi.write(path, (Channel(channel=0),), (project.Note(1.0, 0.5, 62, 0),), 120.0)
    monkeypatch.setattr(MidiImportDialog, "exec", lambda self: QDialog.DialogCode.Rejected)

    assert own_window.import_midi(path) is False
    assert [note.pitch for note in own_window.view.notes()] == [60]  # nothing was touched


def test_replacing_is_what_the_dialog_can_choose(own_window, monkeypatch, tmp_path) -> None:
    own_window.view.set_channels((Channel(channel=0, name="Old"),))
    own_window.view.set_notes([(60, 0.0, 1.0, 0)])
    path = tmp_path / "song.mid"
    midi.write(path, (Channel(channel=0),), (project.Note(1.0, 0.5, 62, 0),), 120.0)
    accept_import(monkeypatch, "replace")

    assert own_window.import_midi(path) is True
    # a replacement is the file's channels whole: a name the project gave the old one does not stay
    assert [channel.channel for channel in own_window.view.channels] == [0]
    assert [channel.name for channel in own_window.view.channels] == [""]
    assert [note.pitch for note in own_window.view.notes()] == [62]


def test_merging_adds_the_file_channels_to_the_roll(own_window, monkeypatch, tmp_path) -> None:
    own_window.view.set_channels((Channel(channel=0, name="Voice"),))
    own_window.view.set_notes([(60, 0.0, 1.0, 0)])
    path = tmp_path / "song.mid"
    midi.write(
        path,
        (Channel(channel=0), Channel(channel=2, program=33, volume=90)),
        (project.Note(1.0, 0.5, 64, 0), project.Note(1.0, 0.5, 40, 2)),
        140.0,
    )
    accept_import(monkeypatch, "merge", [0, -1])

    assert own_window.import_midi(path) is True
    assert [channel.channel for channel in own_window.view.channels] == [0, 2]
    assert (own_window.view.channels[1].program, own_window.view.channels[1].volume) == (33, 90)
    assert sorted((note.pitch, note.channel) for note in own_window.view.notes()) == [(40, 2), (60, 0), (64, 0)]
    # the file's 140 BPM does not touch the grid: a second stays a second on the audio (120 BPM here)
    assert sorted((note.pitch, round(note.start, 3)) for note in own_window.view.notes()) == [
        (40, 2.0),
        (60, 0.0),
        (64, 2.0),
    ]
    assert own_window.transport.bpm.value() == 120.0  # the grid stays on the audio, not on the file


def test_merging_onto_an_empty_channel_takes_the_file_channel_over(own_window, monkeypatch, tmp_path) -> None:
    own_window.view.set_channels((Channel(channel=5, name="Placeholder"), Channel(channel=0, name="Used")))
    own_window.view.set_notes([(60, 0.0, 1.0, 0)])  # channel 5 carries nothing
    path = tmp_path / "song.mid"
    midi.write(
        path,
        (Channel(channel=2, program=81, volume=90),),
        (project.Note(1.0, 0.5, 62, 2),),
        120.0,
    )
    accept_import(monkeypatch, "merge", [5])

    assert own_window.import_midi(path) is True
    # an empty channel is a free place: the file's channel takes it over, the way a brand new one would
    assert [channel.channel for channel in own_window.view.channels] == [0, 5]
    landed = next(channel for channel in own_window.view.channels if channel.channel == 5)
    assert (landed.program, landed.volume) == (81, 90)
    assert landed.name == "Placeholder"  # the name belongs to the project, and it stays
    assert sorted((note.pitch, note.channel) for note in own_window.view.notes()) == [(60, 0), (62, 5)]


def test_a_new_channel_keeps_the_number_the_file_played_on(own_window, monkeypatch, tmp_path) -> None:
    own_window.view.set_channels((Channel(channel=0, name="Voice"),))
    own_window.view.set_notes([(60, 0.0, 1.0, 0)])
    path = tmp_path / "song.mid"
    midi.write(path, (Channel(channel=6),), (project.Note(1.0, 0.5, 62, 6),), 120.0)
    accept_import(monkeypatch, "merge", [-1])

    assert own_window.import_midi(path) is True
    # nothing else plays on channel 6, so the file's new channel keeps that number
    assert [channel.channel for channel in own_window.view.channels] == [0, 6]
    assert own_window.view.channels[1].name == ""  # a MIDI channel carries no name
    assert sorted((note.pitch, note.channel) for note in own_window.view.notes()) == [(60, 0), (62, 6)]


def test_a_new_channel_takes_a_free_number_when_the_files_own_is_taken(own_window, monkeypatch, tmp_path) -> None:
    own_window.view.set_channels((Channel(channel=0, name="Voice"),))
    own_window.view.set_notes([(60, 0.0, 1.0, 0)])
    path = tmp_path / "song.mid"
    midi.write(path, (Channel(channel=0),), (project.Note(1.0, 0.5, 62, 0),), 120.0)
    accept_import(monkeypatch, "merge", [-1])

    assert own_window.import_midi(path) is True
    # channel 0 already carries the roll's notes, so the file's new channel takes the lowest free one
    assert [channel.channel for channel in own_window.view.channels] == [0, 1]
    assert sorted((note.pitch, note.channel) for note in own_window.view.notes()) == [(60, 0), (62, 1)]


def test_the_import_dialog_prefills_the_mapping_by_position() -> None:
    imported = midi.Imported(channels=(Channel(channel=2), Channel(channel=7)), notes=(), bpm=120.0)
    dialog = MidiImportDialog(imported, (Channel(channel=0), Channel(channel=2)), "song.mid")
    assert dialog.mapping() == [0, 2]  # both roll channels carry no notes, so the file lands on them in order
    assert dialog.mode() == "merge"  # the additive choice is what a bare accept takes
    dialog.close()


def test_a_file_channel_moves_past_a_roll_channel_that_already_has_notes() -> None:
    imported = midi.Imported(channels=(Channel(channel=0), Channel(channel=1), Channel(channel=2)), notes=(), bpm=120.0)
    channels = tuple(Channel(name=name, channel=index) for index, name in enumerate("ABCD"))
    # A and C carry notes, so the file's first channel takes the empty place after the second's own
    dialog = MidiImportDialog(imported, channels, "song.mid", occupied={0, 2})
    assert dialog.mapping() == [3, 1, -1]
    dialog.close()


def test_the_import_dialog_keeps_the_roll_within_sixteen_channels() -> None:
    imported = midi.Imported(channels=(Channel(channel=15),), notes=(), bpm=120.0)
    # every channel carries notes, so nothing is free to land on and the mapping reaches for a new one
    channels = tuple(Channel(name=f"T{index}", channel=channel) for index, channel in enumerate([0, *range(15)]))
    dialog = MidiImportDialog(imported, channels, "song.mid", occupied=range(16))
    assert dialog.mapping() == [-1]
    assert dialog.merge_button.isEnabled() is False  # a new channel would be the seventeenth
    dialog._targets[0].setCurrentIndex(0)  # pointed at an existing channel instead
    assert dialog.merge_button.isEnabled() is True
    dialog.close()


def test_a_wavetone_file_loses_its_lead_in_only_when_the_setting_says_so(own_window, monkeypatch, tmp_path) -> None:
    path = tmp_path / "wavetone.mid"
    midi.write(path, (Channel(channel=0),), (project.Note(1.0, 0.5, 60, 0),), 120.0, wavetone=True)

    own_window.import_midi(path)
    assert [round(note.start, 3) for note in own_window.view.notes()] == [2.0]

    store.set_value(own_window.settings, "midi", "wavetone", False)
    accept_import(monkeypatch, "replace")  # the roll holds a note now, so the dialog would stand in the way
    own_window.import_midi(path)
    assert [round(note.start, 3) for note in own_window.view.notes()] == [6.0]


def test_a_midi_that_cannot_be_read_says_so(own_window, tmp_path) -> None:
    broken = tmp_path / "broken.mid"
    broken.write_bytes(b"not a MIDI file at all")
    assert own_window.import_midi(broken) is False
    assert "could not be read" in own_window.statusBar().currentMessage()


def test_exporting_writes_the_roll_out_as_midi(own_window, tmp_path) -> None:
    own_window.view.set_channels((Channel(channel=2, program=81),))
    own_window.view.set_notes(((60, 0.0, 1.0, 2), (64, 1.0, 0.5, 2)))
    path = tmp_path / "out.mid"
    assert own_window.export_midi(path) is True

    imported = midi.read(path, wavetone=True)  # the WaveTone compatibility the settings start with
    assert [(note.pitch, round(note.start, 3)) for note in imported.notes] == [(60, 0.0), (64, 0.5)]
    assert [(channel.channel, channel.program) for channel in imported.channels] == [(2, 81)]
    assert [round(note.start, 3) for note in midi.read(path).notes] == [2.0, 2.5]  # the same, a bar late
    assert own_window.project_path is None  # exporting is not saving
    assert "Exported out.mid" in own_window.statusBar().currentMessage()


def test_a_hidden_channel_is_exported_like_any_other(own_window, tmp_path) -> None:
    own_window.view.set_channels((Channel(channel=0), Channel(channel=1, visible=False)))
    own_window.view.set_notes(((60, 0.0, 1.0, 0), (62, 0.0, 1.0, 1)))
    path = tmp_path / "hidden.mid"
    own_window.export_midi(path)

    assert [note.pitch for note in midi.read(path).notes] == [60, 62]


def test_opening_a_midi_file_imports_it(own_window, monkeypatch, tmp_path) -> None:
    path = tmp_path / "song.mid"
    midi.write(path, (Channel(channel=0),), (project.Note(0.5, 0.5, 60, 0),), 120.0)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(path), ""))

    own_window._on_open()
    assert [note.pitch for note in own_window.view.notes()] == [60]


def test_the_export_button_writes_a_midi_file(own_window, monkeypatch, tmp_path) -> None:
    own_window.view.set_channels((Channel(channel=0),))
    own_window.view.set_notes(((60, 0.0, 1.0, 0),))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "exported"), MIDI_FILTER))

    assert own_window._on_export_midi() is True
    target = tmp_path / "exported.mid"  # the export adds its own suffix
    assert [note.pitch for note in midi.read(target, wavetone=True).notes] == [60]
    assert own_window.project_path is None  # an export leaves the document where it was
