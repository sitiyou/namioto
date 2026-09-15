# SPDX-License-Identifier: AGPL-3.0-only
"""The transcription window: GAME's parameters, the run's progress and log, and what it found.

The run lives in a process of its own (`namioto.transcription.transcribe`), so a model crash cannot
take the editor down; this window only spawns it, drains its queue and offers the result.
"""

from __future__ import annotations

import multiprocessing
import pathlib
import queue
import shutil
from collections.abc import Callable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from namioto import transcription
from namioto.settings import Field
from namioto.ui.settings_dialog import field_editor

POLL_MS = 100
FIELD_WIDTH = 300
LOG_HEIGHT = 140


def start_job(audio: str, parameters: dict, tempo: float):
    """Spawn the GAME child and the queue it reports on; the one seam the tests replace."""
    context = multiprocessing.get_context("spawn")
    channel = context.Queue()
    process = context.Process(
        target=transcription.transcribe,
        args=(audio, parameters, tempo, channel),
        daemon=True,
    )
    process.start()
    return process, channel


class TranscriptionDialog(QDialog):
    """GAME's parameters and the run, over one audio file.

    `accept()` means the caller takes `notes()` and puts them where `target()` says.
    """

    def __init__(self, audio: str, tempo: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Transcribe the singing voice with GAME")
        self.resize(600, 640)
        self.audio = str(audio)
        self.tempo = float(tempo)
        self._parameters = transcription.load_parameters()
        self._fields: list[tuple[Field, Callable[[], object], Callable[[object], None]]] = []
        self._process = None
        self._queue = None
        self._notes: list[tuple[float, float, float]] = []
        self._settled = True
        self._downloading = False
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)

        self.form = self._build_form()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress_label = QLabel("Ready")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(LOG_HEIGHT)
        self.run_button = QPushButton("Transcribe")
        self.run_button.clicked.connect(self._start)
        self.insert_button = QPushButton("Insert")
        self.insert_button.setEnabled(False)
        self.insert_button.clicked.connect(self.accept)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addWidget(self.progress, 1)
        buttons.addWidget(self.progress_label)
        buttons.addWidget(self.run_button)
        buttons.addWidget(self.insert_button)
        buttons.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.form)
        layout.addWidget(self.log, 1)
        layout.addLayout(buttons)

    def parameters(self) -> dict:
        """What the form holds now, checked the way the store checks it."""
        return transcription.coerce_parameters({field.name: read() for field, read, _write in self._fields})

    def target(self) -> str:
        """Where to put the notes; read when they are taken, so the choice stays the user's."""
        return str(self.parameters()["target"])

    def notes(self) -> list[tuple[float, float, float]]:
        return list(self._notes)

    def _build_form(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        for advanced in (False, True):
            if advanced:
                layout.addWidget(self._advanced_caption())
            form = QFormLayout()
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            for item in (field for field in transcription.PARAMETERS if field.advanced is advanced):
                editor, read, write = field_editor(self._parameters[item.name], item)
                editor.setMaximumWidth(FIELD_WIDTH)
                label = QLabel(item.caption)
                if item.tooltip:
                    label.setToolTip(item.tooltip)
                    editor.setToolTip(item.tooltip)
                form.addRow(label, editor)
                self._fields.append((item, read, write))
            layout.addLayout(form)
        layout.addStretch(1)
        return widget

    def _advanced_caption(self) -> QLabel:
        caption = QLabel("ADVANCED")
        font = QFont()
        font.setPixelSize(10)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
        caption.setFont(font)
        return caption

    def _start(self) -> None:
        self._parameters = self.parameters()
        transcription.save_parameters(self._parameters)
        cached = transcription.find_run(self.audio, self._parameters, self.tempo)
        if cached is not None and self._use_cache(len(cached)):
            self._log_line(f"saved run reused: {len(cached)} notes")
            self._settle(cached, save=False)
            return
        self._launch()

    def _use_cache(self, count: int) -> bool:
        """Ask before a repeat run is skipped; the notes are never listed anywhere, only offered back."""
        answer = QMessageBox.question(
            self,
            "Transcribe with GAME",
            f"A run with these exact parameters already found {count} notes.\n"
            "Use it instead of running the model again?",
        )
        return answer == QMessageBox.StandardButton.Yes

    def _launch(self) -> None:
        self.log.clear()
        self._log_line(f"Transcribing {pathlib.Path(self.audio).name} …")
        self._notes = []
        self._settled = False
        self._downloading = False
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress_label.setText("Starting GAME …")
        self._set_running(True)
        try:
            self._process, self._queue = start_job(self.audio, self._parameters, self.tempo)
        except Exception as error:  # a child that cannot start must not leave the form stuck
            self._fail(f"{type(error).__name__}: {error}")
            return
        self._timer.start()

    def _poll(self) -> None:
        self._drain()
        process = self._process
        if process is None or self._settled or process.is_alive():
            return
        self._drain()  # the last messages can land just after the child exits
        self._timer.stop()
        self._process = None
        self._queue = None
        if not self._settled:
            self._crash(process.exitcode)

    def _drain(self) -> None:
        while self._queue is not None:
            try:
                message = self._queue.get_nowait()
            except queue.Empty:
                return
            self._handle(message)

    def _handle(self, message) -> None:
        kind = message[0]
        if kind == "log":
            self._log_line(message[1])
        elif kind == "progress":
            self._show_progress(*message[1:])
        elif kind == "done":
            self._settle(list(message[1]), save=True)
        elif kind == "error":
            self._fail(message[1])

    def _show_progress(self, stage: str, done: int, total: int) -> None:
        if stage == "download":
            self._downloading = True
            where = f" of {total // (1 << 20)}" if total else ""
            self.progress_label.setText(f"Downloading the model … {done // (1 << 20)}{where} MB")
        else:
            self.progress_label.setText(f"Extracting … {done}/{total}")
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)

    def _settle(self, notes, save: bool) -> None:
        self._settled = True
        self._timer.stop()
        self._process = None
        self._queue = None
        self._notes = [(float(onset), float(offset), float(pitch)) for onset, offset, pitch in notes]
        if save:
            try:
                transcription.save_run(self.audio, self._parameters, self.tempo, self._notes)
            except OSError as error:
                self._log_line(f"the result could not be saved: {error}")
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.progress_label.setText(f"{len(self._notes)} notes")
        self._log_line(f"{len(self._notes)} notes")
        self._set_running(False)
        self.insert_button.setEnabled(bool(self._notes))
        self.insert_button.setDefault(True)

    def _fail(self, message: str) -> None:
        self._settled = True
        self._timer.stop()
        self._process = None
        self._queue = None
        for line in str(message).strip().splitlines()[-8:]:
            self._log_line(line)
        self.progress.setValue(0)
        self.progress_label.setText("Failed")
        self._set_running(False)

    def _crash(self, exitcode) -> None:
        self._log_line(f"GAME stopped unexpectedly (exit code {exitcode})")
        self.progress.setValue(0)
        self.progress_label.setText("Failed")
        self._set_running(False)

    def _set_running(self, running: bool) -> None:
        self.form.setEnabled(not running)
        self.run_button.setEnabled(not running)
        self.run_button.setText("Transcribing …" if running else "Transcribe")
        self.insert_button.setEnabled(False if running else bool(self._notes))

    def _log_line(self, text: str) -> None:
        self.log.appendPlainText(text)

    def _stop(self) -> None:
        self._timer.stop()
        process, self._process = self._process, None
        self._queue = None
        if process is not None and process.is_alive():
            process.terminate()
            process.join(2000)
            self._sweep()

    def _sweep(self) -> None:
        """A killed download leaves its archive in the model directory; it is worth no keeping."""
        if not self._downloading:
            return
        from namioto import game

        for stale in game.models_root().glob("tmp*"):
            if stale.is_dir():
                shutil.rmtree(stale, ignore_errors=True)
            else:
                stale.unlink(missing_ok=True)

    def reject(self) -> None:
        self._stop()
        super().reject()

    def closeEvent(self, event) -> None:
        self._stop()
        super().closeEvent(event)
