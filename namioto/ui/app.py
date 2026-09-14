# SPDX-License-Identifier: AGPL-3.0-only
"""Namioto piano roll. Run with `uv run namioto`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PyQt6.QtCore import QByteArray, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGridLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from namioto import project
from namioto import settings as store
from namioto.beats import TOLERANCE, estimate
from namioto.playback import note_frequency
from namioto.spectrum import CHANNEL_MODES, NoteSpectrum
from namioto.ui import theme
from namioto.ui.audio import open_player, port_names
from namioto.ui.controls import ControlArea, EditBar, MixBar, TransportBar
from namioto.ui.roll import SNAP_CHOICES, PianoKeyboard, PianoRollView, TimelineRuler, note_name
from namioto.ui.settings_dialog import SettingsDialog, SettingsStore
from namioto.ui.song import SongPlayer, load_song, stretch_song
from namioto.ui.spectrogram import SpectrumLoader

POSITION_INTERVAL_MS = 40
SPEED_SETTLE_MS = 400
BEAT_SOURCE = "Beat tracking and least-squares fit"
TEMPOCNN_SOURCE = "TempoCNN"


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
        self.settings_store.source = self._file_settings
        self.settings_store.changed.connect(self._on_settings_changed)
        self.settings_store.failed.connect(lambda message: self.statusBar().showMessage(message))
        self.overrides = dict(overrides or {})  # values this run was asked for, never written back
        self._display_overrides: dict[str, float] = {}
        self._seeding = False
        self._loading = False
        self._app_defaults: store.Settings | None = None
        self.project_path: Path | None = None
        self.project_dirty = False
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
        corner.setObjectName("corner")

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

        self.transport = TransportBar(self)
        self.transport.speed.slider.valueChanged.connect(self._on_speed_changed)
        self.edit = EditBar(SNAP_CHOICES, self)
        self.mix = MixBar(self)
        self.controls = ControlArea((self.transport, self.edit, self.mix))

        central = QWidget()
        column = QVBoxLayout(central)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.controls)
        column.addWidget(panel, 1)
        self.setCentralWidget(central)

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
        self.edit.tool_changed.connect(self._on_tool_changed)
        self.edit.mode_changed.connect(self._on_mode_changed)
        self.edit.division_changed.connect(self._on_division_changed)
        self.transport.bpm.valueChanged.connect(self._on_bpm_changed)
        self.transport.detect.clicked.connect(self._start_tempo)
        self.transport.suggestion.applied.connect(self._apply_tempo)
        self.transport.suggestion.dismissed.connect(self.transport.suggestion.hide)
        self.transport.rewind_requested.connect(lambda: self._seek(0.0))
        self.transport.forward_requested.connect(lambda: self._seek(self._duration()))
        self.transport.play_pause_requested.connect(self._toggle_play)
        self.transport.play_from_start_requested.connect(self._play_from_start)
        self.transport.stop_requested.connect(self._stop)
        self.transport.open_requested.connect(self._on_open)
        self.transport.save_requested.connect(self._on_save)
        self.player.finished.connect(self._on_playback_finished)
        self.song.finished.connect(self._on_playback_finished)
        self.view.seek_requested.connect(self._seek)
        self.view.hover_changed.connect(self._on_hover_changed)
        self.view.note_preview.connect(self._on_note_preview)
        self.keyboard.key_preview.connect(self._on_note_preview)
        self.transport.settings_button.clicked.connect(self._open_settings)
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
        self.view.notes_changed.connect(self._mark_dirty)
        self.transport.bpm.valueChanged.connect(self._mark_dirty)
        self.play_shortcut = QShortcut(QKeySequence("Space"), self)
        self.play_shortcut.activated.connect(self._toggle_play)
        for keys, slot in (("Ctrl+O", self._on_open), ("Ctrl+S", self._on_save), ("Ctrl+Shift+S", self._on_save_as)):
            QShortcut(QKeySequence(keys), self).activated.connect(slot)
        self.cursor_note = QLabel()
        self.cursor_note.setObjectName("cursorNote")
        self.statusBar().addPermanentWidget(self.cursor_note)
        self._restore_session()
        if audio is None:
            self._show_hint()
        elif project.looks_like_project(audio):
            self.load_project(audio)
        else:
            self.load_audio(audio)
        self._update_status()
        self.view.setFocus()  # the roll holds the keyboard, so the bar opens without a focus ring on its first button

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
        beats = self.view.seconds_per_beat
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

    def _remember_session(self) -> None:
        session = self.settings.session
        session.geometry = self.saveGeometry().toBase64().data().decode()
        centre = self.view.mapToScene(self.view.viewport().rect().center())
        session.center_x = round(centre.x(), 1)
        session.center_y = round(centre.y(), 1)

    def _file_settings(self):
        """What the app's file keeps: once a project is open, the values you had before it are still
        your defaults, so the project's own values never leak into them."""
        if self._app_defaults is None:
            return self.settings
        kept = store.clone(self.settings)
        for section, item in store.PROJECT_FIELDS:
            store.set_value(kept, section, item.name, store.get_value(self._app_defaults, section, item.name))
        return kept

    def _document_name(self) -> str:
        name = self.project_path.stem if self.project_path is not None else "Untitled"
        return f"{name}*" if self.project_dirty else name

    def _mark_dirty(self, *_args) -> None:
        """What a save would otherwise lose: the notes, the tempo and the audio. The view and the
        listening values are written with a project, but do not mark it as changed."""
        if self._loading or self.project_dirty:
            return
        self.project_dirty = True
        self._update_status()

    def _start_directory(self) -> str:
        """Where a file dialog opens: the folder of the project in use, else the last one opened."""
        if self.project_path is not None:
            return str(self.project_path.parent)
        return self.settings.paths.last_audio_dir or str(Path.home())

    def _confirm_discard(self) -> bool:
        """Ask before unsaved notes go; False means the caller should do nothing. A roll that has no
        file yet is a sketch, so it is not worth interrupting anyone over."""
        if self.project_path is None or not self.project_dirty:
            return True
        choice = QMessageBox.warning(
            self,
            "Namioto",
            f"Save the changes to {self.project_path.name}?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Cancel:
            return False
        if choice == QMessageBox.StandardButton.Save:
            return self._on_save()
        return True

    def _on_open(self) -> None:
        if not self._confirm_discard():
            return
        chosen, _filter = QFileDialog.getOpenFileName(
            self, "Open project", self._start_directory(), f"Namioto project (*{project.SUFFIX});;All files (*)"
        )
        if chosen:
            self.load_project(chosen)

    def _on_save(self) -> bool:
        if self.project_path is None:
            return self._on_save_as()
        return self.save_project(self.project_path)

    def _on_save_as(self) -> bool:
        suggested = Path(self._start_directory()) / f"untitled{project.SUFFIX}"
        chosen, _filter = QFileDialog.getSaveFileName(
            self,
            "Save project",
            str(self.project_path or suggested),
            f"Namioto project (*{project.SUFFIX})",
        )
        if not chosen:
            return False
        target = Path(chosen)
        if not project.looks_like_project(target):
            target = target.with_name(target.name + project.SUFFIX)
        return self.save_project(target)

    def load_project(self, path: str | Path) -> bool:
        """Open a project: its values come over the running ones, and its notes replace the roll."""
        try:
            opened = project.load(path)
        except (OSError, ValueError) as error:
            self.statusBar().showMessage(f"Project could not be opened: {error}")
            return False
        self._loading = True
        try:
            if self._app_defaults is None:
                self._app_defaults = store.clone(self.settings)
            store.apply_project_values(self.settings, opened.values)
            self.settings_store.apply(self.settings, save=False)
            self.project_path = Path(path)
            self.project_dirty = False
            missing = self._open_audio(project.resolve_audio(self.project_path, opened.audio))
            per_beat = self.view.seconds_per_beat  # scene units are beats, the file keeps seconds
            self.view.set_notes((note.pitch, note.start / per_beat, note.duration / per_beat) for note in opened.notes)
            self.view.center_on(self.settings.session.center_x, self.settings.session.center_y)
        finally:
            self._loading = False
        self._update_status()
        self.statusBar().showMessage(f"Opened {self.project_path.name} — {len(opened.notes)} notes{missing}")
        return True

    def save_project(self, path: str | Path) -> bool:
        """Write the notes and the values that belong to them out to `path`."""
        self._remember_configuration()
        self._remember_session()
        target = Path(path)
        beats = self.view.seconds_per_beat
        # scene order is not a file's order: sorted notes keep a saved project stable to diff
        notes = tuple(
            sorted(project.Note(note.start * beats, note.duration * beats, note.pitch) for note in self.view.notes())
        )
        payload = project.Project(
            values=store.project_values(self.settings),
            audio=project.store_audio(target, self.audio_path),
            notes=notes,
        )
        try:
            project.save(payload, target)
        except OSError as error:
            self.statusBar().showMessage(f"Project could not be saved: {error}")
            return False
        self.project_path = target
        self.project_dirty = False
        store.set_value(self.settings, "paths", "last_audio_dir", str(target.parent))
        self.settings_store.touch()
        self._update_status()
        self.statusBar().showMessage(f"Saved {target.name} — {len(notes)} notes")
        return True

    def _open_audio(self, target: Path | None) -> str:
        """Load the audio a project names, or say why there is none: its notes are worth having either way."""
        if target is not None and target.exists():
            self.load_audio(str(target))
            return ""
        self._clear_audio()
        return f" (audio not found: {target})" if target is not None else ""

    def _clear_audio(self) -> None:
        """Forget the analysed file, for a project that names one this machine does not have."""
        self.position_timer.stop()
        self.audio_path = None
        self.view.set_spectrum(None)
        self.song.unload()
        self.player.stop()
        self.transport.detect.setEnabled(False)
        self.transport.suggestion.hide()
        self._show_position()

    def closeEvent(self, event) -> None:
        if not self._confirm_discard():
            event.ignore()
            return
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
        self._mark_dirty()
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
        beats = self.view.seconds_per_beat  # scene units are beats, the players work in seconds
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
        self.transport.suggestion.hide()
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
        if round(result.bpm) == round(self.transport.bpm.value()):
            return  # the balloon would read what the field already says
        residual = getattr(result, "residual", None)
        if residual is None:
            source = TEMPOCNN_SOURCE
            agreement = _patch_agreement(result)
        else:
            source = BEAT_SOURCE
            agreement = result.agreement
        self.transport.suggestion.estimate(result.bpm, agreement, len(result.local), source, residual)
        self.transport.suggestion.show_under(self.transport.bpm)

    def _on_tempo_failed(self, message: str) -> None:
        self.transport.detect.setEnabled(self.audio_path is not None)
        self.statusBar().showMessage(f"Tempo estimation failed: {message}")

    def _apply_tempo(self, bpm: float) -> None:
        self.transport.suggestion.hide()
        self.transport.bpm.setValue(bpm)
        self.statusBar().showMessage(f"Tempo set to {bpm:.0f} BPM from the audio")

    def _play(self) -> None:
        """Send the notes to the synth and start the audio file, both from where the cursor sits."""
        beats = self.view.seconds_per_beat  # scene units are beats, the players work in seconds
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
        self.transport.suggestion.hide()  # a tempo the user typed wins over the suggestion
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
        self.setWindowTitle(f"{self._document_name()} — Namioto — {len(self.view.notes())} notes")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="namioto", description="Namioto piano-roll MIDI editor")
    parser.add_argument("audio", nargs="?", help="audio file to analyse, or a .nto project to open")
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
    theme.apply(app)
    window = MainWindow(audio=args.audio, overrides={"channels": args.channels, "t_num": args.t_num})
    window.apply_overrides(gain=args.gain, contrast=args.contrast)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
