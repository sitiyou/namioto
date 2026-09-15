# SPDX-License-Identifier: AGPL-3.0-only
"""The settings window, and the store that keeps the file in step with what is running.

The window is built from `namioto.settings`: one row per field of the spec, so a new setting is a
line in that table and nothing here. A field the spec marks `hidden` is what the program remembers
by itself - a bar value, the session - and never a row.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from PyQt6.QtCore import QObject, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from namioto import settings as store
from namioto.settings import Field
from namioto.ui.controls import text_button

SAVE_DELAY_MS = 1000
EDITOR_WIDTH = 300  # a form of numbers that stretch across the page is hard to read


class SettingsStore(QObject):
    """The settings the program runs with, written out once the changes stop coming."""

    changed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, settings, path=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.path = path or store.default_path()
        # a project owns some of the values, so what goes to the file is not always what is on screen
        self.source = lambda: self.settings
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SAVE_DELAY_MS)
        self._timer.timeout.connect(self.flush)

    def touch(self) -> None:
        """Something changed, and more may follow: write it out when they settle."""
        self._timer.start()

    def apply(self, settings, save: bool = True) -> None:
        """Take whole settings over, at once, from the settings window or from an open project."""
        self.settings = settings
        self._timer.stop()  # anything still pending is older than what is being applied
        self.changed.emit(settings)
        if save:
            self.flush()

    def flush(self) -> None:
        self._timer.stop()
        try:
            store.save(self.source(), self.path)
        except OSError as error:  # a read-only home must not take the editor down
            self.failed.emit(f"Settings could not be saved: {error}")


def _read(widget: QWidget) -> Any:
    if isinstance(widget, QCheckBox):
        return widget.isChecked()
    if isinstance(widget, QDoubleSpinBox):  # before QSpinBox's sibling check, they do not nest
        return widget.value()
    if isinstance(widget, QSpinBox):
        return widget.value()
    if isinstance(widget, QComboBox):
        return widget.currentData()
    raise TypeError(f"no way to read a {type(widget).__name__}")


def _write(widget: QWidget, value: Any) -> None:
    if isinstance(widget, QCheckBox):
        widget.setChecked(bool(value))
    elif isinstance(widget, (QDoubleSpinBox, QSpinBox)):
        widget.setValue(value)
    elif isinstance(widget, QComboBox):
        widget.setCurrentIndex(max(0, widget.findData(value)))
    else:
        raise TypeError(f"no way to fill a {type(widget).__name__}")


def _pages() -> tuple[str, ...]:
    """The pages that still have a row: the rest of the spec is what the program remembers by itself."""
    return tuple(
        dict.fromkeys(section.page for section in store.SECTIONS if any(not item.hidden for item in section.fields))
    )


class SettingsDialog(QDialog):
    """Every setting, on a page per group, over a copy that is only handed over when applied."""

    applied = pyqtSignal(object)
    reanalyse_requested = pyqtSignal()

    def __init__(
        self,
        settings,
        *,
        can_reanalyse: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(560, 460)
        self._settings = store.clone(settings)
        self._rows: list[tuple[str, Field, Callable[[], Any], Callable[[Any], None]]] = []

        pages = QTabWidget()
        for page in _pages():
            pages.addTab(self._page(page, can_reanalyse), page)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply
        )
        restore = buttons.addButton("Restore defaults", QDialogButtonBox.ButtonRole.ResetRole)
        restore.clicked.connect(self.restore_defaults)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(pages)
        layout.addWidget(buttons)

    def values(self):
        """The copy, with everything the widgets hold read back into it."""
        for section, field, read, _write_value in self._rows:
            store.set_value(self._settings, section, field.name, read())
        return self._settings

    def apply(self) -> None:
        self.applied.emit(self.values())

    def restore_defaults(self) -> None:
        self._settings = store.Settings()
        for _section, field, _read_value, write in self._rows:
            write(field.default)

    def _accept(self) -> None:
        self.apply()
        self.accept()

    def _page(self, page: str, can_reanalyse: bool) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        for advanced in (False, True):
            rows = [
                (section, field)
                for section in store.SECTIONS
                if section.page == page
                for field in section.fields
                if field.advanced is advanced and not field.hidden
            ]
            if not rows:
                continue
            if advanced:
                caption = QLabel("ADVANCED")
                font = QFont()
                font.setPixelSize(10)
                font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
                caption.setFont(font)
                layout.addWidget(caption)
            form = QFormLayout()
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            for section, field in rows:
                editor, read, write = self._editor(section.name, field)
                editor.setMaximumWidth(EDITOR_WIDTH)
                self._rows.append((section.name, field, read, write))
                label = QLabel(field.caption)
                if field.tooltip:
                    label.setToolTip(field.tooltip)
                    editor.setToolTip(field.tooltip)
                form.addRow(label, editor)
            layout.addLayout(form)
        if page == "Analysis":
            layout.addWidget(self._analysis_hint(can_reanalyse))
        if page == "Advanced":
            layout.addWidget(self._path_hint())
        layout.addStretch(1)
        return widget

    def _analysis_hint(self, can_reanalyse: bool) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("Analysis changes reach the spectrum the next time a file is loaded.")
        layout.addWidget(hint)
        if can_reanalyse:
            again = text_button("Re-analyse now", "Run the analysis again with these settings")
            again.clicked.connect(self.reanalyse_requested)
            layout.addWidget(again)
        layout.addStretch(1)
        return widget

    def _path_hint(self) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("File"))
        path = QLineEdit(str(store.default_path()))
        path.setReadOnly(True)
        layout.addWidget(path, 1)
        folder = text_button("Show folder", "Open the directory holding the settings file")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(store.default_path().parent))))
        layout.addWidget(folder)
        return widget

    def _editor(self, section: str, field: Field) -> tuple[QWidget, Callable[[], Any], Callable[[Any], None]]:
        value = store.get_value(self._settings, section, field.name)
        if field.kind == "bool":
            widget = QCheckBox()
        elif field.kind == "choice":
            labels = field.labels or tuple(str(choice) for choice in field.choices)
            return self._combo(list(zip(labels, field.choices, strict=True)), value)
        elif field.kind == "int":
            widget = QSpinBox()
            widget.setRange(int(field.low), int(field.high))
            widget.setSingleStep(max(1, int(field.step) or 1))
            widget.setSuffix(field.suffix)
        elif field.kind == "float":
            widget = QDoubleSpinBox()
            widget.setRange(field.low, field.high)
            widget.setDecimals(field.decimals)
            widget.setSingleStep(field.step or 0.1)
            widget.setSuffix(field.suffix)
            widget.setKeyboardTracking(False)
        else:
            raise TypeError(f"no editor for a {field.kind} field")
        _write(widget, value)
        return widget, lambda widget=widget: _read(widget), lambda new, widget=widget: _write(widget, new)

    def _combo(self, entries: Sequence[tuple[str, Any]], value: Any):
        widget = QComboBox()
        for caption, data in entries:
            widget.addItem(caption, data)
        _write(widget, value)
        return widget, lambda widget=widget: _read(widget), lambda new, widget=widget: _write(widget, new)
