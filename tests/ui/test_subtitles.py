# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QDialog, QFileDialog, QSpinBox

from namioto import settings as store
from namioto.ui.export_dialog import ExportDialog


def test_edit_mode_ass_matches_its_persisted_krc_and_midi(subtitle_window, tmp_path):
    from namioto import subtitle

    window = subtitle_window
    assert not hasattr(window, "_lyric_mode")
    krc_path, midi_path = tmp_path / "current.krc", tmp_path / "current.mid"
    assert window.export_lyrics(tmp_path / "current.ass", save_krc=krc_path, save_midi=midi_path)
    assert window.export_krc(tmp_path / "separate.krc")
    assert window.export_midi(tmp_path / "separate.mid")
    assert krc_path.read_bytes() == (tmp_path / "separate.krc").read_bytes()
    assert midi_path.read_bytes() == (tmp_path / "separate.mid").read_bytes()
    expected = subtitle.build_ass(
        krc_path.read_text(),
        midi_path.read_bytes(),
        settings=window._export_settings(),
        wavetone=window.settings.midi.wavetone,
    )
    assert (tmp_path / "current.ass").read_text() == expected


def test_the_export_button_writes_an_ass_subtitle(subtitle_window, monkeypatch, tmp_path) -> None:
    own_window = subtitle_window

    def options(dialog):
        dialog.findChild(QSpinBox).setValue(1000)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ExportDialog, "exec", options)
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: (str(tmp_path / "exported"), "ASS subtitle (*.ass)")
    )
    before = own_window.view.notes()
    assert own_window._on_export_lyrics() is True
    target = tmp_path / "exported.ass"  # the export adds its own suffix
    text = target.read_text(encoding="utf-8")
    assert "[Script Info]" in text
    assert "Dialogue: 0," in text
    assert "Style: Default," in text
    assert "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,あい" in text
    assert own_window.view.notes() == before
    assert own_window.settings.ass.offset_ms == 1000
    assert store.load().ass.offset_ms == 1000
    assert own_window.project_path == tmp_path / "song.nto"  # an export leaves the document where it was


@pytest.mark.parametrize("stop", ("options", "path", "write", "build"))
def test_cancelled_or_failed_subtitle_export_does_not_remember_options(subtitle_window, monkeypatch, tmp_path, stop):
    original = store.to_dict(subtitle_window.settings)
    saved = store.default_path().read_bytes() if store.default_path().exists() else None
    seen = []

    def options(dialog):
        seen.append("options")
        dialog.findChild(QSpinBox).setValue(240)
        return QDialog.DialogCode.Rejected if stop == "options" else QDialog.DialogCode.Accepted

    def path(*args, **kwargs):
        seen.append("path")
        return ("" if stop == "path" else str(tmp_path / "out.ass"), "")

    def fail(*args, **kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(ExportDialog, "exec", options)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", path)
    if stop == "write":
        monkeypatch.setattr("namioto.utils.os.replace", fail)
    if stop == "build":

        def fail_build(*args, **kwargs):
            raise ValueError("invalid subtitle")

        monkeypatch.setattr("namioto.subtitle.build_song", fail_build)
    assert not subtitle_window._on_export_lyrics()
    assert seen == (["options"] if stop == "options" else ["options", "path"])
    assert store.to_dict(subtitle_window.settings) == original
    assert (store.default_path().read_bytes() if store.default_path().exists() else None) == saved
    assert not (tmp_path / "out.ass").exists()


def test_offset_dialog_edits_a_copy(own_window):
    dialog = ExportDialog(own_window.settings, own_window)
    dialog.findChild(QSpinBox).setValue(-250)
    assert dialog.values().ass.offset_ms == -250
    assert own_window.settings.ass.offset_ms == 0


def test_a_subtitle_needs_lyrics_and_notes(own_window) -> None:
    assert own_window._on_export_lyrics() is False
    assert "no lyrics" in own_window.statusBar().currentMessage()
    own_window.lyrics_text = "あ"
    own_window._load_sounds()  # the lyrics are readable; now the subtitle needs a note to sit on
    assert own_window._on_export_lyrics() is False
    assert "no notes" in own_window.statusBar().currentMessage()
