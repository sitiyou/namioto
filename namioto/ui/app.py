# SPDX-License-Identifier: AGPL-3.0-only
"""Namioto piano roll. Run with `uv run namioto`."""

from __future__ import annotations

import argparse
import sys

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import (
    QApplication,
    QGridLayout,
    QMainWindow,
    QWidget,
)

from namioto.spectrum import CHANNEL_MODES, NoteSpectrum
from namioto.ui.controls import EditBar, MixBar, TransportBar
from namioto.ui.roll import SNAP_CHOICES, PianoKeyboard, PianoRollView, TimelineRuler
from namioto.ui.spectrogram import SpectrumLoader

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
QScrollBar:horizontal, QScrollBar:vertical { background: #191c23; border: 0; }
QScrollBar:horizontal { height: 11px; }
QScrollBar:vertical { width: 11px; }
QScrollBar::handle:horizontal, QScrollBar::handle:vertical { background: #3a4152; border-radius: 5px; }
QScrollBar::handle:horizontal { min-width: 24px; }
QScrollBar::handle:vertical { min-height: 24px; }
QScrollBar::handle:hover { background: #4a5468; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
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
    def __init__(self, audio: str | None = None, channels: str = "mono", t_num: float = 20.0):
        super().__init__()
        self.setWindowTitle("Namioto")
        self.resize(1200, 720)
        self.loader: SpectrumLoader | None = None

        self.view = PianoRollView()
        self.view.snap = SNAP_CHOICES[4][1]
        self.ruler = TimelineRuler(self.view)
        self.keyboard = PianoKeyboard(self.view)

        corner = QWidget()
        corner.setFixedSize(self.keyboard.width(), self.ruler.height())
        corner.setStyleSheet("background: #20242c;")

        layout = QGridLayout()
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(corner, 0, 0)
        layout.addWidget(self.ruler, 0, 1)
        layout.addWidget(self.keyboard, 1, 0)
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
        self.transport.bpm.valueChanged.connect(self._on_bpm_changed)
        self.mix.gain.value_changed.connect(self._on_spectrum_parameters)
        self.mix.contrast.value_changed.connect(self._on_spectrum_parameters)
        self.view.gain = self.mix.gain.value()
        self.view.contrast = self.mix.contrast.value()
        self.view.bpm = self.transport.bpm.value()

        self.view.notes_changed.connect(self._update_status)
        if audio is None:
            self._show_hint()
        else:
            self.load_audio(audio, channels=channels, t_num=t_num)
        self._update_status()

    def load_audio(self, path: str, channels: str = "mono", t_num: float = 20.0) -> None:
        self.loader = SpectrumLoader(path, channels=channels, t_num=t_num, parent=self)
        self.loader.progress.connect(self._on_analysis_progress)
        self.loader.loaded.connect(self._on_spectrum_loaded)
        self.loader.failed.connect(lambda message: self.statusBar().showMessage(f"Spectrum failed: {message}"))
        self.statusBar().showMessage(f"Analysing {path} …")
        self.loader.start()

    def _show_hint(self) -> None:
        self.statusBar().showMessage(
            "pen: drag an empty row to draw  |  select: drag a box, ctrl-click to add  |  "
            "right click: delete  |  middle drag: pan  |  ctrl wheel: zoom x, ctrl shift wheel: zoom y"
        )

    def _on_tool_changed(self, tool: str) -> None:
        self.view.tool = tool

    def _on_bpm_changed(self, value: float) -> None:
        self.view.bpm = value

    def _on_spectrum_parameters(self, _value: float = 0.0) -> None:
        self.view.gain = self.mix.gain.value()
        self.view.contrast = self.mix.contrast.value()
        self.view.refresh()

    def _on_analysis_progress(self, done: int, total: int) -> None:
        self.statusBar().showMessage(f"Analysing … {done * 100 // max(1, total)}%")

    def _on_spectrum_loaded(self, spectrum: NoteSpectrum) -> None:
        self.view.set_spectrum(spectrum)
        self.statusBar().showMessage(
            f"{spectrum.frames} frames x {spectrum.table.shape[1]} bands, "
            f"{spectrum.frame_ms:g} ms/frame, {spectrum.duration:.1f} s"
        )

    def _update_status(self) -> None:
        self.setWindowTitle(f"Namioto — {len(self.view.notes())} notes")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="namioto", description="Namioto piano-roll MIDI editor")
    parser.add_argument("audio", nargs="?", help="audio file to analyse and draw as a spectrum")
    parser.add_argument("--channels", choices=CHANNEL_MODES, default="mono", help="which channels to analyse")
    parser.add_argument("--t-num", type=float, default=20.0, help="spectrum frames per second")
    parser.add_argument("--gain", type=float, help="initial spectrum gain")
    parser.add_argument("--contrast", type=float, help="initial spectrum contrast")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setStyleSheet(STYLE_SHEET)
    window = MainWindow(audio=args.audio, channels=args.channels, t_num=args.t_num)
    if args.gain is not None:
        window.mix.gain.set_value(args.gain)
    if args.contrast is not None:
        window.mix.contrast.set_value(args.contrast)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
