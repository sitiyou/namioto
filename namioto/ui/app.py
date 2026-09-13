# SPDX-License-Identifier: AGPL-3.0-only
"""Namioto piano roll. Run with `uv run namioto`."""

from __future__ import annotations

import sys

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QGridLayout,
    QMainWindow,
    QWidget,
)

from namioto.ui.controls import EditBar, MixBar, TransportBar
from namioto.ui.roll import SNAP_CHOICES, PianoKeyboard, PianoRollView, TimelineRuler

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

STYLE_SHEET = """
QMainWindow, QToolBar, QStatusBar { background: #191c23; }
QToolBar { border: 0; spacing: 6px; padding: 2px 6px; }
QToolBar::separator { background: #333a48; width: 1px; margin: 4px 6px; }
QWidget#cluster { background: #232833; border: 1px solid #2f3644; border-radius: 6px; }
QLabel { color: #94a0b5; background: transparent; }
QLabel#clusterCaption { color: #6f7a8c; }
QLabel#fieldLabel { color: #8c97a9; }
QLabel#sliderValue { color: #cfd6e4; }
QLabel#position { color: #e6ecf5; font-size: 13px; }
QToolButton { color: #cfd6e4; background: transparent; border: 1px solid transparent;
              border-radius: 4px; padding: 2px; }
QToolButton:hover { background: #2b3140; }
QToolButton:checked { background: #1f3a5c; border: 1px solid #3b9dff; }
QToolButton#textButton { border: 1px solid #3a4152; padding: 1px 6px; }
QToolButton#textButton:hover { background: #2b3140; }
QToolButton#textButton:checked { background: #1f3a5c; border: 1px solid #3b9dff; }
QToolButton:disabled { color: #5c6474; }
QSlider::groove:horizontal { height: 4px; background: #2f3541; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #3b9dff; border-radius: 2px; }
QSlider::handle:horizontal { width: 9px; height: 14px; margin: -5px 0;
                             background: #cfd6e4; border-radius: 2px; }
QComboBox, QSpinBox, QDoubleSpinBox { background: #1c2129; color: #cfd6e4;
                                      border: 1px solid #3a4152; border-radius: 3px; padding: 1px 4px; }
QComboBox QAbstractItemView { background: #262b34; color: #cfd6e4;
                              selection-background-color: #1f3a5c; }
QStatusBar::item { border: 0; }
"""


def dark_palette() -> QPalette:
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, "#20242c"),
        (QPalette.ColorRole.WindowText, "#cfd6e4"),
        (QPalette.ColorRole.Base, "#191c23"),
        (QPalette.ColorRole.AlternateBase, "#20242c"),
        (QPalette.ColorRole.Text, "#cfd6e4"),
        (QPalette.ColorRole.Button, "#262b34"),
        (QPalette.ColorRole.ButtonText, "#cfd6e4"),
        (QPalette.ColorRole.Highlight, "#3b9dff"),
        (QPalette.ColorRole.HighlightedText, "#101318"),
        (QPalette.ColorRole.ToolTipBase, "#262b34"),
        (QPalette.ColorRole.ToolTipText, "#cfd6e4"),
        (QPalette.ColorRole.PlaceholderText, "#6f7a8c"),
    ):
        palette.setColor(role, QColor(color))
    return palette


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Namioto")
        self.resize(1200, 720)

        self.view = PianoRollView()
        self.view.snap = SNAP_CHOICES[4][1]
        ruler = TimelineRuler(self.view)
        keyboard = PianoKeyboard(self.view)

        corner = QWidget()
        corner.setFixedSize(keyboard.width(), ruler.height())
        corner.setStyleSheet("background: #20242c;")

        layout = QGridLayout()
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(corner, 0, 0)
        layout.addWidget(ruler, 0, 1)
        layout.addWidget(keyboard, 1, 0)
        layout.addWidget(self.view, 1, 1)
        layout.setColumnStretch(1, 1)
        layout.setRowStretch(1, 1)
        panel = QWidget()
        panel.setLayout(layout)
        self.setCentralWidget(panel)

        self.transport = TransportBar(self)
        self.edit = EditBar(SNAP_CHOICES, self)
        self.mix = MixBar(self)
        self.addToolBar(self.transport)
        self.addToolBarBreak()
        self.addToolBar(self.edit)
        self.addToolBarBreak()
        self.addToolBar(self.mix)

        for bar in (self.transport, self.edit, self.mix):
            bar.setMovable(False)
            bar.setFloatable(False)

        self.edit.snap.currentIndexChanged.connect(lambda: setattr(self.view, "snap", self.edit.snap.currentData()))
        self.edit.clear_requested.connect(self.view.clear_notes)
        self.edit.tool_changed.connect(self._on_tool_changed)

        self.view.notes_changed.connect(self._update_status)
        for pitch, start, duration in DEMO_NOTES:
            self.view.add_note(pitch, start, duration)
        self._update_status()

        self.statusBar().showMessage(
            "pen: drag an empty row to draw  |  select: drag a box, ctrl-click to add  |  "
            "right click: delete  |  middle drag: pan  |  ctrl wheel: zoom x, ctrl shift wheel: zoom y"
        )

    def _on_tool_changed(self, tool: str) -> None:
        self.view.tool = tool

    def _update_status(self) -> None:
        self.setWindowTitle(f"Namioto — {len(self.view.notes())} notes")


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setStyleSheet(STYLE_SHEET)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
