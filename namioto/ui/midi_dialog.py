# SPDX-License-Identifier: AGPL-3.0-only
"""The choices a MIDI export offers, and what an import does to the roll."""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QVBoxLayout,
)

from namioto.tracks import TRACK_LIMIT


class MidiExportDialog(QDialog):
    """How the notes are written, and which of them."""

    def __init__(self, snap_label: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export MIDI")
        self.exact = QCheckBox("Exact time: 60 BPM, a tick being 0.1 ms, so playback is unchanged")
        self.exact.setToolTip("For a transcription taken from audio, whose times are not on any grid")
        self.quantize = QCheckBox(f"Snap the notes to the {snap_label} grid")
        self.quantize.setToolTip("For a score: the timing is rounded onto the grid the roll is drawing")
        self.visible_only = QCheckBox("Only the visible tracks")
        self.visible_only.setToolTip("A muted track still goes in unless it is hidden")
        self.quantize.setEnabled(False)
        self.exact.toggled.connect(self._on_exact)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Export")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        for box in (self.exact, self.quantize, self.visible_only):
            layout.addWidget(box)
        layout.addWidget(buttons)

    def _on_exact(self, exact: bool) -> None:
        if exact:
            self.quantize.setChecked(False)
        self.quantize.setEnabled(not exact)

    def options(self) -> dict[str, bool]:
        return {
            "exact": self.exact.isChecked(),
            "quantize": self.quantize.isChecked(),
            "visible_only": self.visible_only.isChecked(),
        }


class MidiImportDialog(QDialog):
    """What an import does to the roll, and where each file track lands.

    A merge is the additive choice: every file track is pointed at an existing track or at a new
    one. The mapping is filled in by channel, which is how a file and a project usually agree,
    and the dialog is where it is changed before the notes arrive.
    """

    def __init__(self, imported, tracks, source: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Import MIDI")
        self._tracks = list(tracks)
        self._mode = "merge"
        self._targets: list[QComboBox] = []

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"{len(imported.notes)} notes in {len(imported.tracks)} tracks from {source}."))
        layout.addWidget(QLabel("The notes already on the roll are replaced, or merged into the tracks below."))

        grid = QGridLayout()
        grid.addWidget(QLabel("File track"), 0, 0)
        grid.addWidget(QLabel("Lands on"), 0, 1)
        for row, track in enumerate(imported.tracks, start=1):
            grid.addWidget(QLabel(f"{track.label} (channel {track.channel + 1})"), row, 0)
            combo = QComboBox()
            for index, current in enumerate(self._tracks):
                combo.addItem(f"{current.label} (channel {current.channel + 1})", index)
            combo.addItem("New track", -1)
            combo.setCurrentIndex(max(0, combo.findData(self._matching_track(track))))
            combo.currentIndexChanged.connect(self._refresh)
            self._targets.append(combo)
            grid.addWidget(combo, row, 1)
        layout.addLayout(grid)

        buttons = QDialogButtonBox()
        self.merge_button = buttons.addButton("Merge", QDialogButtonBox.ButtonRole.AcceptRole)
        self.replace_button = buttons.addButton("Replace", QDialogButtonBox.ButtonRole.DestructiveRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.merge_button.setDefault(True)
        self.merge_button.clicked.connect(self._on_merge)
        self.replace_button.clicked.connect(self._on_replace)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh()

    def _matching_track(self, track) -> int:
        """The existing track on the same channel, or a new one when no channel matches."""
        for index, current in enumerate(self._tracks):
            if current.channel == track.channel:
                return index
        return -1

    def _refresh(self) -> None:
        fresh = sum(1 for combo in self._targets if combo.currentData() == -1)
        room = len(self._tracks) + fresh <= TRACK_LIMIT
        self.merge_button.setEnabled(room)
        self.merge_button.setToolTip("" if room else f"Point the file at existing tracks to stay within {TRACK_LIMIT}")

    def _on_merge(self) -> None:
        self._mode = "merge"
        self.accept()

    def _on_replace(self) -> None:
        self._mode = "replace"
        self.accept()

    def mode(self) -> str:
        return self._mode

    def mapping(self) -> list[int]:
        """One target per file track: an index into the roll's tracks, or -1 for a new one."""
        return [combo.currentData() for combo in self._targets]
