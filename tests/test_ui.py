# SPDX-License-Identifier: AGPL-3.0-only
"""Layout checks for the control bars and the main window."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from namioto.ui.app import STYLE_SHEET, MainWindow, dark_palette  # noqa: E402
from namioto.ui.controls import Cluster, ValueSlider  # noqa: E402


@pytest.fixture(scope="module")
def window():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setStyleSheet(STYLE_SHEET)
    window = MainWindow()
    window.resize(1200, 720)
    window.show()
    app.processEvents()
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
    assert window.mix.brightness.value() == 50.0
    assert window.mix.audio_volume.value() == 80.0
    assert window.edit.snap.currentData() == 0.25
    assert window.edit.division_beats.isChecked()
