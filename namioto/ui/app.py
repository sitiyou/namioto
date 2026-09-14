# SPDX-License-Identifier: AGPL-3.0-only
"""Namioto piano roll. Run with `uv run namioto`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PyQt6.QtCore import QByteArray, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QPalette, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QGridLayout,
    QLabel,
    QMainWindow,
    QWidget,
)

from namioto import settings as store
from namioto.beats import TOLERANCE, estimate
from namioto.playback import note_frequency
from namioto.spectrum import CHANNEL_MODES, NoteSpectrum
from namioto.ui.audio import open_player, port_names
from namioto.ui.controls import EditBar, MixBar, TransportBar
from namioto.ui.roll import SNAP_CHOICES, PianoKeyboard, PianoRollView, TimelineRuler, note_name
from namioto.ui.settings_dialog import SettingsDialog, SettingsStore
from namioto.ui.song import SongPlayer, load_song, stretch_song
from namioto.ui.spectrogram import SpectrumLoader

POSITION_INTERVAL_MS = 40
SPEED_SETTLE_MS = 400
BEAT_SOURCE = "Beat tracking and least-squares fit"
TEMPOCNN_SOURCE = "TempoCNN"

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
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit { background: #1c2129; color: #cfd6e4;
                                      border: 1px solid #3a4152; border-radius: 3px; padding: 1px 4px; }
QComboBox QAbstractItemView { background: #262b34; color: #cfd6e4;
                              selection-background-color: #1f3a5c; }
QStatusBar::item { border: 0; }
QLabel#cursorNote { color: #cfd6e4; }
QDialog { background: #20242c; }
QTabWidget::pane { border: 1px solid #2f3644; }
QTabBar::tab { background: #262b34; color: #94a0b5; padding: 5px 11px; }
QTabBar::tab:selected { background: #1f3a5c; color: #e6ecf5; }
QCheckBox { color: #cfd6e4; spacing: 6px; }
QCheckBox::indicator { width: 13px; height: 13px; border: 1px solid #3a4152;
                       border-radius: 3px; background: #1c2129; }
QCheckBox::indicator:checked { background: #3b9dff; border-color: #3b9dff; }
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
    """Estimates the tempo of a file off the GUI thread, with the estimator the settings picked."""

    loaded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        path: str | Path,
        estimator: str = "beats",
        window_seconds: float = 12.0,
        window_hop_seconds: float = 6.0,
        parent=None,
    ):
        super().__init__(parent)
        self.path = Path(path)
        self.estimator = estimator
        self.window_seconds = window_seconds
        self.window_hop_seconds = window_hop_seconds

    def run(self) -> None:
        try:
            if self.estimator == "tempocnn":
                from namioto.tempo import estimate as estimate_tempocnn  # only the model needs onnxruntime

                result = estimate_tempocnn(self.path)
            else:
                result = estimate(
                    self.path,
                    window_seconds=self.window_seconds,
                    window_hop_seconds=self.window_hop_seconds,
                )
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


class StretchLoader(QThread):
    """Rerenders the song for a playback speed, off the GUI thread: it takes seconds of work."""

    loaded = pyqtSignal(object, float)
    failed = pyqtSignal(str)

    def __init__(self, samples, speed: float, parent=None):
        super().__init__(parent)
        self.samples = samples
        self.speed = speed

    def run(self) -> None:
        try:
            buffer = stretch_song(self.samples, self.speed)
        except Exception as error:  # a broken stretch must not take the editor down
            self.failed.emit(f"{type(error).__name__}: {error}")
            return
        self.loaded.emit(buffer, self.speed)


class MainWindow(QMainWindow):
    def __init__(self, audio: str | None = None, settings=None, overrides: dict | None = None):
        super().__init__()
        self.setWindowTitle("Namioto")
        self.resize(1200, 720)
        self.settings = settings if settings is not None else store.load()
        self.settings_store = SettingsStore(self.settings, parent=self)
        self.settings_store.changed.connect(self._on_settings_changed)
        self.settings_store.failed.connect(lambda message: self.statusBar().showMessage(message))
        self.overrides = dict(overrides or {})  # values this run was asked for, never written back
        self._display_overrides: dict[str, float] = {}
        self._seeding = False
        self.audio_path: str | None = None
        self.loader: SpectrumLoader | None = None
        self.tempo_loader: TempoLoader | None = None
        self.song_loader: SongLoader | None = None
        self.stretch_loader: StretchLoader | None = None
        self.pending_play = False

        editor = self.settings.editor
        self.view = PianoRollView()
        self.view.set_zoom(editor.zoom_x, editor.zoom_y)
        self.view.initial_center = (self.settings.session.center_x, self.settings.session.center_y)
        self.view.overtone_highlight = editor.overtone_highlight
        self.view.division = editor.division
        self.view.snap = editor.snap
        self.ruler = TimelineRuler(self.view)
        self.keyboard = PianoKeyboard(self.view)
        self.player, self.player_name = self._make_player()
        self._current_player_key = self._player_key()
        self.player.gain = self.settings.playback.midi_volume / 100.0
        self.song = SongPlayer(self, buffer_ms=self.settings.playback.buffer_ms)
        self.position_timer = QTimer(self)
        self.position_timer.setInterval(POSITION_INTERVAL_MS)
        self.position_timer.timeout.connect(self._show_position)
        self.speed_timer = QTimer(self)
        self.speed_timer.setSingleShot(True)
        self.speed_timer.setInterval(SPEED_SETTLE_MS)
        self.speed_timer.timeout.connect(self._apply_speed)

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
        self.transport.speed.slider.valueChanged.connect(self._on_speed_changed)
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

        self.edit.snap.setCurrentIndex(max(0, self.edit.snap.findData(editor.snap)))
        self.edit.division_beats.setChecked(editor.division == "beats")
        self.edit.division_seconds.setChecked(editor.division == "seconds")
        self.view.snap = self.edit.snap.currentData()
        self.view.edit_mode = editor.start_in_edit_mode
        self.edit.set_mode(editor.start_in_edit_mode)
        self.transport.bpm.setValue(self.settings.tempo.bpm)
        self.transport.latency.setValue(self.settings.playback.latency_ms)
        self.transport.speed.set_value(self.settings.playback.speed)
        self.mix.gain.set_value(self.settings.spectrum.gain)
        self.mix.contrast.set_value(self.settings.spectrum.contrast)
        self.mix.audio_volume.set_value(self.settings.playback.audio_volume)
        self.mix.midi_volume.set_value(self.settings.playback.midi_volume)

        self.edit.snap.currentIndexChanged.connect(lambda: setattr(self.view, "snap", self.edit.snap.currentData()))
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
        self.mix.settings_button.clicked.connect(self._open_settings)
        self.mix.midi_volume.value_changed.connect(self._on_midi_volume)
        self.mix.midi_volume.slider.setToolTip(f"Volume of the note playback through {self.player_name}")
        self.mix.audio_volume.value_changed.connect(self._on_audio_volume)
        self.mix.gain.value_changed.connect(self._on_spectrum_parameters)
        self.mix.contrast.value_changed.connect(self._on_spectrum_parameters)
        self.view.gain = self.mix.gain.value()
        self.view.contrast = self.mix.contrast.value()
        self.view.bpm = self.transport.bpm.value()

        # the bar and the settings are the same thing: whatever is on screen is what comes back
        for signal in (
            self.mix.gain.value_changed,
            self.mix.contrast.value_changed,
            self.mix.audio_volume.value_changed,
            self.mix.midi_volume.value_changed,
            self.transport.speed.value_changed,
            self.transport.latency.valueChanged,
            self.transport.bpm.valueChanged,
            self.edit.division_changed,
        ):
            signal.connect(self._on_panel_changed)
        self.edit.snap.currentIndexChanged.connect(self._on_panel_changed)

        self.view.notes_changed.connect(self._update_status)
        self.play_shortcut = QShortcut(QKeySequence("Space"), self)
        self.play_shortcut.activated.connect(self._toggle_play)
        self.cursor_note = QLabel()
        self.cursor_note.setObjectName("cursorNote")
        self.statusBar().addPermanentWidget(self.cursor_note)
        self._restore_session()
        if audio is None:
            self._show_hint()
        else:
            self.load_audio(audio)
        self._update_status()

    def _make_player(self):
        playback = self.settings.playback
        return open_player(
            self,
            backend=playback.backend,
            port_name=playback.midi_port,
            buffer_ms=playback.buffer_ms,
            velocity=playback.velocity,
            program=playback.program,
            a4=self.settings.analysis.a4,
        )

    def _player_key(self) -> tuple:
        """What a player is built from: a change to any of it means building a new one."""
        playback = self.settings.playback
        return (
            playback.backend,
            playback.midi_port,
            playback.buffer_ms,
            playback.velocity,
            playback.program,
            self.settings.analysis.a4,
        )

    def apply_overrides(self, gain: float | None = None, contrast: float | None = None) -> None:
        """Values a command line asked for: they shape this run, not what is remembered."""
        if gain is not None:
            self._display_overrides["gain"] = gain
        if contrast is not None:
            self._display_overrides["contrast"] = contrast
        self._seeding = True  # the panel is being filled in, not touched
        try:
            if gain is not None:
                self.mix.gain.set_value(gain)
            if contrast is not None:
                self.mix.contrast.set_value(contrast)
        finally:
            self._seeding = False

    def _open_settings(self) -> None:
        dialog = SettingsDialog(
            self.settings,
            ports=port_names,
            can_reanalyse=self.audio_path is not None,
            parent=self,
        )
        dialog.applied.connect(self.settings_store.apply)
        dialog.reanalyse_requested.connect(self._reanalyse)
        dialog.exec()

    def _reanalyse(self) -> None:
        if self.audio_path is not None:
            self.load_audio(self.audio_path)

    def _on_settings_changed(self, settings) -> None:
        """Take a finished settings window over the running one.

        The panel is filled in with the change kept quiet: every slider moved would otherwise write
        the values of the ones not yet moved back into the settings being applied.
        """
        self.settings = settings
        self._seeding = True
        try:
            self.view.overtone_highlight = settings.editor.overtone_highlight
            self.view.gain = settings.spectrum.gain
            self.view.contrast = settings.spectrum.contrast
            self.view.set_zoom(settings.editor.zoom_x, settings.editor.zoom_y)
            self.edit.snap.setCurrentIndex(max(0, self.edit.snap.findData(settings.editor.snap)))
            self.view.snap = self.edit.snap.currentData()
            self.edit.division_beats.setChecked(settings.editor.division == "beats")
            self.edit.division_seconds.setChecked(settings.editor.division == "seconds")
            self.view.division = settings.editor.division
            self.view.refresh()
            self.mix.gain.set_value(settings.spectrum.gain)
            self.mix.contrast.set_value(settings.spectrum.contrast)
            self.mix.audio_volume.set_value(settings.playback.audio_volume)
            self.mix.midi_volume.set_value(settings.playback.midi_volume)
            self.transport.bpm.setValue(settings.tempo.bpm)
            self.transport.latency.setValue(settings.playback.latency_ms)
            self.transport.speed.set_value(settings.playback.speed)
            self.song.buffer_ms = settings.playback.buffer_ms
            if self._player_key() != self._current_player_key:
                self._rebuild_player()
        finally:
            self._seeding = False
        self._update_status()

    def _rebuild_player(self) -> None:
        """A different backend, port, buffer or tuning is a different player; the notes go over again."""
        playing = self.player.is_playing
        position = self._position()
        self.player.stop()
        self.player, self.player_name = self._make_player()
        self._current_player_key = self._player_key()
        self.player.gain = self.mix.midi_volume.value() / 100.0
        self.player.finished.connect(self._on_playback_finished)
        self.mix.midi_volume.slider.setToolTip(f"Volume of the note playback through {self.player_name}")
        beats = 60.0 / self.view.bpm
        notes = tuple((note.pitch, note.start * beats, note.duration * beats) for note in self.view.notes())
        self.player.set_program(notes, self.transport.speed.value())
        if playing:
            self.player.play(position)

    def _remember_configuration(self) -> None:
        """The bar values are the settings, so what is on screen is what comes back next time."""
        if "gain" not in self._display_overrides:
            store.set_value(self.settings, "spectrum", "gain", self.mix.gain.value())
        if "contrast" not in self._display_overrides:
            store.set_value(self.settings, "spectrum", "contrast", self.mix.contrast.value())
        store.set_value(self.settings, "playback", "audio_volume", self.mix.audio_volume.value())
        store.set_value(self.settings, "playback", "midi_volume", self.mix.midi_volume.value())
        store.set_value(self.settings, "playback", "speed", self.transport.speed.value())
        store.set_value(self.settings, "playback", "latency_ms", self.transport.latency.value())
        store.set_value(self.settings, "tempo", "bpm", self.transport.bpm.value())
        store.set_value(self.settings, "editor", "snap", self.view.snap)
        store.set_value(self.settings, "editor", "division", self.view.division)
        store.set_value(self.settings, "editor", "zoom_x", self.view.zoom[0])
        store.set_value(self.settings, "editor", "zoom_y", self.view.zoom[1])

    def _on_panel_changed(self, *_args) -> None:
        if self._seeding:
            return
        self._display_overrides.clear()  # the panel was touched after all, so it is what is remembered
        self._remember_configuration()
        self.settings_store.touch()

    def _restore_session(self) -> None:
        session = self.settings.session
        if session.geometry:
            self.restoreGeometry(QByteArray.fromBase64(session.geometry.encode()))
        if session.window_state:
            self.restoreState(QByteArray.fromBase64(session.window_state.encode()))

    def _remember_session(self) -> None:
        session = self.settings.session
        session.geometry = bytes(self.saveGeometry().toBase64()).decode()
        session.window_state = bytes(self.saveState().toBase64()).decode()
        centre = self.view.mapToScene(self.view.viewport().rect().center())
        session.center_x = round(centre.x(), 1)
        session.center_y = round(centre.y(), 1)

    def closeEvent(self, event) -> None:
        self._remember_configuration()
        self._remember_session()
        self.settings_store.flush()
        super().closeEvent(event)

    def _analysis_options(self) -> dict:
        """What the analysis runs with: the settings, then whatever this run was told to use."""
        analysis = self.settings.analysis
        chosen = {
            "channels": analysis.channels,
            "t_num": analysis.t_num,
            "fft_points": analysis.fft_points,
            "a4": analysis.a4,
        }
        for name, value in self.overrides.items():
            if value is not None and name in chosen:
                chosen[name] = value
        return chosen

    def load_audio(self, path: str) -> None:
        self.audio_path = path
        store.set_value(self.settings, "paths", "last_audio_dir", str(Path(path).parent))
        self.settings_store.touch()
        self.loader = SpectrumLoader(path, parent=self, **self._analysis_options())
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
        self.song.gain = self.mix.audio_volume.value() / 100.0
        if abs(self.transport.speed.value() - 1.0) > 1e-3:
            self._start_stretch()

    def _on_speed_changed(self, _value: int = 0) -> None:
        self.speed_timer.start()  # the slider travels: only the speed it settles on is worth rendering

    def _apply_speed(self) -> None:
        """Take the settled speed: the notes follow at once, the song once it has been rerendered."""
        speed = self.transport.speed.value()
        self._set_note_speed(speed)
        if self.song.is_loaded and self.song.samples.size:
            self._start_stretch()

    def _set_note_speed(self, speed: float) -> None:
        """Hand the notes over at `speed`, carrying on from where they are playing."""
        beats = 60.0 / self.view.bpm  # scene units are beats, the players work in seconds
        notes = tuple((note.pitch, note.start * beats, note.duration * beats) for note in self.view.notes())
        if not notes:
            return
        playing = self.player.is_playing
        position = self._position()
        self.player.set_program(notes, speed)
        if playing:
            self.player.play(position)

    def _start_stretch(self) -> None:
        if not self.song.is_loaded or self.song.samples.size == 0 or self.stretch_loader is not None:
            return
        speed = self.transport.speed.value()
        if abs(speed - self.song.stretch) < 1e-3:
            return
        self.statusBar().showMessage(f"Rerendering the song for {speed:.2f}x playback …")
        self.stretch_loader = StretchLoader(self.song.samples, speed, parent=self)
        self.stretch_loader.loaded.connect(self._on_stretch_loaded)
        self.stretch_loader.failed.connect(
            lambda message: self.statusBar().showMessage(f"Speed change failed: {message}")
        )
        self.stretch_loader.start()

    def _on_stretch_loaded(self, buffer, speed: float) -> None:
        self.stretch_loader = None
        if abs(speed - self.transport.speed.value()) > 1e-3:
            self._start_stretch()  # the slider moved again while this one was rendering
            return
        self.song.set_stretched(buffer, speed)
        self.statusBar().showMessage(f"Playing at {speed:.2f}x, pitch unchanged")
        if self.pending_play:
            self.pending_play = False
            self._play()
            return
        self._set_note_speed(speed)  # the notes may still be running at the speed this replaced

    def _start_tempo(self) -> None:
        """Estimate the tempo of the loaded audio in the background, as a suggestion only."""
        if self.audio_path is None:
            return
        self.transport.tempo.hide()
        self.transport.detect.setEnabled(False)
        tempo = self.settings.tempo
        self.tempo_loader = TempoLoader(
            self.audio_path,
            estimator=tempo.estimator,
            window_seconds=tempo.window_seconds,
            window_hop_seconds=tempo.window_hop_seconds,
            parent=self,
        )
        self.tempo_loader.loaded.connect(self._on_tempo_loaded)
        self.tempo_loader.failed.connect(self._on_tempo_failed)
        self.tempo_loader.start()

    def _on_tempo_loaded(self, result) -> None:
        """Offer what was estimated as a candidate, whichever estimator produced it."""
        self.transport.detect.setEnabled(True)
        if not result.local:
            return
        residual = getattr(result, "residual", None)
        if residual is None:
            source = TEMPOCNN_SOURCE
            agreement = _patch_agreement(result)
        else:
            source = BEAT_SOURCE
            agreement = result.agreement
        self.transport.tempo.estimate(result.bpm, agreement, len(result.local), source, residual)

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
            if abs(speed - self.song.stretch) > 1e-3:
                self.pending_play = True  # play once the song has been rerendered for this speed
                self._start_stretch()
                return
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
        playing = self._is_playing()
        self.transport.set_position(seconds)
        self.transport.set_playing(playing)
        self.view.playing = playing
        self.view.set_playhead(seconds)

    def _on_playback_finished(self) -> None:
        if self._is_playing():  # the other layer is still running
            return
        self.position_timer.stop()
        self._show_position()

    def _on_midi_volume(self, value: float) -> None:
        self.player.gain = value / 100.0

    def _on_audio_volume(self, value: float) -> None:
        self.song.gain = value / 100.0

    def _on_note_preview(self, pitch: int) -> None:
        """Audition a note the user clicked or drew."""
        self.player.preview(pitch, self.settings.playback.preview_seconds)

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
            "middle drag: pan  |  ctrl wheel: zoom x, ctrl shift wheel: zoom y  |  gear: settings"
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
    parser.add_argument("--channels", choices=CHANNEL_MODES, help="which channels to analyse, over the settings")
    parser.add_argument("--t-num", type=float, help="spectrum frames per second, over the settings")
    parser.add_argument("--gain", type=float, help="spectrum gain for this run")
    parser.add_argument("--contrast", type=float, help="spectrum contrast for this run")
    return parser.parse_args(argv)


def _patch_agreement(result) -> float:
    """Share of the TempoCNN patches that agree with the tempo it settled on."""
    return sum(abs(local.bpm - result.bpm) <= result.bpm * TOLERANCE for local in result.local) / len(result.local)


def main() -> int:
    args = parse_args()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setStyleSheet(STYLE_SHEET)
    window = MainWindow(audio=args.audio, overrides={"channels": args.channels, "t_num": args.t_num})
    window.apply_overrides(gain=args.gain, contrast=args.contrast)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
