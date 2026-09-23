# SPDX-License-Identifier: AGPL-3.0-only
"""The editor window and its wiring: the bars, the roll, the sidebar and the lyrics strip.

`MainWindow` owns the document, the players, the settings and every path the program opens or
writes, and runs in this one process - the analysis that must not touch the GUI goes out to the
`LoadingThread` loaders at the top of the file, or to a spawned `namioto-transcription` child. Run
it with `uv run namioto`.
"""

from __future__ import annotations

import argparse
import multiprocessing
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QByteArray, QEvent, QLibraryInfo, QProcess, QTimer, QTranslator, pyqtSignal
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

from namioto import i18n, lyrics, midi, project
from namioto import settings as store
from namioto.analysis import bpm
from namioto.analysis.spectrum import CHANNEL_MODES, NoteSpectrum
from namioto.channels import Channel, free_channel
from namioto.channels import audible as audible_channels
from namioto.channels import set_field as channel_set_field
from namioto.karaoke import KrcError, conflicts, group_morae, mora_lines, note_counts, text_key, with_counts
from namioto.playback import note_frequency
from namioto.ui import theme
from namioto.ui.align_dialog import AlignDialog
from namioto.ui.audio import open_player
from namioto.ui.channel_panel import ChannelPanel
from namioto.ui.controls import ControlArea, EditBar, MixBar, TransportBar
from namioto.ui.loading import LoadingThread
from namioto.ui.lyrics_dialog import LyricsDialog, LyricsWatcher
from namioto.ui.midi_dialog import MidiImportDialog
from namioto.ui.roll import SNAP_CHOICES, PianoKeyboard, PianoRollView, TimelineRuler
from namioto.ui.settings_dialog import SettingsDialog, SettingsStore
from namioto.ui.song import SongPlayer, load_song
from namioto.ui.spectrogram import SpectrumLoader
from namioto.ui.strips import MoraStrip
from namioto.ui.text import note_name
from namioto.ui.transcription_dialog import TranscriptionDialog

POSITION_INTERVAL_MS = 40
SPEED_SETTLE_MS = 100
AUTOSAVE_DELAY_MS = 3000
# common libsndfile formats; anything rarer is reachable through All files
AUDIO_SUFFIXES = (
    ".wav",
    ".wave",
    ".flac",
    ".mp3",
    ".ogg",
    ".oga",
    ".opus",
    ".aiff",
    ".aif",
    ".aifc",
    ".au",
    ".caf",
    ".w64",
    ".rf64",
)


def _project_filter() -> str:
    return i18n.tr("Namioto project (*{suffix})", suffix=project.SUFFIX)


def _audio_filter() -> str:
    return i18n.tr("Audio file ({patterns})", patterns=" ".join(f"*{suffix}" for suffix in AUDIO_SUFFIXES))


def _midi_filter() -> str:
    return i18n.tr("MIDI file ({patterns})", patterns=" ".join(f"*{suffix}" for suffix in midi.SUFFIXES))


_translators: list[QTranslator] = []


def _install_translations(language: str) -> None:
    """Load Qt's own translations, so standard buttons and file dialogs follow the chosen language."""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return
    for translator in _translators:
        app.removeTranslator(translator)
    _translators.clear()
    locale = i18n.LOCALES.get(language)
    if language == i18n.DEFAULT or locale is None:
        return
    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    for name in (f"qtbase_{locale}", f"qt_{locale}"):
        translator = QTranslator()
        if translator.load(name, folder):
            app.installTranslator(translator)
            _translators.append(translator)


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


class TempoLoader(LoadingThread):
    """Estimates the tempo of a file off the GUI thread, with the chosen algorithm."""

    loaded = pyqtSignal(object)

    def __init__(
        self,
        path: str | Path,
        algorithm: str = bpm.DEFAULT,
        window_seconds: float = 12.0,
        window_hop_seconds: float = 6.0,
        parent=None,
    ):
        super().__init__(path, parent)
        self.algorithm = algorithm
        self.window_seconds = window_seconds
        self.window_hop_seconds = window_hop_seconds

    def load(self) -> None:
        self.loaded.emit(
            bpm.estimate(
                self.path,
                self.algorithm,
                window_seconds=self.window_seconds,
                window_hop_seconds=self.window_hop_seconds,
            )
        )


class SongLoader(LoadingThread):
    """Decodes the file for playback off the GUI thread."""

    loaded = pyqtSignal(object, int)

    def load(self) -> None:
        self.loaded.emit(*load_song(self.path))


class MainWindow(QMainWindow):
    def __init__(self, settings=None, overrides: dict | None = None):
        super().__init__()
        self.setWindowTitle("Namioto")
        self.resize(1200, 720)
        self.settings = settings if settings is not None else store.load()
        i18n.set_language(self.settings.general.language)
        _install_translations(i18n.current())
        self.settings_store = SettingsStore(self.settings, parent=self)
        self.settings_store.source = self._file_settings
        self.settings_store.changed.connect(self._on_settings_changed)
        self.settings_store.failed.connect(lambda message: self.statusBar().showMessage(message))
        self._theme = theme.apply(theme.running_app(), self.settings.general.style)
        theme.hints().colorSchemeChanged.connect(self._on_color_scheme)
        self.overrides = dict(overrides or {})  # values this run was asked for, never written back
        self._display_overrides: dict[str, float] = {}
        self._seeding = False
        self._loading = False
        self._app_defaults: store.Settings | None = None
        self.project_path: Path | None = None
        self.project_dirty = False
        self.audio_path: str | None = None
        self.lyrics_text = ""
        self._stored_lyrics: project.LyricTimes | None = None
        self._lyric_key = ""
        self._lyric_model = ""
        self._lyric_error = ""
        self.loader: SpectrumLoader | None = None
        self.tempo_loader: TempoLoader | None = None
        self._tempo_manual = False
        self.song_loader: SongLoader | None = None

        editor = self.settings.editor
        self.view = PianoRollView()
        self.view.set_zoom(editor.zoom_x, editor.zoom_y)
        self.view.initial_center = (self.settings.session.center_x, self.settings.session.center_y)
        self.view.overtone_highlight = editor.overtone_highlight
        self.view.division = editor.division
        self.ruler = TimelineRuler(self.view)
        self.mora_strip = MoraStrip(self.view)
        self.mora_strip.setVisible(False)
        self.mora_strip.mora_group_requested.connect(self._group_morae)
        self.keyboard = PianoKeyboard(self.view)
        self.player, self.player_name = self._make_player()
        self._current_player_key = self._player_key()
        self.player.gain = self.settings.playback.midi_volume / 100.0
        self.song = SongPlayer(self)
        self.lyrics_watcher = LyricsWatcher(self)
        self.lyrics_watcher.changed.connect(self._on_lyrics_file_changed)
        self.position_timer = QTimer(self)
        self.position_timer.setInterval(POSITION_INTERVAL_MS)
        self.position_timer.timeout.connect(self._show_position)
        self.speed_timer = QTimer(self)
        self.speed_timer.setSingleShot(True)
        self.speed_timer.setInterval(SPEED_SETTLE_MS)
        self.speed_timer.timeout.connect(self._apply_speed)
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(AUTOSAVE_DELAY_MS)
        self.autosave_timer.timeout.connect(self._autosave)

        corner = QWidget()
        corner.setFixedSize(self.keyboard.width(), self.ruler.height())

        layout = QGridLayout()
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(corner, 0, 0)
        layout.addWidget(self.ruler, 0, 1)
        layout.addWidget(self.mora_strip, 1, 1)
        layout.addWidget(self.keyboard, 2, 0)
        layout.addWidget(self.view, 2, 1)
        layout.setColumnStretch(1, 1)
        layout.setRowStretch(2, 1)
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
        self.transport.division.setChecked(editor.division == "beats")
        self.transport.auto_page.setChecked(editor.auto_page)
        self.transport.overtone.setChecked(editor.overtone_highlight)
        self.view.snap = self.edit.snap.currentData()
        self.transport.bpm.setValue(self.settings.tempo.bpm)
        self.transport.latency.setValue(self.settings.playback.latency_ms)
        self.view.set_offset(self.transport.latency.value() / 1000.0)
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
        self.transport.detect.clicked.connect(lambda: self._start_tempo(manual=True))
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
        self.edit.transcribe_requested.connect(self._open_transcription)
        self.edit.lyrics_requested.connect(self._open_lyrics)
        self.edit.align_requested.connect(self._open_align)
        self.player.finished.connect(self._on_playback_finished)
        self.song.finished.connect(self._on_playback_finished)
        self.view.seek_requested.connect(self._seek)
        self.view.hover_changed.connect(self._on_hover_changed)
        self.view.note_preview.connect(self._on_note_preview)
        self.keyboard.key_preview.connect(self._on_note_preview)
        self.transport.settings_button.clicked.connect(self._open_settings)
        self.mix.midi_volume.slider.setToolTip(
            i18n.tr("Volume of the note playback through {player}", player=self.player_name)
        )
        self.view.gain = self.mix.gain.value()
        self.view.contrast = self.mix.contrast.value()
        self.view.bpm = self.transport.bpm.value()

        self.view.notes_changed.connect(self._update_status)
        self.view.notes_changed.connect(self._mark_dirty)
        self.view.lyrics_changed.connect(self._mark_dirty)
        self.view.channels_changed.connect(self._on_channels_changed)
        self.view.active_channel_changed.connect(self._on_active_channel_changed)
        self.transport.channels.toggled.connect(self.channel_panel.setVisible)
        self.edit.quantize_requested.connect(self.view.quantize_notes)
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
        self.statusBar().addPermanentWidget(self.cursor_note)
        self._restore_session()
        self._show_hint()
        self._watch_lyrics()
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
            parent=self,
        )
        dialog.applied.connect(self.settings_store.apply)
        dialog.exec()

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
                self._on_latency_changed,
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
                self.transport.division_changed,
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
        return "beats" if self.transport.division.isChecked() else "seconds"

    def _set_division(self, value: str) -> None:
        self.transport.division.setChecked(value == "beats")

    def _binding_changed(self, binding: _Binding, *_args) -> None:
        """A bar value the user moved: it reaches the roll, and it is what the program remembers."""
        if self._seeding:  # the window filled the bar in, so this is not a change to write back
            return
        if binding.show is not None:
            binding.show()
        self._display_overrides.clear()  # the bar was touched after all, so it is what is remembered
        self._remember_configuration()
        self.settings_store.touch()

    def _apply_theme(self) -> None:
        """Dress the window the way the settings ask, and follow the colours the desktop hands out.

        The colour the roll and the ruler draw with is read while they paint, so a switch is those
        two painting again; the keyboard keeps its fixed colours, and everything else is the
        style's to draw.
        """
        name = theme.apply(theme.running_app(), self.settings.general.style)
        if name == self._theme:
            return
        self._theme = name
        self.view.refresh()
        self.ruler.update()

    def _on_color_scheme(self, _scheme) -> None:
        """The desktop switched between light and dark, so the canvas follows it."""
        self._apply_theme()

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
        self._apply_theme()
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
        self.mix.midi_volume.slider.setToolTip(
            i18n.tr("Volume of the note playback through {player}", player=self.player_name)
        )
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

    def _capture_app_defaults(self) -> None:
        """What the program's own file keeps: a document's values must not become the defaults."""
        if self._app_defaults is None:
            self._app_defaults = store.clone(self.settings)

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
        name = self.project_path.stem if self.project_path is not None else i18n.tr("Untitled")
        return f"{name}*" if self.project_dirty else name

    def _mark_dirty(self, *_args) -> None:
        """What a save would otherwise lose: the notes, the tempo and the audio. The view and the
        listening values are written with a project, but do not mark it as changed."""
        if self._loading:
            return
        if not self.project_dirty:
            self.project_dirty = True
            self._update_status()
        if self.settings.general.auto_save:
            self.autosave_timer.start()

    def _autosave(self) -> None:
        """Write the open project out once editing stops, and when the window loses focus.

        Only a document that already has a file is written: a sketch with no name waits for Save As.
        A modal dialog is asking something of its own, so the window losing focus to it is not a
        reason to write - the save-on-close prompt would otherwise answer itself.
        """
        if not self.settings.general.auto_save or self._loading:
            return
        if not self.project_dirty or self.project_path is None:
            return
        if QApplication.activeModalWidget() is not None:
            return
        self.save_project(self.project_path)

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
            i18n.tr("Save the changes to {name}?", name=self.project_path.name),
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Cancel:
            return False
        if choice == QMessageBox.StandardButton.Save:
            return self._on_save()
        return True

    def _on_open(self) -> None:
        filters = [i18n.tr("All files (*)"), _project_filter(), _audio_filter()]
        if self.project_path is not None:  # a MIDI is a project's, so it is only worth offering inside one
            filters.append(_midi_filter())
        chosen, _filter = QFileDialog.getOpenFileName(
            self,
            i18n.tr("Open"),
            self._start_directory(),
            ";;".join(filters),
        )
        if chosen:
            self.open_file(chosen)

    def open_file(self, path: str | Path) -> bool:
        """Open a file by what it is - a project, a MIDI file to import, or audio to analyse.

        The command line and the Open dialog both come through here, so a file behaves the same
        whichever way it arrives. A project and audio are a document each, so switching to one asks
        about unsaved work first; a MIDI is only a part that goes into the project already open.
        """
        if project.looks_like_project(path):
            return self._confirm_discard() and self.load_project(path)
        if midi.looks_like_midi(path):
            return self.import_midi(path)
        return self._confirm_discard() and self.open_audio(str(path))

    def open_audio(self, path: str) -> bool:
        """Open audio as part of a project: the sibling project if there is one, else a new one.

        The project has to be named before the sound is analysed, because the notes and the
        analysis all belong to that file; a cancelled chooser leaves the audio untouched.
        """
        beside = Path(path).with_suffix(project.SUFFIX)
        if beside.exists():
            return self.load_project(beside)
        target = self._new_project_path(path)
        if target is None:
            return False
        self._capture_app_defaults()
        self.load_audio(path)
        return self.save_project(target)

    def _new_project_path(self, audio: str) -> Path | None:
        """Where a new project for an audio file is saved: its own name, in the audio's folder."""
        chosen, _filter = QFileDialog.getSaveFileName(
            self,
            i18n.tr("Save project"),
            str(Path(audio).with_suffix(project.SUFFIX)),
            _project_filter(),
        )
        if not chosen:
            return None
        target = Path(chosen)
        return target if project.looks_like_project(target) else target.with_name(target.name + project.SUFFIX)

    def _on_save(self) -> bool:
        if self.project_path is None:
            return self._on_save_as()
        return self.save_project(self.project_path)

    def _on_save_as(self) -> bool:
        name = Path(self.audio_path).stem if self.audio_path is not None else "untitled"
        suggested = Path(self._start_directory()) / f"{name}{project.SUFFIX}"
        chosen, _filter = QFileDialog.getSaveFileName(
            self,
            i18n.tr("Save project"),
            str(self.project_path or suggested),
            _project_filter(),
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
            i18n.tr("Export MIDI"),
            str(suggested),
            _midi_filter(),
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
            self.statusBar().showMessage(i18n.tr("Project could not be opened: {error}", error=error))
            return False
        self._loading = True
        try:
            self._capture_app_defaults()
            store.apply_project_values(self.settings, opened.values)
            self.settings_store.apply(self.settings, save=False)
            self.project_path = Path(path)
            self.project_dirty = False
            self.autosave_timer.stop()
            self._stored_lyrics = opened.lyrics
            self._watch_lyrics()
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
        self.statusBar().showMessage(
            i18n.tr("Opened {name} — {notes} notes", name=self.project_path.name, notes=len(opened.notes)) + missing
        )
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
        lyrics_times = (
            project.LyricTimes(key=self._lyric_key, model=self._lyric_model, lines=self.view.lyric_times)
            if self.view.lyric_lines
            else None
        )
        payload = project.Project(
            values=store.project_values(self.settings),
            audio=project.store_audio(target, self.audio_path),
            channels=tuple(self.view.channels),
            notes=notes,
            lyrics=lyrics_times,
        )
        try:
            project.save(payload, target)
        except OSError as error:
            self.statusBar().showMessage(i18n.tr("Project could not be saved: {error}", error=error))
            return False
        self.project_path = target
        self.project_dirty = False
        self.autosave_timer.stop()
        self._stored_lyrics = lyrics_times
        self._watch_lyrics()
        store.set_value(self.settings, "paths", "last_audio_dir", str(target.parent))
        self.settings_store.touch()
        self._update_status()
        self.statusBar().showMessage(i18n.tr("Saved {name} — {notes} notes", name=target.name, notes=len(notes)))
        return True

    def import_midi(self, path: str | Path) -> bool:
        """Put the notes and channels of a MIDI file into the running session, audio and view and all.

        Importing a MIDI over analyzed audio is the way a transcription made elsewhere is checked
        against the sound it came from, so nothing here is cleared away but the notes - and when
        there are notes to lose, the dialog says so and offers to merge into the channels instead.
        """
        if self.project_path is None:
            self.statusBar().showMessage(
                i18n.tr("Open a song first: a MIDI file is imported into a project, not on its own")
            )
            return False
        try:
            imported = midi.read(path, wavetone=self.settings.midi.wavetone)
        except (OSError, EOFError, ValueError) as error:
            self.statusBar().showMessage(i18n.tr("MIDI file could not be read: {error}", error=error))
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
        verb = i18n.tr("Merged") if mode == "merge" else i18n.tr("Imported")
        message = [i18n.tr("{verb} {notes} notes from {origin}", verb=verb, notes=len(imported.notes), origin=origin)]
        if imported.tempo_changes:
            message.append(
                i18n.tr("{notes} tempo changes; the grid takes the first tempo", notes=imported.tempo_changes)
            )
        if imported.dropped:
            message.append(i18n.tr("{notes} note events left out", notes=imported.dropped))
        if imported.left_out:
            numbers = " ".join(str(channel + 1) for channel in imported.left_out)
            message.append(i18n.tr("channels {numbers} left out", numbers=numbers))
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
        problems = self._lyric_problems()
        if problems:
            self.statusBar().showMessage(i18n.tr("Lyrics and notes do not line up: {problem}", problem=problems[0]))
            return False
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
            self.statusBar().showMessage(i18n.tr("MIDI file could not be written: {error}", error=error))
            return False
        self._write_lyrics()
        self.statusBar().showMessage(i18n.tr("Exported {name} — {notes} notes", name=Path(path).name, notes=len(notes)))
        return True

    def _lyric_problems(self) -> list[str]:
        """Why the aligned morae do not sit on the notes yet; empty when there is nothing to check."""
        lines = self.view.lyric_lines
        times = self.view.lyric_times
        if not lines or not any(span[0] is not None for row in times for span in row):
            return []
        return conflicts(list(lines), [list(row) for row in times], self.view.note_seconds())

    def _write_lyrics(self) -> None:
        """Put every mora's note count back into the `.krc` as its `.N`, once the conflicts are gone."""
        path = self.lyrics_path()
        lines = self.view.lyric_lines
        if path is None or not lines or not self.lyrics_text:
            return
        counts = note_counts(list(lines), [list(row) for row in self.view.lyric_times], self.view.note_seconds())
        try:
            text = with_counts(self.lyrics_text, counts)
            if text != self.lyrics_text:
                lyrics.save(path, text)
        except (KrcError, OSError) as error:
            self.statusBar().showMessage(str(error))
            return
        self.lyrics_text = text
        self._lyric_key = text_key(text)

    def _open_transcription(self) -> None:
        """Ask GAME for the singing voice's notes, over the audio the session is already listening to."""
        if self.audio_path is None:
            return
        active = self.view.active_channel
        occupied = any(note.channel == active for note in self.view.notes())
        dialog = TranscriptionDialog(
            self.audio_path,
            self.transport.bpm.value(),
            self,
            active_has_notes=occupied,
            offset=self.view.offset,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._adopt_transcription(dialog.notes(), dialog.target())

    def _adopt_transcription(self, notes, target: str) -> None:
        """Put a run's notes on a channel of their own, or over the active channel's own."""
        if not notes:
            self.statusBar().showMessage(i18n.tr("GAME found no notes in the loaded audio"))
            return
        beats = self.view.seconds_per_beat
        arriving = [
            (round(pitch), max(0.0, onset) / beats, max(0.0, offset - onset) / beats) for onset, offset, pitch in notes
        ]
        channels = list(self.view.channels)
        number = self.view.active_channel
        kept = [(note.pitch, note.start, note.duration, note.channel) for note in self.view.notes()]
        if target == "new":
            free = free_channel(channels)
            if free is None:
                self.statusBar().showMessage(
                    i18n.tr("All 16 channels are in use; the notes went to the active channel")
                )
            else:
                number = free
                channels.append(Channel(name="GAME", channel=number, program=53))
        else:
            kept = [note for note in kept if note[3] != number]
        self.view.replace(channels, kept + [(*note, number) for note in arriving], "Transcribe with GAME")
        self.statusBar().showMessage(
            i18n.tr("GAME found {notes} notes on channel {channel}", notes=len(arriving), channel=number + 1)
        )

    def lyrics_path(self) -> Path | None:
        """The sidecar the lyrics live in: `song.nto` keeps them in `song.krc` beside it."""
        return lyrics.path_for(self.project_path) if self.project_path is not None else None

    def _watch_lyrics(self) -> None:
        """Point the lyrics at the open project, taking what is already there as the known text."""
        path = self.lyrics_path()
        self.edit.lyrics.setEnabled(path is not None)
        self.edit.align.setEnabled(path is not None)
        self.lyrics_text = lyrics.load(path) if path is not None else ""
        self.lyrics_watcher.watch(path)
        self._load_mora()

    def _group_morae(self, row: int, first: int, last: int) -> None:
        """Fold a run of morae into one word of the `.krc`, and their times into the span they shared.

        The window that offered it is the strip's menu; a run that cannot be read the same once it is
        one word - a ruby, or a sokuon left leaning on nothing - is refused rather than written.
        """
        path = self.lyrics_path()
        if path is None or not self.lyrics_text:
            return
        try:
            text, times = group_morae(self.lyrics_text, self.view.lyric_times, row, first, last)
        except KrcError as error:
            self.statusBar().showMessage(i18n.tr("The morae could not be made one word: {error}", error=error))
            return
        lyrics.save(path, text)
        self.lyrics_text = text
        self._lyric_key = text_key(text)
        self.view.load_lyrics(mora_lines(text), times)
        self._stored_lyrics = project.LyricTimes(
            key=self._lyric_key, model=self._lyric_model, lines=self.view.lyric_times
        )
        self.statusBar().showMessage(i18n.tr("Grouped {count} morae into one word", count=last - first + 1))

    def _load_mora(self) -> None:
        """Derive the morae of the open `.krc` and give them the times the project kept for them."""
        text = self.lyrics_text
        key = text_key(text) if text else ""
        if key == self._lyric_key and bool(self.view.lyric_lines) == bool(text):
            return
        self._lyric_key = key
        self._lyric_model = ""
        lines = []
        if text:
            try:
                lines = mora_lines(text)
            except KrcError as error:
                self._lyric_error = str(error)
                self.statusBar().showMessage(str(error))
                lines = []
        else:
            self._lyric_error = ""
        stored = self._stored_lyrics
        if lines and stored is not None and stored.key == key and len(stored.lines) == len(lines):
            self._lyric_model = stored.model
            times = [list(row) for row in stored.lines]
        else:
            times = [[(None, None)] * len(line.morae) for line in lines]
        self.view.load_lyrics(lines, times)
        self.mora_strip.setVisible(any(span[0] is not None for row in times for span in row))

    def _open_lyrics(self) -> None:
        """Read a text into a `.krc` with a model, or by pasting what a web model answered."""
        path = self.lyrics_path()
        if path is None:
            return
        dialog = LyricsDialog(path, self.settings.lyrics, parent=self)
        dialog.saved.connect(self._on_lyrics_saved)
        dialog.open_requested.connect(self._open_lyrics_editor)
        dialog.exec()

    def _on_lyrics_saved(self, text: str) -> None:
        """A write of our own, so the watcher's next event does not read it back as a change."""
        self.lyrics_text = text
        self._load_mora()

    def _on_lyrics_file_changed(self) -> None:
        path = self.lyrics_path()
        if path is None:
            return
        text = lyrics.load(path)
        if text == self.lyrics_text:
            return  # our own save, or a change to another file in the project's folder
        self.lyrics_text = text
        self.statusBar().showMessage(i18n.tr("Lyrics reloaded from {name}", name=path.name))
        self._load_mora()

    def _open_lyrics_editor(self) -> None:
        path = self.lyrics_path()
        if path is None:
            return
        command = lyrics.editor_command(self.settings.lyrics.editor)
        QProcess.startDetached(command[0], [*command[1:], str(path)])

    def _open_align(self) -> None:
        """Ask the forced aligner for a time on every mora of the open `.krc`."""
        path = self.lyrics_path()
        if path is None:
            self.statusBar().showMessage(i18n.tr("Save the project first: the lyrics live in a .krc beside it"))
            return
        if not self.lyrics_text:
            self.statusBar().showMessage(i18n.tr("There are no lyrics in {name} to align", name=path.name))
            return
        if not self.view.lyric_lines:
            self.statusBar().showMessage(
                i18n.tr("The lyrics could not be read: {error}", error=self._lyric_error or path.name)
            )
            return
        if self.audio_path is None:
            self.statusBar().showMessage(i18n.tr("Load the audio before aligning the lyrics"))
            return
        dialog = AlignDialog(self.audio_path, self.lyrics_text, self.view.bpm, self, offset=self.view.offset)
        dialog.aligned.connect(self._adopt_alignment)
        dialog.exec()

    def _adopt_alignment(self, times, model: str) -> None:
        lines = self.view.lyric_lines
        if not lines or len(times) != len(lines):
            return
        self.view.set_lyrics(lines, times)
        self._lyric_key = text_key(self.lyrics_text)
        self._lyric_model = model
        self.mora_strip.setVisible(True)
        self.statusBar().showMessage(i18n.tr("Aligned {lines} lines", lines=len(lines)))

    def _open_audio(self, target: Path | None) -> str:
        """Load the audio a project names, or say why there is none: its notes are worth having either way."""
        if target is not None and target.exists():
            self.load_audio(str(target))
            return ""
        self._clear_audio()
        return i18n.tr(" (audio not found: {path})", path=target) if target is not None else ""

    def _clear_audio(self) -> None:
        """Forget the analysed file, for a project that names one this machine does not have."""
        self.position_timer.stop()
        self.audio_path = None
        self.view.set_spectrum(None)
        self.song.unload()
        self.player.stop()
        self.transport.detect.setEnabled(False)
        self.edit.transcribe.setEnabled(False)
        self.transport.suggestion.hide()
        self._show_position()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            self._autosave()

    def closeEvent(self, event) -> None:
        if not self._confirm_discard():
            event.ignore()
            return
        self.autosave_timer.stop()
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
            # another song brings its own tempo and offset; a re-analysis of the same one does not
            self.transport.bpm.setValue(store.FIELD_SPECS[("tempo", "bpm")].default)
            self.transport.latency.setValue(store.FIELD_SPECS[("playback", "latency_ms")].default)
        self.audio_path = path
        self.edit.transcribe.setEnabled(True)
        self._mark_dirty()
        store.set_value(self.settings, "paths", "last_audio_dir", str(Path(path).parent))
        self.settings_store.touch()
        self.loader = SpectrumLoader(path, parent=self, **self._analysis_options())
        self.loader.progress.connect(self._on_analysis_progress)
        self.loader.loaded.connect(self._on_spectrum_loaded)
        self.loader.failed.connect(
            lambda message: self.statusBar().showMessage(i18n.tr("Spectrum failed: {error}", error=message))
        )
        self.statusBar().showMessage(i18n.tr("Analysing {path} …", path=path))
        self.loader.start()
        self.song_loader = SongLoader(path, parent=self)
        self.song_loader.loaded.connect(self._on_song_loaded)
        self.song_loader.failed.connect(
            lambda message: self.statusBar().showMessage(i18n.tr("Playback failed: {error}", error=message))
        )
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
        self.statusBar().showMessage(i18n.tr("Drawing into {channel}", channel=channel.label), 2000)

    def _start_tempo(self, manual: bool = False) -> None:
        """Estimate the tempo of the loaded audio in the background, as a suggestion only.

        A run the user asked for offers its result whatever the BPM field holds; one that follows an
        audio load stays quiet over a tempo of the user's own.
        """
        if self.audio_path is None:
            return
        self._tempo_manual = manual
        self.transport.suggestion.hide()
        self.transport.detect.setEnabled(False)
        tempo = self.settings.tempo
        self.tempo_loader = TempoLoader(
            self.audio_path,
            algorithm=tempo.estimator,
            window_seconds=tempo.window_seconds,
            window_hop_seconds=tempo.window_hop_seconds,
            parent=self,
        )
        self.tempo_loader.loaded.connect(self._on_tempo_loaded)
        self.tempo_loader.failed.connect(self._on_tempo_failed)
        self.tempo_loader.start()

    def _on_tempo_loaded(self, result) -> None:
        """Offer what was estimated as a candidate, unless the field already holds that tempo."""
        self.transport.detect.setEnabled(True)
        manual, self._tempo_manual = self._tempo_manual, False
        if not result.bpm:
            return
        if not manual and self.transport.bpm.value() != store.FIELD_SPECS[("tempo", "bpm")].default:
            return  # a tempo the user set, or took from an estimate, is not one to suggest over
        if round(result.bpm) == round(self.transport.bpm.value()):
            return  # the balloon would read what the field already says
        self.transport.suggestion.estimate(result.bpm, result.agreement, result.windows, result.source, result.residual)
        self.transport.suggestion.show_under(self.transport.bpm)

    def _on_tempo_failed(self, message: str) -> None:
        self.transport.detect.setEnabled(self.audio_path is not None)
        self.statusBar().showMessage(i18n.tr("Tempo estimation failed: {error}", error=message))

    def _apply_tempo(self, bpm: float) -> None:
        self.transport.suggestion.hide()
        self.transport.bpm.setValue(bpm)
        self.statusBar().showMessage(i18n.tr("Tempo set to {bpm} BPM from the audio", bpm=f"{bpm:.0f}"))

    def _play(self) -> None:
        """Send the notes to the synth and start the audio file, both from where the cursor sits."""
        notes, _channels = self._program()
        if not notes and not self.song.is_loaded:
            self.statusBar().showMessage(i18n.tr("Nothing to play: load a file or draw some notes"))
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
            self.statusBar().showMessage(i18n.tr("{player} did not accept the notes", player=self.player_name))

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
        seconds = self._position()
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
            i18n.tr(
                "space: play or pause  |  click (outside edit mode): move the playhead  |  "
                "pen: drag an empty row to draw  |  select: drag a box, ctrl-click to add  |  "
                "shift drag a note: trim its start (left half) or end (right half)  |  "
                "right click: move to a channel  |  "
                "ctrl C: copy the selection, ctrl V: paste it at the playhead  |  "
                "ctrl Z: undo, ctrl shift Z: redo  |  "
                "middle drag: pan  |  ctrl wheel: zoom x, ctrl shift wheel: zoom y  |  gear: settings"
            )
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

    def _on_latency_changed(self, *_args) -> None:
        """The latency only slides the drawn grid; the playhead and the notes keep their timestamps."""
        self.view.set_offset(self.transport.latency.value() / 1000.0)

    def _on_spectrum_parameters(self, *_args) -> None:
        self.view.gain = self.mix.gain.value()
        self.view.contrast = self.mix.contrast.value()
        self.view.refresh()

    def _on_analysis_progress(self, done: int, total: int) -> None:
        self.statusBar().showMessage(i18n.tr("Analysing … {percent}%", percent=done * 100 // max(1, total)))

    def _on_spectrum_loaded(self, spectrum: NoteSpectrum) -> None:
        self.view.set_spectrum(spectrum)
        self.statusBar().showMessage(
            i18n.tr(
                "{frames} frames x {bands} bands, {ms} ms/frame, {seconds} s",
                frames=spectrum.frames,
                bands=spectrum.table.shape[1],
                ms=f"{spectrum.frame_ms:g}",
                seconds=f"{spectrum.duration:.1f}",
            )
        )

    def _update_status(self) -> None:
        self.setWindowTitle(
            i18n.tr(
                "{document} — Namioto — {notes} notes",
                document=self._document_name(),
                notes=len(self.view.notes()),
            )
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="namioto", description="Namioto piano-roll MIDI editor")
    parser.add_argument("audio", nargs="?", help="audio file to analyse, or a .nto project or a .mid file to open")
    parser.add_argument("--channels", choices=CHANNEL_MODES, help="which channels to analyse, over the settings")
    parser.add_argument("--t-num", type=float, help="spectrum frames per second, over the settings")
    parser.add_argument("--gain", type=float, help="spectrum gain for this run")
    parser.add_argument("--contrast", type=float, help="spectrum contrast for this run")
    return parser.parse_args(argv)


def main() -> int:
    multiprocessing.freeze_support()  # a frozen build has to hand the child back the same bootstrap
    args = parse_args()
    app = QApplication(sys.argv)
    window = MainWindow(overrides={"channels": args.channels, "t_num": args.t_num})
    window.apply_overrides(gain=args.gain, contrast=args.contrast)
    window.show()
    if args.audio is not None:
        # a file waits for the window. Naming a project opens the platform's file chooser, and on
        # Linux that is the xdg-desktop-portal one, which is only ready once the event loop has run -
        # asking before that falls back to Qt's own dialog.
        QTimer.singleShot(0, lambda: window.open_file(args.audio))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
