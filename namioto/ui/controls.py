# SPDX-License-Identifier: AGPL-3.0-only
"""Control bars: playback, edit, spectrum and mix, each grouped into captioned blocks."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
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
    elif kind == "stop":
        box(0.24, 0.24, 0.52, 0.52)
    elif kind == "pen":
        shape([(0.82, 0.31), (0.69, 0.18), (0.22, 0.65), (0.35, 0.78)])
        shape([(0.35, 0.78), (0.22, 0.65), (0.14, 0.86)])
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

    def estimate(self, bpm: float, agreement: float, windows: int) -> None:
        """Show `bpm` as a candidate, dimmed while few of the analysed windows agree on it."""
        self._bpm = bpm
        self.label.setText(f"≈{bpm:.0f} BPM")
        self.label.setStyleSheet(f"color: {SUGGESTION_COLOR if agreement >= WEAK_AGREEMENT else WEAK_COLOR}")
        self.setToolTip(
            f"TempoCNN estimate over {windows} windows of 12 s: {agreement:.0%} of them agree.\n"
            "Nothing changes until you click the tick."
        )
        self.show()


class TransportBar(QToolBar):
    """Playback transport, position, playback speed, tempo and latency."""

    rewind_requested = pyqtSignal()
    play_requested = pyqtSignal()
    pause_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    forward_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Transport", parent)
        self.rewind = icon_button("rewind", "Rewind to the beginning")
        self.stop = icon_button("stop", "Stop")
        self.pause = icon_button("pause", "Pause")
        self.play = icon_button("play", "Play")
        self.forward = icon_button("forward", "Go to the end")
        self.rewind.clicked.connect(self.rewind_requested)
        self.play.clicked.connect(self.play_requested)
        self.pause.clicked.connect(self.pause_requested)
        self.stop.clicked.connect(self.stop_requested)
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

        self.bpm = QDoubleSpinBox()
        self.bpm.setRange(20.0, 300.0)
        self.bpm.setDecimals(1)
        self.bpm.setValue(120.0)
        self.bpm.setToolTip("Tempo of the beat grid in BPM, until a tempo map is analysed")
        self.bpm.setFixedWidth(78)
        self.bpm.setFixedHeight(FIELD_HEIGHT)
        self.bpm.setKeyboardTracking(False)

        self.detect = icon_button("refresh", "Estimate the tempo of the loaded audio")
        self.detect.setEnabled(False)
        self.tempo = TempoSuggestion()

        self.latency = QSpinBox()
        self.latency.setRange(-500, 500)
        self.latency.setValue(0)
        self.latency.setSuffix(" ms")
        self.latency.setToolTip("Global offset between audio playback and the displayed waveform")
        self.latency.setFixedWidth(74)
        self.latency.setFixedHeight(FIELD_HEIGHT)
        self.latency.setKeyboardTracking(False)

        playback = Cluster("Playback")
        for button in (self.rewind, self.stop, self.pause, self.play, self.forward):
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

        for cluster in (playback, speed, tempo, latency):
            self.addWidget(cluster)

    def set_position(self, seconds: float) -> None:
        self.position.setText(format_time(seconds))


class EditBar(QToolBar):
    """Tools, time-axis division, snapping, and note cleanup."""

    tool_changed = pyqtSignal(str)
    division_changed = pyqtSignal(str)
    clear_requested = pyqtSignal()

    def __init__(self, snap_choices, parent=None):
        super().__init__("Edit", parent)
        self.pen = icon_button("pen", "Pen: click or drag an empty row to draw a note", checkable=True)
        self.select = icon_button(
            "select", "Select: drag a box, ctrl-click a note to add, drag a note to move", checkable=True
        )
        self.pen.setChecked(True)
        self.tools = QButtonGroup(self)
        self.tools.setExclusive(True)
        self.tools.addButton(self.pen)
        self.tools.addButton(self.select)
        self.pen.clicked.connect(lambda: self.tool_changed.emit("pen"))
        self.select.clicked.connect(lambda: self.tool_changed.emit("select"))

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
