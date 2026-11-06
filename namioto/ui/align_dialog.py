# SPDX-License-Identifier: AGPL-3.0-only
"""The alignment window: put a time on every sound by forcing the lyrics onto the audio.

The run is a `LoadingThread` of its own (`Aligner`), because the model is an ONNX graph and the
audio is read once; the window only starts it, shows the progress, and shows the failures
`namioto.analysis.align` reports. Encoder inference may be chunked; its concatenated emissions are
aligned as one global CTC path because KRC lines have no timestamps. Its choices are
`align.PARAMETERS`, remembered between runs in the file `align.parameter_path()` names.
Its Quantize choice (Off / 1/4 / 1/8 / ..., GAME's list) snaps onsets onto the drawn beat grid
before mapping, preserving the reference durations captured from the alignment.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import suppress

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
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

from namioto.analysis import align, devices
from namioto.i18n import tr
from namioto.karaoke.sounds import natural_sounds, natural_tokens, split_tokens
from namioto.lyricmap import from_spans, snap_to_beats
from namioto.lyricmap.channels import split as split_channels
from namioto.params import Field
from namioto.ui.form import add_row, field_editor
from namioto.ui.loading import LoadingThread

LOG_HEIGHT = 120


class Aligner(LoadingThread):
    """One pass of the forced aligner over the whole audio."""

    progress = Signal(int, int)
    aligned = Signal(object)
    message = Signal(str)

    def __init__(
        self,
        audio: str,
        text: str,
        model: str = align.DEFAULT_MODEL,
        provider: str = "cpu",
        chunk: str = align.DEFAULT_MODE,
        parent=None,
        *,
        channel: int | None = None,
        onset_priors=(),
    ):
        super().__init__(audio, parent)
        self.text = text
        self.channel = channel
        self.onset_priors = onset_priors
        self.cache_text = text if channel is None else f"# Align channel {channel}\n{text}"
        self.model = model
        self.provider = provider
        self.chunk = chunk
        self._downloaded = -10

    def load(self) -> None:
        started = time.monotonic()
        lines = natural_sounds(self.text) if self.channel is None else split_channels(self.text)[self.channel].lines
        sounds = sum(len(line.sounds) for line in lines)
        self.message.emit(tr("Read {lines} lines, {sounds} sounds", lines=len(lines), sounds=sounds))
        audio = align.load_audio(self.path)
        seconds = len(audio) / align.SAMPLE_RATE
        emission = align.load_emissions(self.path, self.model, self.provider, self.chunk)
        directory = align.resolve_model(None, self.model, "ja", progress=self._downloading)
        if emission is None:
            self.message.emit(
                tr("Loading the {model} model on {provider}\u2026", model=self.model, provider=self.provider)
            )
            align.fetch_fp16(directory, self.provider, self.model, progress=self._downloading)
            backend = align.OnnxBackend(align.model_file(directory, self.provider), provider=self.provider)
            if self.chunk == "off":
                self.message.emit(tr("Aligning over {seconds:.1f}s of audio in one pass\u2026", seconds=seconds))
            else:
                backend = align.ChunkedBackend(backend, silence=self.chunk == "silence", progress=self.progress.emit)
                self.message.emit(tr("Aligning over {seconds:.1f}s of audio in chunks\u2026", seconds=seconds))
            emission = align.whole_emissions(backend, audio)
            with suppress(OSError):
                align.save_emissions(self.path, self.model, self.provider, self.chunk, emission)
        else:
            self.message.emit(tr("Reusing the model's pass over the audio\u2026"))
        dictionary, blank_id = align.load_dictionary(directory / align.VOCAB_FILE)
        # Chunked inference yields one emission stream; KRC supplies no line-time windows.
        segment = align.Segment(0.0, seconds, tuple(natural_tokens(lines)))
        flattened_priors = [center for row in self.onset_priors for center in row]
        if len(flattened_priors) != len(segment.tokens):
            flattened_priors = [None] * len(segment.tokens)
        ctc_priors = [
            None if center is None else center - align.onset_offset(self.model, token)
            for token, center in zip(segment.tokens, flattened_priors, strict=True)
        ]
        found = align.align_whole(
            segment, emission, dictionary, blank_id=blank_id, onset_priors=ctc_priors
        )
        self.message.emit(tr("Fitting the sounds to the voice\u2026"))
        rows = align.correct_times(split_tokens(found.tokens, lines), audio)
        rows = align.calibrate_times(rows, found.tokens, self.model)
        score_rows = []
        onset_rows = []
        at = 0
        for line_index, line in enumerate(lines):
            line_tokens = found.tokens[at : at + len(line.sounds)]
            score_rows.append([token.score for token in line_tokens])
            onset_row = []
            for sound_index, token in enumerate(line_tokens):
                corrected_start = rows[line_index][sound_index][0]
                if token.onset_interval is None or token.start is None or corrected_start is None:
                    onset_row.append(None)
                else:
                    shift = corrected_start - token.start
                    low, high = token.onset_interval
                    onset_row.append((round(low + shift, 3), round(high + shift, 3)))
            onset_rows.append(onset_row)
            at += len(line.sounds)
        with suppress(OSError):  # the cache is disposable, and the alignment itself already came back
            align.save_alignment(
                self.path,
                self.model,
                self.provider,
                self.cache_text,
                rows,
                [],
                [],
                self.chunk,
                scores=score_rows,
                onset_bounds=onset_rows,
                onset_priors=self.onset_priors,
            )
        self.message.emit(tr("Alignment took {seconds:.1f}s", seconds=time.monotonic() - started))
        self.aligned.emit((rows, self.model, [], [], score_rows, onset_rows))

    def _downloading(self, done: int, total: int) -> None:
        """A download of a model that was not installed, logged a tenth at a time."""
        if not total:
            return
        percent = done * 100 // total
        if percent < self._downloaded:  # the half-precision package starts a second download
            self._downloaded = -10
        if percent >= self._downloaded + 10:
            self._downloaded = percent
            self.message.emit(tr("Downloading the {model} model\u2026 {percent}%", model=self.model, percent=percent))


class AlignDialog(QDialog):
    """The model choice, the run, and what it found. The result is handed over as it arrives."""

    aligned = Signal(object, str, object, object, object)

    def __init__(
        self,
        audio: str,
        text: str,
        tempo: float,
        parent=None,
        offset: float = 0.0,
        *,
        channel: int | None = None,
        onset_priors=(),
    ):
        super().__init__(parent)
        self.setWindowTitle(tr("Align lyrics"))
        self.audio = audio
        self.text = text
        self.channel = channel
        self.onset_priors = onset_priors
        self.cache_text = text if channel is None else f"# Align channel {channel}\n{text}"
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
        self.rebuild = QPushButton(tr("Rebuild mapping"))
        self.rebuild.clicked.connect(self._start)
        buttons = QDialogButtonBox()
        buttons.addButton(self.rebuild, QDialogButtonBox.ButtonRole.ActionRole)
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
        return align.coerce_parameters(
            {
                **{field.name: read() for field, read, _write in self._fields},
            }
        )

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
        problem = devices.validate()
        if problem is not None:
            self._fail(tr(problem))
            return
        self.log.clear()
        self.rebuild.setEnabled(False)
        self._remember()
        model = self._parameters["model"]
        provider = devices.resolve(self._parameters["device"])
        if self._parameters["device"] == "gpu" and provider == "cpu":
            self.log.appendPlainText(tr("No GPU backend is available; running on the CPU"))
        chunk = self._parameters["chunk"]
        cached = align.find_alignment(
            self.audio, model, provider, self.cache_text, chunk, onset_priors=self.onset_priors
        )
        if cached is not None:
            rows, _problems, _flagged, scores, onset_bounds = cached
            self._done((rows, model, [], [], scores, onset_bounds))
            self.log.setPlainText(tr("saved alignment reused"))
            return
        self.progress.setRange(0, 0)  # busy until the first chunk of the model's pass comes back
        self.status.setText(tr("Aligning…"))
        self._thread = Aligner(
            self.audio,
            self.text,
            model,
            provider,
            chunk,
            self,
            channel=self.channel,
            onset_priors=self.onset_priors,
        )
        self._thread.progress.connect(self._show_progress)
        self._thread.message.connect(self.log.appendPlainText)
        self._thread.aligned.connect(self._done)
        self._thread.failed.connect(self._fail)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _show_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)

    def _done(self, result) -> None:
        spans, model, _problems, _flagged, *optional = result
        scores = optional[0] if optional else []
        onset_bounds = optional[1] if len(optional) > 1 else []
        times = from_spans(spans)
        cells = self.parameters()["quantize"]  # read now, so a change made while the run went counts
        if cells:
            quantized = snap_to_beats(times, self.tempo, 1.0 / cells, self.offset)
            if onset_bounds:
                onset_bounds = [
                    [
                        None
                        if bounds is None or before is None or after is None
                        else (
                            round(bounds[0] + after[0] - before[0], 3),
                            round(bounds[1] + after[0] - before[0], 3),
                        )
                        for before, after, bounds in zip(old_row, new_row, bound_row, strict=False)
                    ]
                    for old_row, new_row, bound_row in zip(times, quantized, onset_bounds, strict=False)
                ]
            times = quantized
        self._thread = None
        self.rebuild.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.status.setText(tr("Aligned {lines} lines", lines=len(times)))
        self.aligned.emit(times, model, (), scores, onset_bounds)

    def _fail(self, message: str) -> None:
        self._thread = None
        self.rebuild.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status.setText(tr("Alignment failed"))
        self.log.setPlainText(message)

    def reject(self) -> None:
        self._remember()
        if self._thread is not None:
            self._thread.wait()
        super().reject()
