# SPDX-License-Identifier: AGPL-3.0-only
"""The lyrics window: the source text, the `.krc` it becomes, and the model call in between.

A request runs off the GUI thread (`LyricsTranslator`), and `LyricsWatcher` follows the sidecar file
so one edited outside the editor is seen.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QFileSystemWatcher, QObject, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from namioto import lyrics
from namioto.i18n import tr
from namioto.ui.loading import LoadingThread

LOAD_HEIGHT = 180
LOG_HEIGHT = 120


class LyricsTranslator(LoadingThread):
    """One request to the model, off the GUI thread; `LoadingThread` reports whatever it throws.

    The endpoint is asked to stream, so `delta` carries the model's own output while it comes - the
    reasoning first, then the answer - and `translated` still carries the answer alone at the end.
    """

    translated = pyqtSignal(str)
    delta = pyqtSignal(str, str)

    def __init__(self, source: str, config, path, parent=None):
        super().__init__(path, parent)
        self.source = source
        self.config = config

    def load(self) -> None:
        self.translated.emit(
            lyrics.translate(
                self.source,
                base_url=self.config.api_base,
                api_key=self.config.api_key,
                model=self.config.model,
                temperature=self.config.temperature,
                timeout=self.config.timeout,
                stream=True,
                on_delta=lambda kind, text: self.delta.emit(kind, text),
            )
        )


class LyricsWatcher(QObject):
    """Follow a `.krc` and its folder, so a change made outside the editor is seen.

    A save replaces the file rather than writing it in place, which drops the watch Qt holds on the
    path, so every event puts the watches back; the text is not compared here but by the caller,
    which keeps our own write from reading back as somebody else's.
    """

    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._notify)
        self._watcher.directoryChanged.connect(self._notify)
        self._paths: tuple[str, ...] = ()

    def watch(self, path: str | Path | None) -> None:
        self._release()
        if path is None:
            return
        target = Path(path)
        self._paths = (str(target), str(target.parent))
        self._readd()

    def _release(self) -> None:
        if self._paths:
            self._watcher.removePaths(list(self._paths))
        self._paths = ()

    def _readd(self) -> None:
        self._watcher.addPaths([watch for watch in self._paths if Path(watch).exists()])

    def _notify(self, _path: str) -> None:
        self._readd()
        self.changed.emit()


class LyricsDialog(QDialog):
    """The source lyrics, the `.krc` beside the project, and the model that fills it in."""

    saved = pyqtSignal(str)
    open_requested = pyqtSignal()

    def __init__(self, path: str | Path, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Import lyrics"))
        self.resize(640, 560)
        self.path = Path(path)
        self.config = config
        self.translator: LyricsTranslator | None = None

        self.path_label = QLabel(str(self.path))
        self.path_label.setToolTip(tr("The lyrics file beside the project, written as .krc"))
        self.source = QPlainTextEdit()
        self.source.setPlaceholderText(tr("Paste the lyrics here"))
        self.source.setFixedHeight(LOAD_HEIGHT)
        self.load_button = QPushButton(tr("Load file…"))
        self.load_button.clicked.connect(self._load)
        self.result = QPlainTextEdit()
        self.result.setPlaceholderText(tr("The annotated lyrics, editable before saving"))
        self.result.setPlainText(lyrics.load(self.path))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(LOG_HEIGHT)
        self.log.setPlaceholderText(tr("The model's own output, streamed as it arrives"))
        self.log.hide()
        self._log_kind: str | None = None
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)

        self.configured = bool(config.api_base.strip() and config.api_key.strip() and config.model.strip())
        self.translate_button = QPushButton(tr("Translate with the API"))
        self.translate_button.setEnabled(self.configured)
        self.translate_button.setToolTip(
            tr("Ask the endpoint in the settings to annotate the text above")
            if self.configured
            else tr("Set the API base, key and model in the settings first")
        )
        self.translate_button.clicked.connect(self._translate)
        self.copy_button = QPushButton(tr("Copy prompt"))
        self.copy_button.setToolTip(tr("Put the prompt and the lyrics on the clipboard, for a web model"))
        self.copy_button.clicked.connect(self._copy_prompt)
        self.save_button = QPushButton(tr("Save"))
        self.save_button.setToolTip(tr("Write the result to the lyrics file"))
        self.save_button.clicked.connect(self._save)
        self.open_button = QPushButton(tr("Open in external editor"))
        self.open_button.setToolTip(tr("Open the lyrics file with the editor named in the settings"))
        self.open_button.clicked.connect(self.open_requested)
        self.close_button = QPushButton(tr("Close"))
        self.close_button.clicked.connect(self.reject)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel(tr("Source lyrics")))
        source_row.addStretch(1)
        source_row.addWidget(self.load_button)
        buttons = QHBoxLayout()
        buttons.addWidget(self.translate_button)
        buttons.addWidget(self.copy_button)
        buttons.addStretch(1)
        for button in (self.save_button, self.open_button, self.close_button):
            buttons.addWidget(button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.path_label)
        layout.addLayout(source_row)
        layout.addWidget(self.source, 1)
        layout.addWidget(QLabel(tr("Result (.krc)")))
        layout.addWidget(self.result, 1)
        layout.addWidget(self.log)
        layout.addWidget(self.status_label)
        layout.addLayout(buttons)

    def _load(self) -> None:
        chosen, _filter = QFileDialog.getOpenFileName(
            self,
            tr("Load lyrics"),
            str(self.path.parent),
            tr("Lyrics file ({patterns})", patterns="*.txt *.md *.lrc") + ";;" + tr("All files (*)"),
        )
        if not chosen:
            return
        text = lyrics.load(chosen)
        if not text:
            self.status_label.setText(tr("That file could not be read"))
            return
        self.source.setPlainText(text)
        self.status_label.setText(tr("Loaded {name}", name=Path(chosen).name))

    def _copy_prompt(self) -> None:
        QGuiApplication.clipboard().setText(lyrics.build_prompt(self.source.toPlainText()))
        self.status_label.setText(tr("Prompt copied: paste it into a web model, then paste its answer below and save"))

    def _translate(self) -> None:
        source = self.source.toPlainText().strip()
        if not source:
            self.status_label.setText(tr("Paste the lyrics to annotate first"))
            return
        self._set_running(True)
        self.log.clear()
        self.log.show()
        self._log_kind = None
        translator = LyricsTranslator(source, self.config, self.path, parent=self)
        self.translator = translator
        translator.translated.connect(self._translated)
        translator.delta.connect(self._on_delta)
        translator.failed.connect(self._failed)
        translator.start()

    def _on_delta(self, kind: str, text: str) -> None:
        """Show the model's own stream, starting the answer on a line of its own."""
        if self._log_kind is not None and kind != self._log_kind:
            self.log.appendPlainText("")
        self._log_kind = kind
        cursor = self.log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.log.setTextCursor(cursor)

    def _translated(self, text: str) -> None:
        self.result.setPlainText(text)
        self._set_running(False)
        self.status_label.setText(tr("Translated: check it over, then save"))

    def _failed(self, message: str) -> None:
        self._set_running(False)
        self.status_label.setText(tr("Translation failed: {error}", error=message))

    def _set_running(self, running: bool) -> None:
        self.translate_button.setEnabled(self.configured and not running)
        self.translate_button.setText(tr("Translating …") if running else tr("Translate with the API"))

    def _save(self) -> None:
        text = self.result.toPlainText()
        try:
            lyrics.save(self.path, text)
        except OSError as error:
            self.status_label.setText(tr("Could not save: {error}", error=error))
            return
        self.status_label.setText(tr("Saved to {name}", name=self.path.name))
        self.saved.emit(text)

    def _detach(self) -> None:
        """A request already on its way cannot be recalled, so it is left to finish unobserved."""
        translator, self.translator = self.translator, None
        if translator is None:
            return
        translator.blockSignals(True)
        if translator.isRunning():
            translator.setParent(QApplication.instance())

    def reject(self) -> None:
        self._detach()
        super().reject()

    def closeEvent(self, event) -> None:
        self._detach()
        super().closeEvent(event)
