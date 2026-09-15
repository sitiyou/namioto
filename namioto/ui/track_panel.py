# SPDX-License-Identifier: AGPL-3.0-only
"""The track sidebar: one card per track, noteDigger style, toggled from the edit bar."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPainter, QPalette
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from namioto import settings as store
from namioto.tracks import TRACK_LIMIT
from namioto.ui import icons, theme
from namioto.ui.controls import BUTTON_HEIGHT
from namioto.ui.roll import PianoRollView

# wide enough that the longest General MIDI name fits the combo whole, scrollbar included
CARD_WIDTH = 300
SWATCH_WIDTH = 5


class _NameLabel(QLabel):
    """The track name, elided so a long one cannot push the card wider than its column."""

    def __init__(self, text: str):
        super().__init__(text)
        self.setMinimumWidth(1)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.WindowText, QColor(theme.TOKENS["dark"]["TEXT"]))
        self.setPalette(palette)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        metrics = painter.fontMetrics()
        painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
        painter.drawText(
            self.rect(),
            Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width()),
        )


def _state_button(kind_on: str, kind_off: str, on: bool, tooltip: str) -> QToolButton:
    button = QToolButton()
    button.setIcon(icons.icon(kind_on if on else kind_off))
    button.setIconSize(button.iconSize())
    button.setFixedHeight(BUTTON_HEIGHT)
    button.setAutoRaise(True)
    button.setToolTip(tooltip)
    return button


class _Card(QWidget):
    """One track: colour swatch, name and instrument, the lock/eye/mute buttons."""

    def __init__(self, panel: TrackPanel, index: int):
        super().__init__()
        self._panel = panel
        self._index = index
        track = panel.view.tracks[index]
        self.setObjectName("trackCard")
        if index == panel.view.active_track:
            self.setProperty("active", True)

        swatch = QFrame()
        swatch.setFixedSize(SWATCH_WIDTH, 34)
        swatch.setStyleSheet(f"background: {track.color}; border-radius: 2px;")

        self.name = _NameLabel(track.label)
        self.program = QComboBox()
        self.program.addItems(store.GM_PROGRAMS)  # the index is the program number
        # the card is a fixed column: the combo must be allowed to shrink below its longest item
        self.program.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.program.setMinimumContentsLength(12)
        self.program.setCurrentIndex(track.program)
        self.program.setFixedHeight(22)
        self.program.setToolTip(store.PROGRAM_LABELS[track.program])
        self.program.currentIndexChanged.connect(self._on_program)

        self.lock_button = _state_button("lock", "unlock", track.lock, "Lock: notes on this track cannot be edited")
        self.eye_button = _state_button("eye", "eyeoff", track.visible, "Show or hide the notes of this track")
        self.mute_button = _state_button("mute", "sound", track.mute, "Mute this track during playback")
        for button, field in ((self.lock_button, "lock"), (self.eye_button, "visible"), (self.mute_button, "mute")):
            button.clicked.connect(lambda _c, field=field: self._toggle(field))

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(2)
        head.addWidget(self.name, 1)
        head.addWidget(self.lock_button)
        head.addWidget(self.eye_button)
        head.addWidget(self.mute_button)

        text = QVBoxLayout()
        text.setContentsMargins(0, 2, 0, 2)
        text.setSpacing(2)
        text.addLayout(head)
        text.addWidget(self.program)

        row = QHBoxLayout()
        row.setContentsMargins(6, 4, 4, 4)
        row.setSpacing(6)
        row.addWidget(swatch)
        row.addLayout(text, 1)
        self.setLayout(row)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._panel.view.set_active_track(self._index)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:
        panel, track = self._panel, self._panel.view.tracks[self._index]
        menu = QMenu(self)
        rename = menu.addAction("Rename…")
        volume = menu.addAction("Volume…")
        remove = menu.addAction("Delete track")
        chosen = menu.exec(event.globalPos())
        view = panel.view
        if chosen is rename:
            name, ok = QInputDialog.getText(self, "Rename track", "Name:", text=track.name)
            if ok and name.strip():
                view.set_track_field(self._index, name=name.strip())
        elif chosen is volume:
            value, ok = QInputDialog.getInt(self, "Track volume", "Volume (0-127):", track.volume, 0, 127)
            if ok:
                view.set_track_field(self._index, volume=value)
        elif chosen is remove:
            view.remove_track(self._index)

    def _toggle(self, field: str) -> None:
        track = self._panel.view.tracks[self._index]
        if field == "visible":
            visible = not track.visible
            # hiding also locks, the way noteDigger couples the eye and the padlock
            self._panel.view.set_track_field(self._index, visible=visible, lock=not visible)
        else:
            self._panel.view.set_track_field(self._index, **{field: not getattr(track, field)})

    def _on_program(self, program: int) -> None:
        self.program.setToolTip(store.PROGRAM_LABELS[program])
        self._panel.view.set_track_field(self._index, program=program)


class TrackPanel(QWidget):
    """The list of the document's tracks; a view over `PianoRollView.tracks`, never a second copy."""

    def __init__(self, view: PianoRollView, default_program: int = 0, parent=None):
        super().__init__(parent)
        self.view = view
        self.default_program = default_program
        self.setObjectName("trackPanel")
        self.setFixedWidth(CARD_WIDTH)
        view.tracks_changed.connect(self._rebuild)
        view.active_track_changed.connect(lambda _index: self._rebuild())

        self.cards = QVBoxLayout()
        self.cards.setContentsMargins(6, 6, 6, 6)
        self.cards.setSpacing(4)

        body = QWidget()
        body.setLayout(self.cards)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # the card is the column width
        scroll.setWidget(body)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self._rebuild()

    def _rebuild(self) -> None:
        while self.cards.count():
            item = self.cards.takeAt(0)
            if widget := item.widget():
                widget.deleteLater()
        for index in range(len(self.view.tracks)):
            self.cards.addWidget(_Card(self, index))
        self.cards.addStretch(1)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        add = menu.addAction("Add track")
        add.setEnabled(len(self.view.tracks) < TRACK_LIMIT)
        if menu.exec(event.globalPos()) is add:
            self.view.add_track(program=self.default_program)
