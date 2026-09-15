# SPDX-License-Identifier: AGPL-3.0-only
"""Namioto piano roll. Run with `uv run namioto`."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QByteArray, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from namioto import midi, project
from namioto import settings as store
from namioto.beats import estimate
from namioto.channels import Channel, free_channel
from namioto.channels import audible as audible_channels
from namioto.channels import set_field as channel_set_field
from namioto.playback import note_frequency
from namioto.spectrum import CHANNEL_MODES, NoteSpectrum
from namioto.ui import theme
from namioto.ui.audio import open_player
from namioto.ui.channel_panel import ChannelPanel
from namioto.ui.controls import ControlArea, EditBar, MixBar, TransportBar
from namioto.ui.midi_dialog import MidiImportDialog
from namioto.ui.roll import SNAP_CHOICES, PianoKeyboard, PianoRollView, TimelineRuler, note_name
from namioto.ui.settings_dialog import SettingsDialog, SettingsStore
from namioto.ui.song import SongPlayer, load_song
from namioto.ui.spectrogram import SpectrumLoader

POSITION_INTERVAL_MS = 40
SPEED_SETTLE_MS = 100
BEAT_SOURCE = "Beat tracking and least-squares fit"
PROJECT_FILTER = f"Namioto project (*{project.SUFFIX})"
MIDI_FILTER = f"MIDI file ({' '.join(f'*{suffix}' for suffix in midi.SUFFIXES)})"


@dataclass(frozen=True)
class _Binding:
    """One value the bars own: where it is read and written, and what a change to it means.

    The window keeps no second copy of these: `read` and `write` are the settings' side, `show` is
    what a new value does to the roll and the players, and both directions - remember on screen,
    apply a document - walk this one table, so a bar value is a line here and nowhere else.
    """

    section: str
    name: str
    read: Callable[[], Any]
    write: Callable[[Any], None]
    signal: Any
    show: Callable[[], None] | None = None
    override: str = ""


class TempoLoader(QThread):
    """Estimates the tempo of a file off the GUI thread, from the beats of its onsets."""

    loaded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(
        self,
        path: str | Path,
        window_seconds: float = 12.0,
        window_hop_seconds: float = 6.0,
        parent=None,
    ):
        super().__init__(parent)
        self.path = Path(path)
        self.window_seconds = window_seconds
        self.window_hop_seconds = window_hop_seconds

    def run(self) -> None:
        try:
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

        editor = self.settings.editor
        self.view = PianoRollView()
        self.view.set_zoom(editor.zoom_x, editor.zoom_y)
        self.view.initial_center = (self.settings.session.center_x, self.settings.session.center_y)
        self.view.overtone_highlight = editor.overtone_highlight
        self.view.division = editor.division
        self.ruler = TimelineRuler(self.view)
        self.keyboard = PianoKeyboard(self.view)
        self.player, self.player_name = self._make_player()
        self._current_player_key = self._player_key()
        self.player.gain = self.settings.playback.midi_volume / 100.0
        self.song = SongPlayer(self)
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
        self.edit = EditBar(SNAP_CHOICES, self)
        self.mix = MixBar(self)
        self.controls = ControlArea((self.transport, self.edit, self.mix))

        central = QWidget()
        column = QVBoxLayout(central)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self.controls)
        self.channel_panel = ChannelPanel(self.view)
        self.channel_panel.setVisible(False)  # one channel needs no sidebar; the icon opens it
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.channel_panel)
        row.addWidget(panel, 1)
        column.addLayout(row, 1)
        self.setCentralWidget(central)

        self.edit.snap.setCurrentIndex(max(0, self.edit.snap.findData(editor.snap)))
        self.edit.division.setChecked(editor.division == "beats")
        self.transport.auto_page.setChecked(editor.auto_page)
        self.transport.overtone.setChecked(editor.overtone_highlight)
        self.view.snap = self.edit.snap.currentData()
        self.transport.bpm.setValue(self.settings.tempo.bpm)
        self.transport.latency.setValue(self.settings.playback.latency_ms)
        self.transport.speed.set_value(self.settings.playback.speed)
        self.mix.gain.set_value(self.settings.spectrum.gain)
        self.mix.contrast.set_value(self.settings.spectrum.contrast)
        self.mix.audio_volume.set_value(self.settings.playback.audio_volume)
        self.mix.midi_volume.set_value(self.settings.playback.midi_volume)

        # one table for every bar value: it feeds the roll and is what the program remembers
        self._bindings = self._make_bindings()
        for binding in self._bindings:
            binding.signal.connect(partial(self._binding_changed, binding))
        self.edit.interaction_changed.connect(self.view.apply_interaction)
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
        self.transport.export_midi_requested.connect(self._on_export_midi)
        self.player.finished.connect(self._on_playback_finished)
        self.song.finished.connect(self._on_playback_finished)
        self.view.seek_requested.connect(self._seek)
        self.view.hover_changed.connect(self._on_hover_changed)
        self.view.note_preview.connect(self._on_note_preview)
        self.keyboard.key_preview.connect(self._on_note_preview)
        self.transport.settings_button.clicked.connect(self._open_settings)
        self.mix.midi_volume.slider.setToolTip(f"Volume of the note playback through {self.player_name}")
        self.view.gain = self.mix.gain.value()
        self.view.contrast = self.mix.contrast.value()
        self.view.bpm = self.transport.bpm.value()

        self.view.notes_changed.connect(self._update_status)
        self.view.notes_changed.connect(self._mark_dirty)
        self.view.channels_changed.connect(self._on_channels_changed)
        self.view.active_channel_changed.connect(self._on_active_channel_changed)
        self.edit.channels.toggled.connect(self.channel_panel.setVisible)
        self.transport.bpm.valueChanged.connect(self._mark_dirty)
        self.play_shortcut = QShortcut(QKeySequence("Space"), self)
        self.play_shortcut.activated.connect(self._toggle_play)
        for keys, slot in (
            ("Ctrl+O", self._on_open),
            ("Ctrl+S", self._on_save),
            ("Ctrl+Shift+S", self._on_save_as),
            ("Ctrl+C", self.view.copy_selection),
            ("Ctrl+V", self.view.paste_notes),
        ):
            QShortcut(QKeySequence(keys), self).activated.connect(slot)
        for standard, slot in (
            (QKeySequence.StandardKey.Undo, self.view.undo),
            (QKeySequence.StandardKey.Redo, self.view.redo),
        ):
            QShortcut(QKeySequence(standard), self).activated.connect(slot)
        QShortcut(QKeySequence("Ctrl+Y"), self).activated.connect(self.view.redo)
        self.cursor_note = QLabel()
        self.cursor_note.setObjectName("cursorNote")
        self.statusBar().addPermanentWidget(self.cursor_note)
        self._restore_session()
        if audio is None:
            self._show_hint()
        elif project.looks_like_project(audio):
            self.load_project(audio)
        elif midi.looks_like_midi(audio):
            self.import_midi(audio)
        else:
            self.load_audio(audio)
        self._update_status()
        self.view.setFocus()  # the roll holds the keyboard, so the bar opens without a focus ring on its first button

    def _make_player(self):
        return open_player(self, a4=self.settings.analysis.a4)

    def _player_key(self) -> tuple:
        """What a player is built from: a change to any of it means building a new one."""
        return (self.settings.analysis.a4,)

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
        self._on_spectrum_parameters()  # the seed was quiet, so show the roll what it landed on

    def _open_settings(self) -> None:
        dialog = SettingsDialog(
            self.settings,
            can_reanalyse=self.audio_path is not None,
            parent=self,
        )
        dialog.applied.connect(self.settings_store.apply)
        dialog.reanalyse_requested.connect(self._reanalyse)
        dialog.exec()

    def _reanalyse(self) -> None:
        if self.audio_path is not None:
            self.load_audio(self.audio_path)

    def _make_bindings(self) -> tuple[_Binding, ...]:
        """Every bar value the settings hold, one line each: read, write, the signal, and its effect."""
        return (
            _Binding(
                "spectrum",
                "gain",
                self.mix.gain.value,
                self.mix.gain.set_value,
                self.mix.gain.value_changed,
                self._on_spectrum_parameters,
                "gain",
            ),
            _Binding(
                "spectrum",
                "contrast",
                self.mix.contrast.value,
                self.mix.contrast.set_value,
                self.mix.contrast.value_changed,
                self._on_spectrum_parameters,
                "contrast",
            ),
            _Binding(
                "playback",
                "audio_volume",
                self.mix.audio_volume.value,
                self.mix.audio_volume.set_value,
                self.mix.audio_volume.value_changed,
                self._on_audio_volume,
            ),
            _Binding(
                "playback",
                "midi_volume",
                self.mix.midi_volume.value,
                self.mix.midi_volume.set_value,
                self.mix.midi_volume.value_changed,
                self._on_midi_volume,
            ),
            _Binding(
                "playback",
                "speed",
                self.transport.speed.value,
                self.transport.speed.set_value,
                self.transport.speed.slider.valueChanged,
                self._on_speed_changed,
            ),
            _Binding(
                "playback",
                "latency_ms",
                self.transport.latency.value,
                self.transport.latency.setValue,
                self.transport.latency.valueChanged,
            ),
            _Binding(
                "tempo",
                "bpm",
                self.transport.bpm.value,
                self.transport.bpm.setValue,
                self.transport.bpm.valueChanged,
                self._on_bpm_changed,
            ),
            _Binding(
                "editor",
                "snap",
                self._snap_value,
                self._set_snap,
                self.edit.snap.currentIndexChanged,
                self._on_snap_changed,
            ),
            _Binding(
                "editor",
                "division",
                self._division_value,
                self._set_division,
                self.edit.division_changed,
                self._on_division_changed,
            ),
            _Binding(
                "editor",
                "auto_page",
                self.transport.auto_page.isChecked,
                self.transport.auto_page.setChecked,
                self.transport.auto_page_toggled,
            ),
            _Binding(
                "editor",
                "overtone_highlight",
                self.transport.overtone.isChecked,
                self.transport.overtone.setChecked,
                self.transport.overtone_toggled,
                self._on_overtone,
            ),
        )

    def _snap_value(self) -> float:
        return self.edit.snap.currentData()

    def _set_snap(self, value: float) -> None:
        self.edit.snap.setCurrentIndex(max(0, self.edit.snap.findData(value)))

    def _division_value(self) -> str:
        return "beats" if self.edit.division.isChecked() else "seconds"

    def _set_division(self, value: str) -> None:
        self.edit.division.setChecked(value == "beats")

    def _binding_changed(self, binding: _Binding, *_args) -> None:
        """A bar value the user moved: it reaches the roll, and it is what the program remembers."""
        if self._seeding:  # the window filled the bar in, so this is not a change to write back
            return
        if binding.show is not None:
            binding.show()
        self._display_overrides.clear()  # the bar was touched after all, so it is what is remembered
        self._remember_configuration()
        self.settings_store.touch()

    def _on_settings_changed(self, settings) -> None:
        """Take a settings object over the running one: a project was opened, or the window applied.

        Every bar is filled in quietly - `_seeding` keeps the write from looking like a user change -
        and then told to show its value, so the roll follows even when the widget already held it.
        """
        self.settings = settings
        self._seeding = True
        try:
            for binding in self._bindings:
                binding.write(store.get_value(settings, binding.section, binding.name))
                if binding.show is not None:
                    binding.show()
            self.view.set_zoom(settings.editor.zoom_x, settings.editor.zoom_y)
            self.view.refresh()
            if self._player_key() != self._current_player_key:
                self._rebuild_player()
        finally:
            self._seeding = False
        self._update_status()

    def _rebuild_player(self) -> None:
        """A player that has to be built again - for another tuning - takes the notes over."""
        playing = self.player.is_playing
        position = self._position()
        self.player.stop()
        self.player, self.player_name = self._make_player()
        self._current_player_key = self._player_key()
        self.player.gain = self.mix.midi_volume.value() / 100.0
        self.player.finished.connect(self._on_playback_finished)
        self.mix.midi_volume.slider.setToolTip(f"Volume of the note playback through {self.player_name}")
        self._send_program()
        if playing:
            self.player.play(position)

    def _program(self) -> tuple[tuple, tuple]:
        """The notes of every channel that sounds, and each sounding channel's instrument, volume."""
        beats = self.view.seconds_per_beat
        audible = set(audible_channels(self.view.channels))
        notes = tuple(
            (note.pitch, note.start * beats, note.duration * beats, note.channel)
            for note in self.view.notes()
            if note.channel in audible
        )
        programs = tuple(
            (channel.channel, channel.program, channel.volume)
            for channel in self.view.channels
            if channel.channel in audible
        )
        return notes, programs

    def _send_program(self) -> None:
        """Hand the current notes and their channels to the player, as playback speed leaves them."""
        notes, programs = self._program()
        self.player.set_program(notes, self.transport.speed.value(), programs)

    def _remember_configuration(self) -> None:
        """The bar values are the settings, so what is on screen is what comes back next time."""
        for binding in self._bindings:
            if binding.override and binding.override in self._display_overrides:
                continue
            store.set_value(self.settings, binding.section, binding.name, binding.read())
        store.set_value(self.settings, "editor", "zoom_x", self.view.zoom[0])
        store.set_value(self.settings, "editor", "zoom_y", self.view.zoom[1])

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
        """What the app's file keeps: a document's values never become the program's defaults, and the
        ones that belong to a song - the tempo, the offset between sound and picture - keep the
        default they are written with."""
        kept = store.clone(self.settings)
        for section, item in store.PROJECT_FIELDS:
            if not item.remembered:
                store.set_value(kept, section, item.name, item.default)
            elif self._app_defaults is not None:
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
            self,
            "Open project",
            self._start_directory(),
            f"{PROJECT_FILTER};;{MIDI_FILTER};;All files (*)",
        )
        if chosen:
            if midi.looks_like_midi(chosen):
                self.import_midi(chosen)
            else:
                self.load_project(chosen)

    def _on_save(self) -> bool:
        if self.project_path is None:
            return self._on_save_as()
        return self.save_project(self.project_path)

    def _on_save_as(self) -> bool:
        name = Path(self.audio_path).stem if self.audio_path is not None else "untitled"
        suggested = Path(self._start_directory()) / f"{name}{project.SUFFIX}"
        chosen, _filter = QFileDialog.getSaveFileName(
            self,
            "Save project",
            str(self.project_path or suggested),
            PROJECT_FILTER,
        )
        if not chosen:
            return False
        target = Path(chosen)
        if not project.looks_like_project(target):
            target = target.with_name(target.name + project.SUFFIX)
        return self.save_project(target)

    def _on_export_midi(self) -> bool:
        """Write the roll out as a MIDI file, a file of its own and never the project's name."""
        name = Path(self.audio_path).stem if self.audio_path is not None else "untitled"
        suggested = Path(self._start_directory()) / f"{name}{midi.SUFFIXES[0]}"
        chosen, _filter = QFileDialog.getSaveFileName(
            self,
            "Export MIDI",
            str(suggested),
            MIDI_FILTER,
        )
        if not chosen:
            return False
        target = Path(chosen)
        if not midi.looks_like_midi(target):
            target = target.with_name(target.name + midi.SUFFIXES[0])
        return self.export_midi(target)

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
            self.view.set_channels(opened.channels)
            self.view.set_notes(
                (note.pitch, note.start / per_beat, note.duration / per_beat, note.channel) for note in opened.notes
            )
            self.view.center_on(self.settings.session.center_x, self.settings.session.center_y)
            self.view.undo_stack.clear()  # another document starts its history over
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
            sorted(
                project.Note(note.start * beats, note.duration * beats, note.pitch, note.channel)
                for note in self.view.notes()
            )
        )
        payload = project.Project(
            values=store.project_values(self.settings),
            audio=project.store_audio(target, self.audio_path),
            channels=tuple(self.view.channels),
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

    def import_midi(self, path: str | Path) -> bool:
        """Put the notes and channels of a MIDI file into the running session, audio and view and all.

        Importing a MIDI over analyzed audio is the way a transcription made elsewhere is checked
        against the sound it came from, so nothing here is cleared away but the notes - and when
        there are notes to lose, the dialog says so and offers to merge into the channels instead.
        """
        try:
            imported = midi.read(path, wavetone=self.settings.midi.wavetone)
        except (OSError, EOFError, ValueError) as error:
            self.statusBar().showMessage(f"MIDI file could not be read: {error}")
            return False
        # the file's notes land on the roll's channels, keeping the instrument and volume the file
        # gave them; a name is not among them, because a MIDI channel cannot carry one
        origin = Path(path).name
        mode, mapping = "replace", ()
        if self.view.notes():
            occupied = {note.channel for note in self.view.notes()}
            dialog = MidiImportDialog(imported, self.view.channels, origin, occupied, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            mode, mapping = dialog.mode(), tuple(dialog.mapping())
        self._loading = True
        try:
            if mode == "merge":
                self._merge_midi(imported, mapping)
            else:
                self.transport.bpm.setValue(imported.bpm)  # scene units are beats, and the file sets them
                beats = self.view.seconds_per_beat
                self.view.replace(
                    imported.channels,
                    [(note.pitch, note.start / beats, note.duration / beats, note.channel) for note in imported.notes],
                    "Import MIDI",
                )
        finally:
            self._loading = False
        self._mark_dirty()
        verb = "Merged" if mode == "merge" else "Imported"
        message = [f"{verb} {len(imported.notes)} notes from {origin}"]
        if imported.tempo_changes:
            message.append(f"{imported.tempo_changes} tempo changes; the grid takes the first tempo")
        if imported.dropped:
            message.append(f"{imported.dropped} note events left out")
        if imported.left_out:
            message.append(f"channels {' '.join(str(channel + 1) for channel in imported.left_out)} left out")
        self.statusBar().showMessage(" — ".join(message))
        return True

    def _merge_midi(self, imported, mapping) -> None:
        """Add the file's notes to the roll, on the channels the dialog pointed them at.

        The tempo stays where it is: both sets of notes are timed against the same audio, and moving
        the grid under them would take the ones already there off it. A channel that already carries
        notes is merged into and keeps what it is; an empty one the file walks into takes the file's
        instrument and volume, exactly as a new one would.
        """
        channels = {channel.channel: channel for channel in self.view.channels}
        carried = {note.channel for note in self.view.notes()}
        landing: dict[int, int] = {}
        for source, wanted in enumerate(imported.channels):
            target = mapping[source]
            if target >= 0 and target in carried:
                landing[wanted.channel] = target
                continue
            place = target
            if place < 0:
                free = free_channel(channels.values())
                place = wanted.channel if wanted.channel not in channels else free
                if place is None:
                    place = wanted.channel  # sixteen channels are full; the file shares its own
            channels[place] = channel_set_field(
                channels.get(place, Channel(channel=place)), program=wanted.program, volume=wanted.volume
            )
            landing[wanted.channel] = place
        kept = [(note.pitch, note.start, note.duration, note.channel) for note in self.view.notes()]
        beats = self.view.seconds_per_beat
        arriving = [
            (note.pitch, note.start / beats, note.duration / beats, landing[note.channel]) for note in imported.notes
        ]
        self.view.replace(channels.values(), kept + arriving, "Merge MIDI")

    def export_midi(self, path: str | Path) -> bool:
        """Write every channel out as MIDI, on the project's own tempo."""
        beats = self.view.seconds_per_beat
        notes = tuple(
            project.Note(note.start * beats, note.duration * beats, note.pitch, note.channel)
            for note in self.view.notes()
        )
        try:
            midi.write(
                path,
                tuple(self.view.channels),
                notes,
                self.settings.tempo.bpm,
                wavetone=self.settings.midi.wavetone,
            )
        except OSError as error:
            self.statusBar().showMessage(f"MIDI file could not be written: {error}")
            return False
        self.statusBar().showMessage(f"Exported {Path(path).name} — {len(notes)} notes")
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
        if not self._loading and str(path) != self.audio_path:
            beside = Path(path).with_suffix(project.SUFFIX)
            if self.project_path is None and beside.exists() and self.load_project(beside):
                return
            # another song brings its own tempo and offset; a re-analysis of the same one does not
            self.transport.bpm.setValue(store.FIELD_SPECS[("tempo", "bpm")].default)
            self.transport.latency.setValue(store.FIELD_SPECS[("playback", "latency_ms")].default)
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
        self.song.speed = self.transport.speed.value()

    def _on_speed_changed(self, _value: int = 0) -> None:
        # the song retunes in place as the slider travels; the notes follow once it settles
        self.song.speed = self.transport.speed.value()
        self.speed_timer.start()

    def _apply_speed(self) -> None:
        """Take the settled speed for the note layer; the song already follows the slider."""
        self._set_note_speed()

    def _set_note_speed(self) -> None:
        """Hand the notes over at the settled speed, carrying on from where they are playing."""
        if not self.view.notes():
            return
        playing = self.player.is_playing
        position = self._position()
        self._send_program()
        if playing:
            self.player.play(position)

    def _on_channels_changed(self, *_args) -> None:
        """A channel edit is a document change, and a mute or instrument lands in the running sound."""
        self._mark_dirty()
        if self._is_playing():
            self._set_note_speed()

    def _on_active_channel_changed(self, number: int) -> None:
        channel = next(channel for channel in self.view.channels if channel.channel == number)
        self.statusBar().showMessage(f"Drawing into {channel.label}", 2000)

    def _start_tempo(self) -> None:
        """Estimate the tempo of the loaded audio in the background, as a suggestion only."""
        if self.audio_path is None:
            return
        self.transport.suggestion.hide()
        self.transport.detect.setEnabled(False)
        tempo = self.settings.tempo
        self.tempo_loader = TempoLoader(
            self.audio_path,
            window_seconds=tempo.window_seconds,
            window_hop_seconds=tempo.window_hop_seconds,
            parent=self,
        )
        self.tempo_loader.loaded.connect(self._on_tempo_loaded)
        self.tempo_loader.failed.connect(self._on_tempo_failed)
        self.tempo_loader.start()

    def _on_tempo_loaded(self, result) -> None:
        """Offer what was estimated as a candidate, unless the field already holds a tempo of its own."""
        self.transport.detect.setEnabled(True)
        if not result.local:
            return
        if self.transport.bpm.value() != store.FIELD_SPECS[("tempo", "bpm")].default:
            return  # a tempo the user set, or took from an estimate, is not one to suggest over
        if round(result.bpm) == round(self.transport.bpm.value()):
            return  # the balloon would read what the field already says
        self.transport.suggestion.estimate(
            result.bpm, result.agreement, len(result.local), BEAT_SOURCE, result.residual
        )
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
        notes, _channels = self._program()
        if not notes and not self.song.is_loaded:
            self.statusBar().showMessage("Nothing to play: load a file or draw some notes")
            return
        seconds = self._position()
        if seconds >= self._duration() - 1e-3:
            seconds = 0.0
        speed = self.transport.speed.value()
        if self.song.is_loaded:
            self.song.speed = speed
            self.song.play(seconds)
        self._send_program()
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
        if playing and self.transport.auto_page.isChecked():
            self.view.follow_playhead(seconds)

    def _on_playback_finished(self) -> None:
        if self._is_playing():  # the other layer is still running
            return
        self.position_timer.stop()
        self._show_position()

    def _on_midi_volume(self, *_args) -> None:
        self.player.gain = self.mix.midi_volume.value() / 100.0

    def _on_audio_volume(self, *_args) -> None:
        self.song.gain = self.mix.audio_volume.value() / 100.0

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
            "ctrl C: copy the selection, ctrl V: paste it at the playhead  |  "
            "ctrl Z: undo, ctrl shift Z: redo  |  "
            "middle drag: pan  |  ctrl wheel: zoom x, ctrl shift wheel: zoom y  |  gear: settings"
        )

    def _on_division_changed(self, *_args) -> None:
        self.view.division = self._division_value()
        self.view.refresh()

    def _on_overtone(self, *_args) -> None:
        self.view.overtone_highlight = self.transport.overtone.isChecked()
        self.view.refresh()

    def _on_snap_changed(self, *_args) -> None:
        self.view.snap = self._snap_value()

    def _on_bpm_changed(self, *_args) -> None:
        self.transport.suggestion.hide()  # a tempo the user typed wins over the suggestion
        self.view.bpm = self.transport.bpm.value()

    def _on_spectrum_parameters(self, *_args) -> None:
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
    parser.add_argument("audio", nargs="?", help="audio file to analyse, or a .nto project or a .mid file to open")
    parser.add_argument("--channels", choices=CHANNEL_MODES, help="which channels to analyse, over the settings")
    parser.add_argument("--t-num", type=float, help="spectrum frames per second, over the settings")
    parser.add_argument("--gain", type=float, help="spectrum gain for this run")
    parser.add_argument("--contrast", type=float, help="spectrum contrast for this run")
    return parser.parse_args(argv)


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
