# SPDX-License-Identifier: AGPL-3.0-only
"""The channel sidebar: one card per MIDI channel, noteDigger style, toggled from the edit bar."""

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
from namioto.channels import Channel, free_channel
from namioto.ui import icons, theme
from namioto.ui.controls import BUTTON_HEIGHT
from namioto.ui.roll import PianoRollView

# wide enough that the longest General MIDI name fits the combo whole, scrollbar included
CARD_WIDTH = 300
SWATCH_WIDTH = 5


class _NameLabel(QLabel):
    """The channel name, elided so a long one cannot push the card wider than its column."""

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
    """One channel: colour swatch, name and instrument, the lock/eye/mute buttons."""

    def __init__(self, panel: ChannelPanel, channel: Channel):
        super().__init__()
        self._panel = panel
        self._channel = channel
        self._number = channel.channel
        self.setObjectName("channelCard")
        if channel.channel == panel.view.active_channel:
            self.setProperty("active", True)

        swatch = QFrame()
        swatch.setFixedSize(SWATCH_WIDTH, 34)
        swatch.setStyleSheet(f"background: {channel.color}; border-radius: 2px;")

        self.name = _NameLabel(channel.label)
        self.program = QComboBox()
        self.program.addItems(store.GM_PROGRAMS)  # the index is the program number
        # the card is a fixed column: the combo must be allowed to shrink below its longest item
        self.program.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.program.setMinimumContentsLength(12)
        self.program.setCurrentIndex(channel.program)
        self.program.setFixedHeight(22)
        self.program.setToolTip(store.PROGRAM_LABELS[channel.program])
        self.program.currentIndexChanged.connect(self._on_program)

        self.lock_button = _state_button("lock", "unlock", channel.lock, "Lock: notes on this channel cannot be edited")
        self.eye_button = _state_button("eye", "eyeoff", channel.visible, "Show or hide the notes of this channel")
        self.mute_button = _state_button("mute", "sound", channel.mute, "Mute this channel during playback")
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
            self._panel.view.set_active_channel(self._number)
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:
        panel, channel = self._panel, self._channel
        menu = QMenu(self)
        rename = menu.addAction("Rename…")
        volume = menu.addAction("Volume…")
        remove = menu.addAction("Delete channel")
        chosen = menu.exec(event.globalPos())
        view = panel.view
        if chosen is rename:
            name, ok = QInputDialog.getText(self, "Rename channel", "Name:", text=channel.name)
            if ok and name.strip():
                view.set_channel_field(self._number, name=name.strip())
        elif chosen is volume:
            value, ok = QInputDialog.getInt(self, "Channel volume", "Volume (0-127):", channel.volume, 0, 127)
            if ok:
                view.set_channel_field(self._number, volume=value)
        elif chosen is remove:
            view.remove_channel(self._number)

    def _toggle(self, field: str) -> None:
        channel = self._channel
        if field == "visible":
            visible = not channel.visible
            # hiding also locks, the way noteDigger couples the eye and the padlock
            self._panel.view.set_channel_field(self._number, visible=visible, lock=not visible)
        else:
            self._panel.view.set_channel_field(self._number, **{field: not getattr(channel, field)})

    def _on_program(self, program: int) -> None:
        self.program.setToolTip(store.PROGRAM_LABELS[program])
        self._panel.view.set_channel_field(self._number, program=program)


class ChannelPanel(QWidget):
    """The list of the document's channels; a view over `PianoRollView.channels`, never a copy."""

    def __init__(self, view: PianoRollView, default_program: int = 0, parent=None):
        super().__init__(parent)
        self.view = view
        self.default_program = default_program
        self.setObjectName("channelPanel")
        self.setFixedWidth(CARD_WIDTH)
        view.channels_changed.connect(self._rebuild)
        view.active_channel_changed.connect(lambda _number: self._rebuild())

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
        for channel in self.view.channels:
            self.cards.addWidget(_Card(self, channel))
        self.cards.addStretch(1)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        add = menu.addAction("Add channel")
        add.setEnabled(free_channel(self.view.channels) is not None)
        if menu.exec(event.globalPos()) is add:
            self.view.add_channel(program=self.default_program)
