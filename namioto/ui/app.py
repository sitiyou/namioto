# SPDX-License-Identifier: AGPL-3.0-only
"""Namioto piano roll. Run with `uv run namioto`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PyQt6.QtCore import QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QPalette, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QGridLayout,
    QLabel,
    QMainWindow,
    QWidget,
)

from namioto.beats import BeatTempo, estimate
from namioto.playback import note_frequency
from namioto.spectrum import CHANNEL_MODES, NoteSpectrum
from namioto.ui.audio import open_player
from namioto.ui.controls import EditBar, MixBar, TransportBar
from namioto.ui.roll import SNAP_CHOICES, PianoKeyboard, PianoRollView, TimelineRuler, note_name
from namioto.ui.song import SongPlayer, load_song
from namioto.ui.spectrogram import SpectrumLoader

POSITION_INTERVAL_MS = 40

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
QLabel#cursorNote { color: #cfd6e4; }
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


class TempoLoader(QThread):
    """Estimates the tempo of a file off the GUI thread."""

    loaded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, path: str | Path, parent=None):
        super().__init__(parent)
        self.path = Path(path)

    def run(self) -> None:
        try:
            result = estimate(self.path)
        except Exception as error:  # a broken file must not take the editor down
            self.failed.emit(f"{type(error).__name__}: {error}")
            return
        self.loaded.emit(result)


class SongLoader(QThread):
    """Decodes the file for playback off the GUI thread."""

    loaded = pyqtSignal(object, int)
    failed = pyqtSignal(str)

    def __init__(self, path: str | Path, parent=None):
        super().__init__(parent)
        self.path = Path(path)

    def run(self) -> None:
        try:
            samples, sample_rate = load_song(self.path)
        except Exception as error:  # a broken file must not take the editor down
            self.failed.emit(f"{type(error).__name__}: {error}")
            return
        self.loaded.emit(samples, sample_rate)


class MainWindow(QMainWindow):
    def __init__(self, audio: str | None = None, channels: str = "mono", t_num: float = 40.0):
        super().__init__()
        self.setWindowTitle("Namioto")
        self.resize(1200, 720)
        self.audio_path: str | None = None
        self.loader: SpectrumLoader | None = None
        self.tempo_loader: TempoLoader | None = None
        self.song_loader: SongLoader | None = None

        self.view = PianoRollView()
        self.ruler = TimelineRuler(self.view)
        self.keyboard = PianoKeyboard(self.view)
        self.player, self.player_name = open_player(self)
        self.player.gain = 0.8
        self.song = SongPlayer(self)
        self.position_timer = QTimer(self)
        self.position_timer.setInterval(POSITION_INTERVAL_MS)
        self.position_timer.timeout.connect(self._show_position)

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
        self.view.edit_mode = self.edit.mode.isChecked()
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
        self.view.snap = self.edit.snap.currentData()
        self.edit.clear_requested.connect(self.view.clear_notes)
        self.edit.tool_changed.connect(self._on_tool_changed)
        self.edit.mode_changed.connect(self._on_mode_changed)
        self.edit.division_changed.connect(self._on_division_changed)
        self.transport.bpm.valueChanged.connect(self._on_bpm_changed)
        self.transport.detect.clicked.connect(self._start_tempo)
        self.transport.tempo.applied.connect(self._apply_tempo)
        self.transport.tempo.dismissed.connect(self.transport.tempo.hide)
        self.transport.rewind_requested.connect(lambda: self._seek(0.0))
        self.transport.forward_requested.connect(lambda: self._seek(self._duration()))
        self.transport.play_pause_requested.connect(self._toggle_play)
        self.transport.play_from_start_requested.connect(self._play_from_start)
        self.transport.stop_requested.connect(self._stop)
        self.player.finished.connect(self._on_playback_finished)
        self.song.finished.connect(self._on_playback_finished)
        self.view.seek_requested.connect(self._seek)
        self.view.hover_changed.connect(self._on_hover_changed)
        self.view.note_preview.connect(self._on_note_preview)
        self.keyboard.key_preview.connect(self._on_note_preview)
        self.mix.midi_volume.value_changed.connect(self._on_midi_volume)
        self.mix.midi_volume.slider.setToolTip(f"Volume of the note playback through {self.player_name}")
        self.mix.gain.value_changed.connect(self._on_spectrum_parameters)
        self.mix.contrast.value_changed.connect(self._on_spectrum_parameters)
        self.view.gain = self.mix.gain.value()
        self.view.contrast = self.mix.contrast.value()
        self.view.bpm = self.transport.bpm.value()

        self.view.notes_changed.connect(self._update_status)
        self.play_shortcut = QShortcut(QKeySequence("Space"), self)
        self.play_shortcut.activated.connect(self._toggle_play)
        self.cursor_note = QLabel()
        self.cursor_note.setObjectName("cursorNote")
        self.statusBar().addPermanentWidget(self.cursor_note)
        if audio is None:
            self._show_hint()
        else:
            self.load_audio(audio, channels=channels, t_num=t_num)
        self._update_status()

    def load_audio(self, path: str, channels: str = "mono", t_num: float = 40.0) -> None:
        self.audio_path = path
        self.loader = SpectrumLoader(path, channels=channels, t_num=t_num, parent=self)
        self.loader.progress.connect(self._on_analysis_progress)
        self.loader.loaded.connect(self._on_spectrum_loaded)
        self.loader.failed.connect(lambda message: self.statusBar().showMessage(f"Spectrum failed: {message}"))
        self.statusBar().showMessage(f"Analysing {path} …")
        self.loader.start()
        self.song_loader = SongLoader(path, parent=self)
        self.song_loader.loaded.connect(self._on_song_loaded)
        self.song_loader.failed.connect(lambda message: self.statusBar().showMessage(f"Playback failed: {message}"))
        self.song_loader.start()
        self._start_tempo()

    def _on_song_loaded(self, samples, sample_rate: int) -> None:
        self.song.load(samples, sample_rate)
        self.song.set_speed(self.transport.speed.value())

    def _start_tempo(self) -> None:
        """Estimate the tempo of the loaded audio in the background, as a suggestion only."""
        if self.audio_path is None:
            return
        self.transport.tempo.hide()
        self.transport.detect.setEnabled(False)
        self.tempo_loader = TempoLoader(self.audio_path, parent=self)
        self.tempo_loader.loaded.connect(self._on_tempo_loaded)
        self.tempo_loader.failed.connect(self._on_tempo_failed)
        self.tempo_loader.start()

    def _on_tempo_loaded(self, result: BeatTempo) -> None:
        self.transport.detect.setEnabled(True)
        if not result.local:
            return
        self.transport.tempo.estimate(result.bpm, result.agreement, len(result.local), result.residual)

    def _on_tempo_failed(self, message: str) -> None:
        self.transport.detect.setEnabled(self.audio_path is not None)
        self.statusBar().showMessage(f"Tempo estimation failed: {message}")

    def _apply_tempo(self, bpm: float) -> None:
        self.transport.tempo.hide()
        self.transport.bpm.setValue(bpm)
        self.statusBar().showMessage(f"Tempo set to {bpm:.0f} BPM from the audio")

    def _play(self) -> None:
        """Send the notes to the synth and start the audio file, both from where the cursor sits."""
        beats = 60.0 / self.view.bpm  # scene units are beats, the players work in seconds
        notes = tuple((note.pitch, note.start * beats, note.duration * beats) for note in self.view.notes())
        if not notes and not self.song.is_loaded:
            self.statusBar().showMessage("Nothing to play: load a file or draw some notes")
            return
        seconds = self._position()
        if seconds >= self._duration() - 1e-3:
            seconds = 0.0
        speed = self.transport.speed.value()
        if self.song.is_loaded:
            self.song.set_speed(speed)
            self.song.play(seconds)
        self.player.set_program(notes, speed)
        self.player.play(seconds)
        self._show_position()
        if self._is_playing():
            self.position_timer.start()
        else:
            self.statusBar().showMessage(f"{self.player_name} did not accept the notes")

    def _toggle_play(self) -> None:
        """One button for both, so it asks the players what they are doing right now."""
        if self._is_playing():
            self._pause()
        else:
            self._play()

    def _play_from_start(self) -> None:
        self._seek(0.0)
        self._play()

    def _pause(self) -> None:
        self.position_timer.stop()
        self.song.pause()
        self.player.pause()
        self._show_position()

    def _stop(self) -> None:
        self.position_timer.stop()
        self.song.stop()
        self.player.stop()
        self._show_position()

    def _seek(self, seconds: float) -> None:
        self.song.seek(seconds)
        self.player.seek(seconds)
        self._show_position()

    def _position(self) -> float:
        """Where the transport sits: the audio file leads when one is loaded, else the notes."""
        return self.song.position if self.song.is_loaded else self.player.position

    def _duration(self) -> float:
        return self.song.duration if self.song.is_loaded else self.player.duration

    def _is_playing(self) -> bool:
        return self.song.is_playing or self.player.is_playing

    def _show_position(self) -> None:
        seconds = self._position() + self.transport.latency.value() / 1000.0
        self.transport.set_position(seconds)
        self.transport.set_playing(self._is_playing())
        self.view.set_playhead(seconds)

    def _on_playback_finished(self) -> None:
        if self._is_playing():  # the other layer is still running
            return
        self.position_timer.stop()
        self._show_position()

    def _on_midi_volume(self, value: float) -> None:
        self.player.gain = value / 100.0

    def _on_note_preview(self, pitch: int) -> None:
        """Audition a note the user clicked or drew."""
        self.player.preview(pitch)

    def _on_hover_changed(self, pitch: int | None) -> None:
        if pitch is None:
            self.cursor_note.clear()
            return
        self.cursor_note.setText(f"{note_name(pitch)}   {note_frequency(pitch):.2f} Hz")

    def _show_hint(self) -> None:
        self.statusBar().showMessage(
            "space: play or pause  |  click (outside edit mode): move the playhead  |  "
            "pen: drag an empty row to draw  |  select: drag a box, ctrl-click to add  |  "
            "shift drag a note: trim its start (left half) or end (right half)  |  right click: delete  |  "
            "middle drag: pan  |  ctrl wheel: zoom x, ctrl shift wheel: zoom y"
        )

    def _on_tool_changed(self, tool: str) -> None:
        self.view.tool = tool or None

    def _on_mode_changed(self, editing: bool) -> None:
        self.view.edit_mode = editing
        self.view.refresh()

    def _on_division_changed(self, division: str) -> None:
        self.view.division = division
        self.view.refresh()

    def _on_bpm_changed(self, value: float) -> None:
        self.transport.tempo.hide()  # a tempo the user typed wins over the suggestion
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
    parser.add_argument("--t-num", type=float, default=40.0, help="spectrum frames per second")
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
