# SPDX-License-Identifier: AGPL-3.0-only
"""The alignment window: put a time on every mora by forcing the lyrics onto the audio.

The run is a `LoadingThread` of its own (`Aligner`), because the model is an ONNX graph and the
audio is read once; the window only starts it, shows the progress, and shows the failures
`namioto.analysis.align` reports. The whole stream is aligned in one pass, the way FA-Kara does it. Its
choices are `align.PARAMETERS`, remembered between runs in the file `align.parameter_path()` names.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from namioto.analysis import align
from namioto.i18n import tr
from namioto.karaoke import align_tokens, mora_lines, snap_to_beats, split
from namioto.settings import Field
from namioto.ui.loading import LoadingThread
from namioto.ui.settings_dialog import add_row, field_editor

LOG_HEIGHT = 120


class Aligner(LoadingThread):
    """One pass of the forced aligner over the whole audio: the times, and the lines it doubts."""

    progress = pyqtSignal(int, int)
    aligned = pyqtSignal(object)

    def __init__(self, audio: str, text: str, model: str = align.DEFAULT_MODEL, provider: str = "cpu", parent=None):
        super().__init__(audio, parent)
        self.text = text
        self.model = model
        self.provider = provider

    def load(self) -> None:
        directory = align.resolve_model(None, self.model, "ja")
        backend = align.OnnxBackend(directory / align.MODEL_FILE, provider=self.provider)
        dictionary, blank_id = align.load_dictionary(directory / align.VOCAB_FILE)
        lines = mora_lines(self.text)
        audio = align.load_audio(self.path)
        segment = align.Segment(0.0, len(audio) / align.SAMPLE_RATE, tuple(align_tokens(lines)))
        found = align.align([segment], backend, dictionary, audio, blank_id=blank_id, progress=self.progress.emit)[0]
        rows = align.correct_times(split(found.tokens, lines), audio)
        problems = self._problems(found, lines)
        with suppress(OSError):  # the cache is disposable, and the alignment itself already came back
            align.save_alignment(self.path, self.model, self.provider, self.text, rows, problems)
        self.aligned.emit((rows, self.model, problems))

    @staticmethod
    def _problems(found, lines) -> list[str]:
        reported = []
        at = 0
        for line in lines:
            chunk = found.tokens[at : at + len(line.morae)]
            at += len(chunk)
            trouble = align.problems(align.AlignedSegment(found.start, found.end, tuple(chunk)))
            if trouble:
                reported.append(f"{line.text}: {', '.join(trouble)}")
        return reported


class AlignDialog(QDialog):
    """The model choice, the run, and what it found. The result is handed over as it arrives."""

    aligned = pyqtSignal(object, str)

    def __init__(self, audio: str, text: str, tempo: float, parent=None, offset: float = 0.0):
        super().__init__(parent)
        self.setWindowTitle(tr("Align lyrics"))
        self.audio = audio
        self.text = text
        self.tempo = tempo
        self.offset = offset
        self._thread: Aligner | None = None
        # the choices the last run was made with, straight onto the form
        self._parameters = align.load_parameters()
        self._fields: list[tuple[Field, Callable[[], object], Callable[[object], None]]] = []

        form = self._build_form()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.status = QLabel(tr("Ready"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(LOG_HEIGHT)
        self.run = QPushButton(tr("Run"))
        self.run.clicked.connect(self._start)
        buttons = QDialogButtonBox()
        buttons.addButton(self.run, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(form)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addWidget(self.log)
        layout.addWidget(buttons)

    def parameters(self) -> dict:
        """What the form holds now, checked the way the store checks it."""
        return align.coerce_parameters({field.name: read() for field, read, _write in self._fields})

    def _build_form(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)
        form.setContentsMargins(0, 0, 0, 0)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for item in align.PARAMETERS:
            editor, read, write = field_editor(self._parameters[item.name], item)
            add_row(form, editor, item)
            self._fields.append((item, read, write))
        return widget

    def _remember(self) -> None:
        """Keep what the form holds, so the next window opens on it."""
        self._parameters = self.parameters()
        align.save_parameters(self._parameters)

    def _start(self) -> None:
        if self._thread is not None:
            return
        self.log.clear()
        self.run.setEnabled(False)
        self._remember()
        model = self._parameters["model"]
        provider = self._parameters["provider"]
        cached = align.find_alignment(self.audio, model, provider, self.text)
        if cached is not None:
            rows, problems = cached
            self._done((rows, model, problems))  # re-snapped with the tempo and grid offset in hand
            self.log.setPlainText("\n".join([tr("saved alignment reused"), *problems]))
            return
        self.progress.setRange(0, 0)  # one busy bar: the whole song is one pass, so nothing ticks yet
        self.status.setText(tr("Aligning…"))
        self._thread = Aligner(self.audio, self.text, model, provider, self)
        self._thread.progress.connect(self._show_progress)
        self._thread.aligned.connect(self._done)
        self._thread.failed.connect(self._fail)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _show_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)

    def _done(self, result) -> None:
        times, model, problems = result
        cells = self.parameters()["quantize"]  # read now, so a change made while the run went counts
        if cells:
            times = snap_to_beats(times, self.tempo, 1.0 / cells, self.offset)
        self._thread = None
        self.run.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.status.setText(tr("Aligned {lines} lines", lines=len(times)))
        self.log.setPlainText("\n".join(problems))
        self.aligned.emit(times, model)

    def _fail(self, message: str) -> None:
        self._thread = None
        self.run.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status.setText(tr("Alignment failed"))
        self.log.setPlainText(message)

    def reject(self) -> None:
        self._remember()
        if self._thread is not None:
            self._thread.wait()
        super().reject()
