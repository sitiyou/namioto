# SPDX-License-Identifier: AGPL-3.0-only
"""Layout checks for the control bars and the main window."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PyQt6.QtCore import QPoint, QPointF  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from namioto.spectrum import MIDI_OFFSET, NOTE_COUNT, NoteSpectrum  # noqa: E402
from namioto.ui.app import STYLE_SHEET, MainWindow, dark_palette  # noqa: E402
from namioto.ui.controls import Cluster, ValueSlider  # noqa: E402
from namioto.ui.roll import (  # noqa: E402
    CONTENT_MARGIN,
    GRID_BAR,
    LENGTH_BEATS,
    NOTE_EDGE_DARK,
    NOTE_EDGE_LIGHT,
    NOTE_FILL,
    NOTE_INSET,
    NOTE_SELECTED,
    NOTE_SELECTED_EDGE,
    PITCH_MAX,
    SPECTRUM_OCTAVE,
    SPECTRUM_TOP,
    PianoRollView,
)
from namioto.ui.spectrogram import SpectrumImage, SpectrumLoader  # noqa: E402

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
        window.edit.pen,
        window.edit.select,
        window.edit.division_beats,
        window.edit.division_seconds,
        window.transport.play,
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
    ruler_marks = [x for x in range(ruler_image.width()) if ruler_image.pixelColor(x, 2) == GRID_BAR]
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


def test_defaults_of_the_control_bars(window) -> None:
    assert window.transport.bpm.value() == 120.0
    assert window.transport.latency.value() == 0
    assert window.transport.position.text() == "00:00.000"
    assert window.transport.speed.value() == 1.0
    assert window.mix.gain.value() == 240.0
    assert window.mix.contrast.value() == 1.0
    assert window.mix.audio_volume.value() == 80.0
    assert window.edit.snap.currentData() == 0.25
    assert window.edit.division_beats.isChecked()


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
    window.view.set_spectrum(make_spectrum(frames=200, value=9.0))
    image = window.view.grab().toImage()
    assert not image.isNull()
    assert any(
        image.pixelColor(x, y).red() > 200 and image.pixelColor(x, y).green() < 40
        for x in range(200, image.width(), 40)
        for y in range(20, image.height(), 20)
    )
    window.view.set_spectrum(None)


def test_notes_are_drawn_over_the_spectrum(window) -> None:
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
