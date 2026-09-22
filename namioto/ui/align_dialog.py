# SPDX-License-Identifier: AGPL-3.0-only
"""The alignment window: put a time on every mora by forcing the lyrics onto the audio.

The run is a `LoadingThread` of its own (`Aligner`), because the model is an ONNX graph and the
audio is read once; the window only starts it, shows the progress, and shows the failures
`namioto.align` reports. The whole stream is aligned in one pass, the way FA-Kara does it.
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from namioto import align
from namioto.i18n import tr
from namioto.karaoke import align_tokens, mora_lines, snap_to_beats, split
from namioto.ui.loading import LoadingThread

LOG_HEIGHT = 120
# the cells per quarter note, the way GAME's Quantize counts them: a label is the note the cell is
QUANTIZE = ((0, "Off"), (1, "1/4"), (2, "1/8"), (4, "1/16"), (8, "1/32"), (16, "1/64"), (32, "1/128"))


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
        self.aligned.emit((split(found.tokens, lines), self.model, self._problems(found, lines)))

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

    def __init__(self, audio: str, text: str, tempo: float, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Align lyrics"))
        self.audio = audio
        self.text = text
        self.tempo = tempo
        self._thread: Aligner | None = None

        self.model = QComboBox()
        for name in align.MODELS:
            self.model.addItem(name, name)
        self.model.setToolTip(tr("Which forced-alignment model to use"))
        self.provider = QComboBox()
        for name in align.PROVIDERS:
            self.provider.addItem(name, name)
        self.provider.setToolTip(tr("Where the model runs"))
        self.snap = QComboBox()
        for cells, label in QUANTIZE:
            self.snap.addItem(label, cells)
        self.snap.setToolTip(tr("Snap the times to the beat grid, this many cells per quarter note"))

        form = QFormLayout()
        form.addRow(tr("Model"), self.model)
        form.addRow(tr("Device"), self.provider)
        form.addRow(tr("Quantize"), self.snap)
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
        layout.addLayout(form)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addWidget(self.log)
        layout.addWidget(buttons)

    def _start(self) -> None:
        if self._thread is not None:
            return
        self.log.clear()
        self.run.setEnabled(False)
        self.progress.setRange(0, 0)  # one busy bar: the whole song is one pass, so nothing ticks yet
        self.status.setText(tr("Aligning…"))
        self._thread = Aligner(self.audio, self.text, self.model.currentData(), self.provider.currentData(), self)
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
        cells = self.snap.currentData()
        if cells:
            times = snap_to_beats(times, self.tempo, 1.0 / cells)
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
        if self._thread is not None:
            self._thread.wait()
        super().reject()
