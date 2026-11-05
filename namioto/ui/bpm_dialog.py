# SPDX-License-Identifier: AGPL-3.0-only
"""Compare BPM estimates on one selectable WaveTone distribution chart."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout, QWidget

from namioto.analysis import bpm
from namioto.analysis.choices import ALGORITHMS
from namioto.i18n import tr
from namioto.ui.loading import LoadingThread


class BpmDistribution(QWidget):
    """WaveTone's score curve and clickable estimates from the other backends."""

    COLORS = {
        "wavetone": QColor("#36a879"),
        "librosa": QColor("#e08b32"),
        "tempocnn": QColor("#548be0"),
    }
    selected = Signal(float)
    LEFT, RIGHT, TOP, BOTTOM = 56, 24, 34, 92

    def __init__(self, parent=None):
        super().__init__(parent)
        self.estimates = {}
        self.errors = set()
        self.pending = set(ALGORITHMS)
        self.selected_value = None
        self.hovered_value = None
        self.hover_amount = 0.0
        self.hover_animation = QVariantAnimation(self)
        self.hover_animation.setDuration(150)
        self.hover_animation.setStartValue(0.0)
        self.hover_animation.setEndValue(1.0)
        self.hover_animation.valueChanged.connect(self._hover_changed)
        self.setMinimumSize(540, 360)
        self.setMouseTracking(True)

    def set_estimates(self, estimates, errors=(), pending=()) -> None:
        self.estimates = estimates
        self.errors = set(errors)
        self.pending = set(pending)
        self.update()

    def _plot(self) -> QRectF:
        return QRectF(
            self.LEFT,
            self.TOP,
            max(1, self.width() - self.LEFT - self.RIGHT),
            max(1, self.height() - self.TOP - self.BOTTOM),
        )

    def _x(self, value: float) -> float:
        plot = self._plot()
        return plot.left() + (value - 60) / 180 * plot.width()

    def _bpm_at_x(self, x: float) -> float:
        plot = self._plot()
        return 60 + (x - plot.left()) / plot.width() * 180

    def _markers(self):
        markers = []
        wave = self.estimates.get("wavetone")
        if wave is not None:
            for index, (value, score) in enumerate(wave.candidates or ((wave.bpm, 1.0),)):
                markers.append(("wavetone", float(value), float(score), index))
        for algorithm in ("librosa", "tempocnn"):
            estimate = self.estimates.get(algorithm)
            if estimate is not None:
                score = estimate.agreement if estimate.windows else 0.0
                markers.append((algorithm, float(estimate.bpm), score, 0))
        return markers

    def _marker_point(self, algorithm: str, value: float, score: float) -> QPointF:
        plot = self._plot()
        wave = self.estimates.get("wavetone")
        if algorithm == "wavetone" and wave is not None and wave.distribution:
            peak = max((item[1] for item in wave.distribution), default=1.0) or 1.0
            nearest = min(wave.distribution, key=lambda item: abs(item[0] - value))[1]
            y = plot.bottom() - nearest / peak * plot.height()
        else:
            y = plot.bottom() - (0.30 + 0.55 * score) * plot.height()
        return QPointF(self._x(value), y)

    def _closest_marker(self, point: QPointF):
        markers = self._markers()
        if not markers:
            return None
        cursor_bpm = self._bpm_at_x(point.x())
        return min(
            (
                (
                    abs(cursor_bpm - value),
                    algorithm,
                    value,
                )
                for algorithm, value, _score, _index in markers
            ),
            default=None,
        )

    def _hover_changed(self, value) -> None:
        self.hover_amount = float(value)
        self.update()

    def mouseMoveEvent(self, event) -> None:
        point = event.position()
        plot = self._plot()
        closest = self._closest_marker(point) if plot.left() <= point.x() <= plot.right() else None
        hovered = (closest[1], closest[2]) if closest else None
        if hovered != self.hovered_value:
            self.hovered_value = hovered
            self.hover_animation.stop()
            self.hover_animation.setStartValue(0.0 if hovered else self.hover_amount)
            self.hover_animation.setEndValue(1.0 if hovered else 0.0)
            self.hover_animation.start()
        if hovered:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip(f"{self._name(hovered[0])}: {hovered[1]:.2f} BPM")
        else:
            self.unsetCursor()
            self.setToolTip("")
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            point = event.position()
            plot = self._plot()
            closest = self._closest_marker(point)
            if closest and plot.left() <= point.x() <= plot.right():
                self.selected_value = (closest[1], closest[2])
                self.selected.emit(closest[2])
                self.update()
        super().mousePressEvent(event)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = self._plot()
        palette = self.palette()
        text_color = palette.text().color()
        muted = text_color

        painter.setPen(text_color)
        painter.drawText(QRectF(12, 4, 100, 20), Qt.AlignmentFlag.AlignVCenter, "BPM")
        if self.hovered_value is not None:
            hover_algorithm, hover_bpm = self.hovered_value
            painter.setPen(self.COLORS[hover_algorithm])
            painter.drawText(
                QRectF(68, 4, 240, 22),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                f"{self._name(hover_algorithm)}: {hover_bpm:.2f} BPM",
            )
        for tempo in (60, 90, 120, 150, 180, 210, 240):
            x = self._x(tempo)
            painter.setPen(QPen(palette.midlight().color(), 1))
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.setPen(muted)
            painter.drawText(
                QRectF(x - 20, plot.bottom() + 5, 40, 20),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                str(tempo),
            )
        painter.setPen(QPen(palette.mid().color(), 1))
        painter.drawLine(QPointF(plot.left(), plot.bottom()), QPointF(plot.right(), plot.bottom()))

        bpm_labels = []
        lane_rights = []
        for algorithm, value, _score, _index in sorted(self._markers(), key=lambda marker: self._x(marker[1])):
            x = self._x(value)
            label = f"{value:.2f}"
            width = painter.fontMetrics().horizontalAdvance(label) + 8
            left = max(plot.left(), min(x - width / 2, plot.right() - width))
            lane = next((i for i, right in enumerate(lane_rights) if left > right + 5), len(lane_rights))
            if lane == len(lane_rights):
                lane_rights.append(left + width)
            else:
                lane_rights[lane] = left + width
            bpm_labels.append((algorithm, value, x, left, width, lane, label))

        for algorithm, value, x, left, width, lane, label in bpm_labels:
            color = self.COLORS[algorithm]
            label_y = plot.bottom() + 23 + lane * 18
            painter.setPen(QPen(color, 1))
            painter.drawLine(QPointF(x, plot.bottom()), QPointF(x, label_y - 3))
            if self.selected_value == (algorithm, value):
                painter.setPen(QPen(color, 1))
                painter.setBrush(QColor(color.red(), color.green(), color.blue(), 35))
                painter.drawRoundedRect(QRectF(left, label_y - 2, width, 17), 3, 3)
            painter.setPen(color)
            painter.drawText(
                QRectF(left, label_y - 2, width, 17),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextSingleLine,
                label,
            )

        wave = self.estimates.get("wavetone")
        peak = 1.0
        if wave is not None and wave.distribution:
            peak = max((score for _value, score in wave.distribution), default=1.0) or 1.0
            points = [
                QPointF(self._x(value), plot.bottom() - score / peak * plot.height())
                for value, score in wave.distribution
            ]
            area = QPainterPath(QPointF(points[0].x(), plot.bottom()))
            for point in points:
                area.lineTo(point)
            area.lineTo(QPointF(points[-1].x(), plot.bottom()))
            area.closeSubpath()
            color = self.COLORS["wavetone"]
            painter.setPen(QPen(color, 2))
            painter.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 65)))
            painter.drawPath(area)

        for algorithm, value, score, _index in self._markers():
            x = self._x(value)
            color = self.COLORS[algorithm]
            marker = self._marker_point(algorithm, value, score)
            if algorithm != "wavetone":
                painter.setPen(QPen(color, 2, Qt.PenStyle.DashLine))
                painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            chosen = self.selected_value == (algorithm, value)
            hovered = self.hovered_value == (algorithm, value)
            radius = 4.5 + (2.5 * self.hover_amount if hovered else 0) + (1.5 if chosen else 0)
            if hovered or chosen:
                painter.setPen(Qt.PenStyle.NoPen)
                alpha = 55 if hovered else 35
                painter.setBrush(QColor(color.red(), color.green(), color.blue(), alpha))
                painter.drawEllipse(marker, radius + 6, radius + 6)
            painter.setPen(QPen(palette.base().color(), 2) if chosen else QPen(color, 2))
            painter.setBrush(QBrush(color))
            painter.drawEllipse(marker, radius, radius)

        legend_top = plot.top() + 10
        slot_width = 122
        legend_left = plot.left() + 10
        legend_rect = QRectF(legend_left, legend_top, slot_width + 18, 76)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(palette.window().color().red(), palette.window().color().green(),
                                palette.window().color().blue(), 220))
        painter.drawRoundedRect(legend_rect, 4, 4)
        for index, algorithm in enumerate(ALGORITHMS):
            x = legend_left + 9
            y = legend_top + 8 + index * 22
            painter.setPen(QPen(self.COLORS[algorithm], 3))
            painter.drawLine(QPointF(x, y + 10), QPointF(x + 16, y + 10))
            painter.setPen(text_color)
            painter.drawText(
                QRectF(x + 23, y, slot_width - 28, 20),
                Qt.AlignmentFlag.AlignVCenter | Qt.TextFlag.TextSingleLine,
                self._name(algorithm),
            )

    @staticmethod
    def _name(algorithm: str) -> str:
        return {"wavetone": tr("WaveTone"), "librosa": tr("Librosa"), "tempocnn": tr("TempoCNN")}[algorithm]


class BpmAnalysisLoader(LoadingThread):
    """Run one BPM backend off the GUI thread and report its result independently."""

    loaded = Signal(str, object, str)

    def __init__(self, path: str, algorithm: str, parent=None):
        super().__init__(path, parent)
        self.algorithm = algorithm

    def load(self) -> None:
        try:
            self.loaded.emit(self.algorithm, bpm.estimate(self.path, self.algorithm), "")
        except Exception as error:
            self.loaded.emit(self.algorithm, None, f"{type(error).__name__}: {error}")


class BpmAnalysisDialog(QDialog):
    """Compare the three BPM backends and select a chart marker."""

    def __init__(self, audio: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("BPM analysis"))
        self.setMinimumSize(580, 420)
        self.selected_bpm: float | None = None
        self.pending = set(ALGORITHMS)
        self.results = {}
        self.errors = set()
        self.user_selected = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        self.distribution = BpmDistribution(self)
        self.distribution.selected.connect(self._select_bpm)
        layout.addWidget(self.distribution, 1)
        self.selected_label = QLabel(f"{tr('Selected BPM')}: --")
        self.selected_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.selected_label.setStyleSheet("font-size: 20px; font-weight: 600; padding: 8px;")
        layout.addWidget(self.selected_label)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = buttons.addButton(tr("Use selected BPM"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_chart()
        start_loader = getattr(parent, "_start_loader", None)
        for algorithm in ALGORITHMS:
            loader = BpmAnalysisLoader(audio, algorithm, self)
            loader.loaded.connect(self._backend_loaded)
            loader.failed.connect(lambda message, name=algorithm: self._backend_loaded(name, None, message))
            if callable(start_loader):
                start_loader(f"bpm_analysis_loader_{algorithm}_{id(self)}", loader)
            else:
                loader.start()

    def _backend_loaded(self, algorithm: str, result, error: str) -> None:
        self.pending.discard(algorithm)
        if error or result is None:
            self.errors.add(algorithm)
        else:
            self.errors.discard(algorithm)
            self.results[algorithm] = result
            if not self.user_selected and (algorithm == "wavetone" or self.distribution.selected_value is None):
                value = result.candidates[0][0] if algorithm == "wavetone" and result.candidates else result.bpm
                self.distribution.selected_value = (algorithm, float(value))
                self.selected_bpm = float(value)
                self._update_selected_label(float(value), algorithm)
                self.apply_button.setEnabled(True)
        self._refresh_chart()

    def _refresh_chart(self) -> None:
        self.distribution.set_estimates(self.results, self.errors, self.pending)

    def _select_bpm(self, value: float) -> None:
        self.user_selected = True
        self.selected_bpm = value
        selected = self.distribution.selected_value
        self._update_selected_label(value, selected[0] if selected else "wavetone")
        self.apply_button.setEnabled(True)

    def _update_selected_label(self, value: float, algorithm: str) -> None:
        self.selected_label.setText(f"{tr('Selected BPM')}: {value:.2f}")
        color = BpmDistribution.COLORS[algorithm].name()
        self.selected_label.setStyleSheet(
            f"font-size: 20px; font-weight: 600; padding: 8px; color: {color};"
        )
