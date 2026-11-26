# SPDX-License-Identifier: AGPL-3.0-only
"""The timing offset for a plain ASS export."""

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout

from namioto import settings as store
from namioto.i18n import tr
from namioto.ui.form import add_row, field_editor


class ExportDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Export ASS"))
        self._settings = store.clone(settings)
        field = store.FIELD_SPECS["ass", "offset_ms"]
        editor, self._read, _ = field_editor(settings.ass.offset_ms, field)
        layout = QFormLayout(self)
        add_row(layout, editor, field)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.addButton(tr("Export"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def values(self):
        store.set_value(self._settings, "ass", "offset_ms", self._read())
        return self._settings
