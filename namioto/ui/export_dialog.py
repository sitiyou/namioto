# SPDX-License-Identifier: AGPL-3.0-only
"""Subtitle effects edited on a copy; primary colours belong to the project."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from namioto import settings as store
from namioto.i18n import tr
from namioto.karaoke import ExportSettings
from namioto.karaoke.templates import load_templates, read_preferences
from namioto.lyric_colors import DEFAULT_COLORS
from namioto.ui.colors import bind_swatch
from namioto.ui.form import add_row, field_editor

GROUPS = (
    ("Guide", ("cue", "cue_placement", "cue_direction", "cue_gap", "cue_size_ratio", "cue_color")),
    ("Ruby", ("show_ruby", "ruby_font", "ruby_ratio", "ruby_color", "ruby_offset", "border_furi")),
    ("Palette", ("use_project_colors", "overlay_color", "channel_colors")),
    ("Timing", ("offset_ms", "hold_ms", "lead_time_ms", "fade_in_ms", "fade_out_ms", "guide_dot_duration_ms")),
    (
        "Font and layout",
        (
            "layout",
            "font",
            "font_size",
            "spacing",
            "margin_h",
            "margin_v",
            "row_gap",
            "harmony_tracks",
            "harmony_ratio",
            "harmony_align",
        ),
    ),
    (
        "Colours and effects",
        (
            "base_outline_color",
            "overlay_outline_color",
            "overlay_blur_color",
            "base_blur_color",
            "border",
            "base_color",
            "feather",
            "pulse",
            "pulse_peak",
            "blur",
            "blur_scale",
        ),
    ),
)


class ExportDialog(QDialog):
    """The export form; accepting it does not change the program's saved preferences."""

    def __init__(self, settings, parent=None, *, lyric_colors=DEFAULT_COLORS):
        super().__init__(parent)
        self.setWindowTitle(tr("Export karaoke"))
        self._settings = store.clone(settings)
        self._lyric_colors = lyric_colors
        self.editors = {}
        self._readers = {}
        self._writers = {}
        self.automatic = {}
        self._swatches = {}
        fields = {field.name: field for section in store.SECTIONS if section.name == "ass" for field in section.fields}
        self.scripts = []
        self.registry = load_templates()
        self.template = QComboBox()
        self.template.addItem(tr("Default"), "default")
        self.format = QComboBox()
        self.fps = QSpinBox()
        self.fps.setRange(1, 240)
        self.fps.setValue(60)
        self.format.currentIndexChanged.connect(lambda: self.fps.setEnabled(self.format.currentData() == "hook"))
        self.custom_page = QWidget()
        self.custom_form = QFormLayout(self.custom_page)
        self.custom_values = {}
        self.custom_readers = {}
        self.active_id = "default"
        pages = self.pages = QTabWidget()
        for title, names in GROUPS:
            page = QWidget()
            form = QFormLayout(page)
            for name in names:
                field = fields[name]
                editor, read, write = field_editor(store.get_value(settings, "ass", name), field)
                self.editors[name] = editor
                self._readers[name] = read
                self._writers[name] = write
                row = self._colour_row(name, editor) if name.endswith("_color") else editor
                add_row(form, row, field)
                if isinstance(editor, QLineEdit):
                    editor.textChanged.connect(self._validate)
                elif isinstance(editor, QComboBox):
                    editor.currentIndexChanged.connect(self._validate)
                elif isinstance(editor, QCheckBox):
                    editor.toggled.connect(self._validate)
                elif isinstance(editor, (QSpinBox, QDoubleSpinBox)):
                    editor.valueChanged.connect(self._validate)
            pages.addTab(page, tr(title))
            if title == "Font and layout":
                pages.setCurrentWidget(page)

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.export_button = buttons.addButton(tr("Export"), QDialogButtonBox.ButtonRole.AcceptRole)
        restore = buttons.addButton(tr("Restore defaults"), QDialogButtonBox.ButtonRole.ResetRole)
        restore.clicked.connect(self.restore_defaults)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(self.template)
        load = QPushButton(tr("Load Python template"))
        load.clicked.connect(self._load_script)
        row.addWidget(load)
        layout.addLayout(row)
        options = QFormLayout()
        options.addRow(tr("Output format"), self.format)
        options.addRow(tr("Hook frame rate"), self.fps)
        layout.addLayout(options)
        layout.addWidget(pages)
        layout.addWidget(self.custom_page)
        layout.addWidget(self.hint)
        layout.addWidget(buttons)
        self.template.currentIndexChanged.connect(self._select)
        self._select()
        saved = read_preferences()
        errors = []
        for path in saved.get("scripts", []):
            try:
                self._add_script(path)
            except (OSError, ValueError) as error:
                errors.append(str(error))
        self.custom_values.update(saved.get("values", {}))
        selected = self.template.findData(saved.get("id", "default"))
        if selected >= 0:
            self.template.setCurrentIndex(selected)
        else:
            errors.append(tr("Missing template") + " " + str(saved.get("id")))
        index = self.format.findData(saved.get("format", "ass"))
        if index >= 0:
            self.format.setCurrentIndex(index)
        self.fps.setValue(saved.get("fps", 60))
        if errors:
            self.hint.setText("\n".join(errors))

    def preferences(self):
        return {
            "id": self.active_id,
            "scripts": self.scripts,
            "format": self.format.currentData(),
            "fps": self.fps.value(),
            "values": {**self.custom_values, self.active_id: self.parameters()},
        }

    def _load_script(self):
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Load trusted Python template"), "", tr("Python templates (*.py)")
        )
        if not path or path in self.scripts:
            return
        try:
            self._add_script(path)
            self.template.setCurrentIndex(self.template.count() - 1)
        except ValueError as error:
            self.hint.setText(str(error))

    def _add_script(self, path):
        registry = load_templates([*self.scripts, path])
        self.registry = registry
        self.scripts.append(path)
        for recipe in registry.templates.values():
            if self.template.findData(recipe.id) < 0:
                self.template.addItem(tr(recipe.title), recipe.id)

    def _select(self, *_):
        self.custom_values[self.active_id] = self.parameters()
        while self.custom_form.rowCount():
            self.custom_form.removeRow(0)
        self.custom_readers.clear()
        recipe = self.registry.get(self.template.currentData())
        self.active_id = recipe.id
        previous_format = self.format.currentData()
        self.format.clear()
        for fmt in recipe.formats:
            label = "ASS subtitle" if fmt == "ass" else "libplacebo shader"
            self.format.addItem(tr(label), fmt)
        if previous_format in recipe.formats:
            self.format.setCurrentIndex(self.format.findData(previous_format))
        custom = recipe.id != "default"
        self.pages.setVisible(not custom)
        self.custom_page.setVisible(custom)
        if custom:
            for field in recipe.parameters:
                value = self.custom_values.get(recipe.id, {}).get(field.name, field.default)
                editor, read, _ = field_editor(value, field)
                self.custom_readers[field.name] = read
                add_row(self.custom_form, editor, field)
        self._validate()

    def parameters(self):
        return {name: read() for name, read in self.custom_readers.items()}

    def _colour_row(self, name, editor):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(editor, 1)
        picker = QPushButton(tr("Choose colour"))
        picker.clicked.connect(lambda: self._choose_colour(editor))
        layout.addWidget(picker)
        self._swatches[name] = bind_swatch(editor, picker)
        if name in ("overlay_blur_color", "base_blur_color"):
            automatic = QCheckBox(tr("Automatic"))
            automatic.setChecked(not editor.text())
            editor.setEnabled(not automatic.isChecked())
            picker.setEnabled(not automatic.isChecked())
            automatic.toggled.connect(lambda checked: editor.setEnabled(not checked))
            automatic.toggled.connect(lambda checked: picker.setEnabled(not checked))
            automatic.toggled.connect(self._validate)
            self.automatic[name] = automatic
            layout.addWidget(automatic)
        return row

    def _choose_colour(self, editor):
        colour = QColorDialog.getColor(QColor("#" + editor.text().lstrip("#")), self)
        if colour.isValid():
            editor.setText(colour.name()[1:].upper())

    def values(self):
        for name, read in self._readers.items():
            value = read()
            if name in self.automatic and self.automatic[name].isChecked():
                value = ""
            store.set_value(self._settings, "ass", name, value)
        return self._settings

    def _validate(self, *_):
        from namioto.subtitle import validate_settings

        settings = ExportSettings(**vars(self.values().ass))
        if settings.use_project_colors:
            from dataclasses import replace

            settings = replace(
                settings,
                overlay_color=self._lyric_colors[0].lstrip("#"),
                channel_colors={i: c.lstrip("#") for i, c in enumerate(self._lyric_colors)},
            )
        for name in ("overlay_color", "channel_colors"):
            self.editors[name].setEnabled(not settings.use_project_colors)
        for name in ("ruby_font", "ruby_ratio", "ruby_color", "ruby_offset", "border_furi"):
            self.editors[name].setEnabled(settings.show_ruby)
        self.editors["row_gap"].setEnabled(settings.layout == "double")
        error = ""
        try:
            validate_settings(settings)
        except ValueError as exc:
            message = str(exc)
            if message.endswith(" must be a six-digit RGB colour"):
                field = next(
                    f
                    for section in store.SECTIONS
                    if section.name == "ass"
                    for f in section.fields
                    if f.name == message.split()[0]
                )
                error = tr("{field} must be a six-digit RGB colour", field=tr(field.caption))
            else:
                error = tr(message)
        if not error:
            for name, colour in zip(("overlay_blur_color", "base_blur_color"), settings.blur_colours(), strict=True):
                if self.automatic[name].isChecked():
                    editor = self.editors[name]
                    assert isinstance(editor, QLineEdit)
                    editor.blockSignals(True)
                    editor.setText(colour)
                    editor.blockSignals(False)
                    self._swatches[name]()
        self.hint.setText(error)
        self.export_button.setEnabled(not error)

    def restore_defaults(self):
        defaults = store.Settings()
        for name, write in self._writers.items():
            write(getattr(defaults.ass, name))
        for automatic in self.automatic.values():
            automatic.setChecked(True)
        if self.active_id != "default":
            self.custom_values.pop(self.active_id, None)
            self.custom_readers.clear()
            self._select()
        self._validate()

    def _accept(self):
        self._validate()
        if self.export_button.isEnabled():
            try:
                self.registry.get(self.active_id).resolve(self.parameters())
            except ValueError as error:
                self.hint.setText(str(error))
                return
            self.accept()
