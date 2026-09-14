# SPDX-License-Identifier: AGPL-3.0-only
"""Control bars: playback, edit, spectrum and mix, each grouped into captioned blocks."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QToolBar,
    QToolButton,
    QWidget,
)

from namioto.ui.roll import format_time

ICON_PX = 32
ICON_SIZE = 17
ICON_COLOR = "#cfd6e4"
BUTTON_HEIGHT = 24
FIELD_HEIGHT = 24
SUGGESTION_COLOR = "#cfd6e4"
WEAK_COLOR = "#7f8b9e"
WEAK_AGREEMENT = 0.5


def _icon(kind: str) -> QIcon:
    pixmap = QPixmap(ICON_PX, ICON_PX)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(ICON_COLOR))
    size = float(ICON_PX)

    def shape(points: list[tuple[float, float]]) -> None:
        painter.drawPolygon(QPolygonF([QPointF(x * size, y * size) for x, y in points]))

    def box(x: float, y: float, width: float, height: float) -> None:
        painter.drawRoundedRect(QRectF(x * size, y * size, width * size, height * size), 0.05 * size, 0.05 * size)

    if kind == "rewind":
        shape([(0.58, 0.16), (0.58, 0.84), (0.33, 0.50)])
        shape([(0.34, 0.16), (0.34, 0.84), (0.09, 0.50)])
    elif kind == "forward":
        shape([(0.42, 0.16), (0.42, 0.84), (0.67, 0.50)])
        shape([(0.66, 0.16), (0.66, 0.84), (0.91, 0.50)])
    elif kind == "play":
        shape([(0.26, 0.16), (0.26, 0.84), (0.80, 0.50)])
    elif kind == "pause":
        box(0.28, 0.18, 0.15, 0.64)
        box(0.57, 0.18, 0.15, 0.64)
    elif kind == "playstart":
        box(0.16, 0.16, 0.10, 0.68)
        shape([(0.40, 0.16), (0.40, 0.84), (0.90, 0.50)])
    elif kind == "stop":
        box(0.24, 0.24, 0.52, 0.52)
    elif kind == "pen":
        shape([(0.82, 0.31), (0.69, 0.18), (0.22, 0.65), (0.35, 0.78)])
        shape([(0.35, 0.78), (0.22, 0.65), (0.14, 0.86)])
    elif kind == "edit":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(ICON_COLOR), 0.09 * size, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        painter.drawRect(QRectF(0.13 * size, 0.13 * size, 0.74 * size, 0.74 * size))
        painter.drawLine(QPointF(0.50 * size, 0.13 * size), QPointF(0.50 * size, 0.87 * size))
        painter.drawLine(QPointF(0.13 * size, 0.50 * size), QPointF(0.87 * size, 0.50 * size))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ICON_COLOR))
        box(0.52, 0.38, 0.30, 0.10)  # two notes on the grid
        box(0.18, 0.54, 0.30, 0.10)
    elif kind == "select":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(ICON_COLOR), 0.10 * size, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        painter.drawRect(QRectF(0.12 * size, 0.18 * size, 0.76 * size, 0.64 * size))
    elif kind == "beat":
        painter.drawEllipse(QPointF(0.32 * size, 0.72 * size), 0.16 * size, 0.11 * size)
        painter.drawRect(QRectF(0.42 * size, 0.14 * size, 0.07 * size, 0.58 * size))
    elif kind == "seconds":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(ICON_COLOR), 0.09 * size, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        center = QPointF(0.5 * size, 0.5 * size)
        painter.drawEllipse(center, 0.36 * size, 0.36 * size)
        painter.drawLine(center, QPointF(0.5 * size, 0.26 * size))
        painter.drawLine(center, QPointF(0.70 * size, 0.58 * size))
    elif kind == "check":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(ICON_COLOR), 0.13 * size, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(0.18 * size, 0.54 * size), QPointF(0.42 * size, 0.78 * size))
        painter.drawLine(QPointF(0.42 * size, 0.78 * size), QPointF(0.82 * size, 0.24 * size))
    elif kind == "cross":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(ICON_COLOR), 0.12 * size, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(QPointF(0.28 * size, 0.28 * size), QPointF(0.72 * size, 0.72 * size))
        painter.drawLine(QPointF(0.72 * size, 0.28 * size), QPointF(0.28 * size, 0.72 * size))
    elif kind == "refresh":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(ICON_COLOR), 0.10 * size, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        painter.drawArc(QRectF(0.20 * size, 0.20 * size, 0.60 * size, 0.60 * size), 100 * 16, 250 * 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ICON_COLOR))
        shape([(0.38, 0.02), (0.74, 0.16), (0.44, 0.34)])
    painter.end()
    return QIcon(pixmap)


def caption_font() -> QFont:
    font = QFont()
    font.setPixelSize(10)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
    return font


class Cluster(QWidget):
    """A block of related controls sharing one caption, the WaveTone grouping."""

    def __init__(self, caption: str, spacing: int = 8):
        super().__init__()
        self.setObjectName("cluster")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.body = QGridLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(spacing)
        self.body.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.caption = QLabel(caption.upper())
        self.caption.setObjectName("clusterCaption")
        self.caption.setFont(caption_font())
        self.caption.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 3, 10, 3)
        layout.setSpacing(10)
        layout.addWidget(self.caption)
        layout.addLayout(self.body)
        self._columns: dict[int, int] = {}

    def add(self, widget: QWidget, row: int = 0) -> QWidget:
        column = self._columns.get(row, 0)
        self.body.addWidget(widget, row, column)
        self._columns[row] = column + 1
        return widget


class ValueSlider(QWidget):
    """A caption, a horizontal slider and the current value."""

    value_changed = pyqtSignal(float)

    def __init__(
        self,
        caption: str,
        minimum: float,
        maximum: float,
        value: float,
        suffix: str = "",
        scale: int = 1,
        slider_width: int = 92,
    ):
        super().__init__()
        self._suffix = suffix
        self._scale = scale
        self._decimals = max(0, len(str(scale)) - 1)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(round(minimum * scale), round(maximum * scale))
        self.slider.setValue(round(value * scale))
        self.slider.setFixedWidth(slider_width)
        self.slider.setFixedHeight(24)
        self.slider.setPageStep(max(1, round((maximum - minimum) * scale / 10)))
        self.caption = QLabel(caption)
        self.caption.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.value_label = QLabel()
        self.value_label.setObjectName("sliderValue")
        self.value_label.setFixedWidth(38)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.caption)
        layout.addWidget(self.slider)
        layout.addWidget(self.value_label)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self.slider.valueChanged.connect(self._on_value_changed)
        self._refresh()

    def value(self) -> float:
        return self.slider.value() / self._scale

    def set_value(self, value: float) -> None:
        self.slider.setValue(round(value * self._scale))

    def _refresh(self) -> None:
        self.value_label.setText(f"{self.value():.{self._decimals}f}{self._suffix}")

    def _on_value_changed(self, _value: int) -> None:
        self._refresh()
        self.value_changed.emit(self.value())


def field_label(caption: str) -> QLabel:
    label = QLabel(caption)
    label.setObjectName("fieldLabel")
    return label


def icon_button(kind: str, tooltip: str, checkable: bool = False) -> QToolButton:
    button = QToolButton()
    button.setIcon(_icon(kind))
    button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
    button.setToolTip(tooltip)
    button.setCheckable(checkable)
    button.setAutoRaise(True)
    button.setFixedHeight(BUTTON_HEIGHT)
    return button


def text_button(caption: str, tooltip: str, checkable: bool = False, width: int = 0) -> QToolButton:
    button = QToolButton()
    button.setText(caption)
    button.setToolTip(tooltip)
    button.setCheckable(checkable)
    button.setAutoRaise(True)
    button.setFixedHeight(BUTTON_HEIGHT)
    button.setObjectName("textButton")
    if width:
        button.setFixedWidth(width)
    return button


class TempoSuggestion(QWidget):
    """A tempo the audio suggests: apply it or drop it, but nothing changes on its own."""

    applied = pyqtSignal(float)
    dismissed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bpm = 0.0
        self.label = QLabel()
        self.apply_button = icon_button("check", "Use this tempo")
        self.dismiss_button = icon_button("cross", "Dismiss this estimate")
        self.apply_button.clicked.connect(lambda: self.applied.emit(self._bpm))
        self.dismiss_button.clicked.connect(self.dismissed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self.label)
        layout.addWidget(self.apply_button)
        layout.addWidget(self.dismiss_button)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.hide()

    def estimate(self, bpm: float, agreement: float, windows: int, residual: float) -> None:
        """Show `bpm` as a candidate, dimmed while few of the analysed windows agree on it."""
        self._bpm = bpm
        self.label.setText(f"≈{bpm:.0f} BPM")
        self.label.setStyleSheet(f"color: {SUGGESTION_COLOR if agreement >= WEAK_AGREEMENT else WEAK_COLOR}")
        self.setToolTip(
            f"Beat tracking + least-squares fit over {windows} windows of 12 s: "
            f"{agreement:.0%} of them agree.\nBeat fit residual {1000 * residual:.0f} ms.\n"
            "Nothing changes until you click the tick."
        )
        self.show()


class _SelectAll:
    """A spin box that selects its text when it is picked up, so typing replaces it."""

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.selectAll()

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        self.selectAll()


class TempoBox(_SelectAll, QDoubleSpinBox):
    """BPM, with a decimal only when the tempo has one.

    Doubling and halving live in the context menu and on `*` and `/` rather than on two more
    buttons, which would crowd the transport bar.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.double_action = QAction("Double tempo  (*)", self)
        self.half_action = QAction("Halve tempo  (/)", self)
        self.double_action.triggered.connect(lambda: self.scale(2.0))
        self.half_action.triggered.connect(lambda: self.scale(0.5))
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)

    def textFromValue(self, value: float) -> str:
        return f"{value:.1f}".rstrip("0").rstrip(".")

    def scale(self, factor: float) -> None:
        self.setValue(self.value() * factor)

    def context_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction(self.double_action)
        menu.addAction(self.half_action)
        self.double_action.setEnabled(self.value() * 2.0 <= self.maximum())
        self.half_action.setEnabled(self.value() / 2.0 >= self.minimum())
        return menu

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Asterisk, Qt.Key.Key_multiply):
            self.scale(2.0)
            return
        if event.key() == Qt.Key.Key_Slash:
            self.scale(0.5)
            return
        super().keyPressEvent(event)

    def _show_menu(self, position) -> None:
        self.context_menu().exec(self.mapToGlobal(position))


class LatencyBox(_SelectAll, QSpinBox):
    """Milliseconds; the unit is a label beside the field, the way WaveTone shows it."""


class TransportBar(QToolBar):
    """Playback transport, position, playback speed, tempo and latency."""

    rewind_requested = pyqtSignal()
    play_from_start_requested = pyqtSignal()
    play_pause_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    forward_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Transport", parent)
        self.rewind = icon_button("rewind", "Rewind to the beginning")
        self.stop = icon_button("stop", "Stop")
        self.play_from_start = icon_button("playstart", "Play from the beginning")
        self.play_pause = icon_button("play", "Play from the cursor")
        self.forward = icon_button("forward", "Go to the end")
        self.rewind.clicked.connect(self.rewind_requested)
        self.stop.clicked.connect(self.stop_requested)
        self.play_from_start.clicked.connect(self.play_from_start_requested)
        self.play_pause.clicked.connect(self.play_pause_requested)
        self.forward.clicked.connect(self.forward_requested)

        self.position = QLabel("00:00.000")
        self.position.setObjectName("position")
        self.position.setToolTip("Playback position")
        self.position.setFixedWidth(76)
        self.position.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.speed = ValueSlider("", 0.25, 2.0, 1.0, suffix="x", scale=100)
        self.speed.slider.setToolTip("Playback speed, 0.25x to 2.00x")
        self.speed_reset = text_button("1.0", "Reset the playback speed to 1.00x", width=40)
        self.speed_reset.clicked.connect(lambda: self.speed.set_value(1.0))

        self.bpm = TempoBox()
        self.bpm.setRange(20.0, 300.0)
        self.bpm.setDecimals(1)
        self.bpm.setValue(120.0)
        self.bpm.setToolTip(
            "Tempo of the beat grid in BPM, until a tempo map is analysed\nRight-click to double or halve it"
        )
        self.bpm.setFixedWidth(78)
        self.bpm.setFixedHeight(FIELD_HEIGHT)
        self.bpm.setKeyboardTracking(False)

        self.detect = icon_button("refresh", "Estimate the tempo of the loaded audio")
        self.detect.setEnabled(False)
        self.tempo = TempoSuggestion()

        self.latency = LatencyBox()
        self.latency.setRange(-500, 500)
        self.latency.setValue(0)
        self.latency.setToolTip("Global offset between audio playback and the displayed waveform")
        self.latency.setFixedWidth(74)
        self.latency.setFixedHeight(FIELD_HEIGHT)
        self.latency.setKeyboardTracking(False)

        playback = Cluster("Playback")
        for button in (self.rewind, self.stop, self.play_from_start, self.play_pause, self.forward):
            playback.add(button, row=0)
        playback.add(self.position, row=0)

        speed = Cluster("Speed")
        speed.add(self.speed, row=0)
        speed.add(self.speed_reset, row=0)

        tempo = Cluster("Tempo")
        tempo.add(self.bpm, row=0)
        tempo.add(self.detect, row=0)
        tempo.add(self.tempo, row=0)

        latency = Cluster("Latency")
        latency.add(self.latency, row=0)
        latency.add(field_label("ms"), row=0)

        for cluster in (playback, speed, tempo, latency):
            self.addWidget(cluster)

    def set_playing(self, playing: bool) -> None:
        """Playing and pausing share one button the way WaveTone shows it, so it turns into a
        pause button while the sound runs."""
        self.play_pause.setIcon(_icon("pause" if playing else "play"))
        self.play_pause.setToolTip("Pause playback" if playing else "Play from the cursor")

    def set_position(self, seconds: float) -> None:
        self.position.setText(format_time(seconds))


class EditBar(QToolBar):
    """Tools, time-axis division, snapping, and note cleanup."""

    tool_changed = pyqtSignal(str)
    mode_changed = pyqtSignal(bool)
    division_changed = pyqtSignal(str)
    clear_requested = pyqtSignal()

    def __init__(self, snap_choices, parent=None):
        super().__init__("Edit", parent)
        self.mode = icon_button(
            "edit", "Edit mode: draw, move and select notes (picking a tool turns it on)", checkable=True
        )
        self.mode.setChecked(True)
        self.mode.clicked.connect(self._mode_clicked)
        self.pen = icon_button("pen", "Pen: click or drag an empty row to draw a note", checkable=True)
        self.select = icon_button(
            "select", "Select: drag a box, ctrl-click a note to add, drag a note to move", checkable=True
        )
        self.pen.setChecked(True)
        self.tools = QButtonGroup(self)
        self.tools.setExclusive(True)
        self.tools.addButton(self.pen)
        self.tools.addButton(self.select)
        self.pen.clicked.connect(lambda: self._tool_clicked("pen"))
        self.select.clicked.connect(lambda: self._tool_clicked("select"))

        self.snap = QComboBox()
        for label, beats in snap_choices:
            self.snap.addItem(label, beats)
        self.snap.setCurrentIndex(self.snap.findText("1/8"))
        self.snap.setToolTip("Snap grid for the pen tool")
        self.snap.setFixedWidth(66)
        self.snap.setFixedHeight(FIELD_HEIGHT)

        self.clear = text_button("Clear", "Delete every note")
        self.clear.clicked.connect(self.clear_requested)

        tools = Cluster("Tools")
        tools.add(self.mode, row=0)
        tools.add(self.pen, row=0)
        tools.add(self.select, row=0)
        tools.add(field_label("Snap"), row=0)
        tools.add(self.snap, row=0)
        tools.add(self.clear, row=0)

        self.division_beats = icon_button("beat", "Divide the time axis by beats of the tempo map", checkable=True)
        self.division_seconds = icon_button("seconds", "Divide the time axis by seconds", checkable=True)
        self.division_beats.setChecked(True)
        self.division = QButtonGroup(self)
        self.division.setExclusive(True)
        self.division.addButton(self.division_beats)
        self.division.addButton(self.division_seconds)
        self.division_beats.clicked.connect(lambda: self.division_changed.emit("beats"))
        self.division_seconds.clicked.connect(lambda: self.division_changed.emit("seconds"))

        division = Cluster("Division")
        division.add(self.division_beats, row=0)
        division.add(self.division_seconds, row=0)

        self.addWidget(tools)
        self.addWidget(division)

    def _mode_clicked(self) -> None:
        editing = self.mode.isChecked()
        self._set_mode(editing)
        if editing:
            self.pen.setChecked(True)  # entering the mode starts on the pen
            self.tool_changed.emit("pen")
        else:
            self.tools.setExclusive(False)
            for button in self.tools.buttons():
                button.setChecked(False)
            self.tools.setExclusive(True)
            self.tool_changed.emit("")

    def _tool_clicked(self, tool: str) -> None:
        if not self.mode.isChecked():
            self._set_mode(True)
        self.tool_changed.emit(tool)

    def _set_mode(self, editing: bool) -> None:
        self.mode.setChecked(editing)
        self.snap.setEnabled(editing)
        self.clear.setEnabled(editing)
        self.mode_changed.emit(editing)


class MixBar(QToolBar):
    """Spectrum display parameters and the audio/MIDI mix."""

    def __init__(self, parent=None):
        super().__init__("Mix", parent)
        self.gain = ValueSlider("Gain", 10, 600, 240)
        self.gain.slider.setToolTip("Spectrum gain: how much energy it takes to reach full red")
        self.contrast = ValueSlider("Contrast", 0.2, 4.0, 1.0, scale=10)
        self.contrast.slider.setToolTip("Spectrum contrast: exponent applied to the energy")
        self.audio_volume = ValueSlider("Audio", 0, 100, 80, suffix="%")
        self.audio_volume.slider.setToolTip("Volume of the analysed audio track")
        self.midi_volume = ValueSlider("MIDI", 0, 100, 80, suffix="%")
        self.midi_volume.slider.setToolTip("Volume of the note playback")

        spectrum = Cluster("Spectrum", spacing=12)
        spectrum.add(self.gain, row=0)
        spectrum.add(self.contrast, row=0)

        volume = Cluster("Volume", spacing=12)
        volume.add(self.audio_volume, row=0)
        volume.add(self.midi_volume, row=0)

        self.addWidget(spectrum)
        self.addWidget(volume)
