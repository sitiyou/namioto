# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest

from namioto.analysis.bpm import BpmEstimate
from namioto.ui.bpm_dialog import BpmAnalysisDialog, BpmAnalysisLoader, BpmDistribution


def test_dialog_lists_backend_results_and_selects_a_wavetone_alternative(window, monkeypatch) -> None:
    started = []
    monkeypatch.setattr(BpmAnalysisLoader, "start", lambda self: started.append(self.algorithm))
    dialog = BpmAnalysisDialog("song.wav", window)
    assert started == ["wavetone", "librosa", "tempocnn"]
    assert dialog.pending == {"wavetone", "librosa", "tempocnn"}

    dialog._backend_loaded("librosa", BpmEstimate(126.2, "librosa", windows=4, agreement=0.75), "")
    assert dialog.distribution.estimates["librosa"].bpm == 126.2

    dialog._backend_loaded("tempocnn", None, "RuntimeError: model missing")
    dialog._backend_loaded("wavetone", BpmEstimate(126, "wavetone", candidates=((126, 1.0), (189, 0.82))), "")

    assert dialog.distribution.estimates["wavetone"].candidates[1][0] == 189
    assert "tempocnn" in dialog.errors
    assert dialog.distribution.selected_value == ("wavetone", 126.0)
    assert dialog.selected_bpm == 126.0
    assert dialog.selected_label.text().endswith("126.00")
    dialog._select_bpm(189.0)
    assert dialog.selected_bpm == 189.0
    assert dialog.selected_label.text().endswith("189.00")
    assert dialog.user_selected
    assert dialog.apply_button.isEnabled()
    dialog.close()


def test_estimate_line_is_clickable_away_from_its_marker(window, monkeypatch) -> None:
    monkeypatch.setattr(BpmAnalysisLoader, "start", lambda self: None)
    dialog = BpmAnalysisDialog("song.wav", window)
    dialog._backend_loaded("librosa", BpmEstimate(126.2, "librosa", windows=4, agreement=0.75), "")
    dialog.resize(640, 420)
    dialog.show()
    chart = dialog.distribution
    x = round(chart._x(126.2))
    y = 5

    QTest.mouseClick(chart, Qt.MouseButton.LeftButton, pos=QPoint(x, y))

    assert dialog.selected_bpm == 126.2
    assert chart.selected_value == ("librosa", 126.2)
    dialog.close()


def test_horizontal_bpm_distance_switches_nearest_hover_regardless_of_y() -> None:
    chart = BpmDistribution()
    chart.resize(600, 360)
    wave = BpmEstimate(
        126.0,
        "wavetone",
        candidates=((126.0, 1.0),),
        distribution=((60.0, 0.0), (126.0, 1.0), (240.0, 0.0)),
    )
    librosa = BpmEstimate(128.0, "librosa", windows=4, agreement=0.75)
    chart.set_estimates({"wavetone": wave, "librosa": librosa})
    wave_x = chart._x(126.0)
    librosa_x = chart._x(128.0)

    assert chart._closest_marker(QPointF(wave_x, 0))[1:] == ("wavetone", 126.0)
    assert chart._closest_marker(QPointF(librosa_x, chart.height()))[1:] == ("librosa", 128.0)
    for width in (600, 980):
        chart.resize(width, 360)
        cursor_x = chart._x(127.6)
        assert chart._closest_marker(QPointF(cursor_x, 0))[1:] == ("librosa", 128.0)


def test_hover_highlight_is_distinct_from_current_selection(window) -> None:
    chart = BpmDistribution()
    chart.resize(600, 360)
    chart.set_estimates(
        {
            "wavetone": BpmEstimate(126.0, "wavetone", candidates=((126.0, 1.0),)),
            "librosa": BpmEstimate(128.0, "librosa", windows=4, agreement=0.75),
        }
    )
    chart.selected_value = ("wavetone", 126.0)
    chart.show()
    def move_to(bpm: float) -> None:
        event = QMouseEvent(
            QEvent.Type.MouseMove,
            QPointF(chart._x(bpm), 0),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        chart.mouseMoveEvent(event)

    move_to(126.0)
    assert chart.hovered_value == ("wavetone", 126.0)
    move_to(128.0)

    assert chart.hovered_value == ("librosa", 128.0)
    assert chart.selected_value == ("wavetone", 126.0)
