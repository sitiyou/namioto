# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for the settings model: defaults, the file, and how a bad value is brought back in line."""

from __future__ import annotations

import json
from pathlib import Path

import platformdirs
import pytest

from namioto import settings as store
from namioto.game import models_root


@pytest.fixture
def settings_file(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "settings.json"
    monkeypatch.setenv("NAMIOTO_SETTINGS", str(path))
    return path


def test_missing_file_gives_the_defaults(settings_file) -> None:
    loaded = store.load()
    assert store.to_dict(loaded) == store.to_dict(store.Settings())
    assert store.get_value(loaded, "editor", "snap") == 0.5
    assert store.get_value(loaded, "tempo", "estimator") == "beats"


def test_the_default_path_is_the_config_directory(monkeypatch) -> None:
    monkeypatch.delenv("NAMIOTO_SETTINGS", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/namioto-config-probe")
    path = store.default_path()
    assert path.parent == Path(platformdirs.user_config_dir("namioto"))
    assert path.name == "settings.json"
    assert str(path).startswith("/tmp/namioto-config-probe")


def test_the_settings_do_not_live_with_the_models(settings_file) -> None:
    assert store.default_path() != models_root()
    assert not str(store.default_path()).startswith(str(models_root()))


def test_the_environment_variable_names_the_file(settings_file) -> None:
    assert store.default_path() == settings_file
    store.save(store.Settings())
    assert settings_file.is_file()


def test_a_round_trip_keeps_every_value(settings_file) -> None:
    original = store.Settings()
    store.set_value(original, "analysis", "channels", "both")
    store.set_value(original, "analysis", "t_num", 25.5)
    store.set_value(original, "analysis", "a4", 442.0)
    store.set_value(original, "spectrum", "dim_in_edit_mode", False)
    store.set_value(original, "playback", "midi_port", "129:0 TiMidity")
    store.set_value(original, "playback", "speed", 0.75)
    store.set_value(original, "editor", "zoom_y", 22.0)
    store.set_value(original, "tempo", "estimator", "tempocnn")
    store.set_value(original, "extraction", "quantize_subdivisions", 16)
    store.save(original)

    loaded = store.load()
    assert store.to_dict(loaded) == store.to_dict(original)
    assert json.loads(settings_file.read_text())["version"] == store.VERSION
    assert store.get_value(loaded, "spectrum", "dim_in_edit_mode") is False
    assert store.get_value(loaded, "tempo", "estimator") == "tempocnn"


def test_unknown_keys_are_dropped_and_missing_ones_default(settings_file) -> None:
    settings_file.write_text(json.dumps({"version": 1, "editor": {"snap": 0.25}, "nonesuch": {"a": 1}}))
    loaded = store.load()
    assert store.get_value(loaded, "editor", "snap") == 0.25
    assert store.get_value(loaded, "editor", "division") == "beats"
    assert not hasattr(loaded, "nonesuch")
    assert "nonesuch" not in store.to_dict(loaded)


def test_a_broken_file_is_reported_and_the_defaults_are_used(settings_file) -> None:
    settings_file.write_text('{"analysis": {"t_num": ')
    with pytest.warns(UserWarning):
        loaded = store.load()
    assert store.to_dict(loaded) == store.to_dict(store.Settings())

    settings_file.write_text("[1, 2, 3]")
    assert store.to_dict(store.load()) == store.to_dict(store.Settings())


def test_values_of_the_wrong_type_fall_back_one_by_one(settings_file) -> None:
    settings_file.write_text(
        json.dumps(
            {
                "analysis": {"t_num": "fast", "channels": 5, "a4": True},
                "spectrum": {"gain": None, "dim_in_edit_mode": "yes"},
                "editor": {"overtone_highlight": 0},
            }
        )
    )
    loaded = store.load()
    assert store.get_value(loaded, "analysis", "t_num") == 40.0
    assert store.get_value(loaded, "analysis", "channels") == "mono"
    assert store.get_value(loaded, "analysis", "a4") == 440.0
    assert store.get_value(loaded, "spectrum", "gain") == 240.0
    assert store.get_value(loaded, "spectrum", "dim_in_edit_mode") is True
    assert store.get_value(loaded, "editor", "overtone_highlight") is True


def test_values_out_of_range_are_brought_back_in(settings_file) -> None:
    settings_file.write_text(
        json.dumps(
            {
                "analysis": {"t_num": -5.0, "fft_points": 10**9},
                "playback": {"buffer_ms": 10**9, "velocity": 0, "latency_ms": 9999},
                "editor": {"zoom_x": 0.001, "snap": 99.0},
                "tempo": {"bpm": 1000.0},
                "extraction": {"quantize_subdivisions": 7},
            }
        )
    )
    loaded = store.load()
    assert store.get_value(loaded, "analysis", "t_num") == 1.0
    assert store.get_value(loaded, "analysis", "fft_points") == 32768
    assert store.get_value(loaded, "playback", "buffer_ms") == 1000
    assert store.get_value(loaded, "playback", "velocity") == 1
    assert store.get_value(loaded, "playback", "latency_ms") == 500
    assert store.get_value(loaded, "editor", "zoom_x") == 12.0
    assert store.get_value(loaded, "editor", "snap") == 4.0
    assert store.get_value(loaded, "tempo", "bpm") == 300.0
    assert store.get_value(loaded, "extraction", "quantize_subdivisions") == 4  # 7 is not a choice


def test_values_snap_to_the_step_they_are_shown_on(settings_file) -> None:
    settings_file.write_text(
        json.dumps(
            {
                "playback": {"speed": 1.234, "preview_seconds": 0.611},
                "spectrum": {"contrast": 1.234},
                "analysis": {"fft_points": 9000, "a4": 441.2},
            }
        )
    )
    loaded = store.load()
    assert store.get_value(loaded, "playback", "speed") == 1.25
    assert store.get_value(loaded, "playback", "preview_seconds") == 0.6
    assert store.get_value(loaded, "spectrum", "contrast") == 1.2
    assert store.get_value(loaded, "analysis", "fft_points") == 8960
    assert store.get_value(loaded, "analysis", "a4") == 441.0


def test_text_is_trimmed_and_bounded(settings_file) -> None:
    settings_file.write_text(
        json.dumps({"extraction": {"model_dir": "  /tmp/models  "}, "paths": {"last_audio_dir": "x" * 9999}})
    )
    loaded = store.load()
    assert store.get_value(loaded, "extraction", "model_dir") == "/tmp/models"
    assert len(store.get_value(loaded, "paths", "last_audio_dir")) == store.TEXT_LIMIT


def test_saving_leaves_nothing_half_written(settings_file) -> None:
    store.save(store.Settings())
    store.set_value(store.Settings(), "editor", "snap", 1.0)
    first = store.Settings()
    store.save(first)
    second = store.Settings()
    store.set_value(second, "editor", "snap", 2.0)
    store.save(second)

    assert [path.name for path in settings_file.parent.iterdir()] == ["settings.json"]
    assert store.get_value(store.load(), "editor", "snap") == 2.0


def test_a_clone_can_be_edited_without_touching_the_original() -> None:
    original = store.Settings()
    edited = store.clone(original)
    store.set_value(edited, "editor", "snap", 4.0)
    store.set_value(edited, "analysis", "channels", "side")
    assert store.get_value(original, "editor", "snap") == 0.5
    assert store.get_value(original, "analysis", "channels") == "mono"


def test_the_program_field_lists_the_general_midi_presets() -> None:
    spec = store.FIELD_SPECS[("playback", "program")]
    assert spec.kind == "choice"
    assert len(store.GM_PROGRAMS) == 128
    assert len(set(store.GM_PROGRAMS)) == 128  # one name per program, or the list cannot be picked from
    assert spec.choices == tuple(range(128))
    assert spec.labels == store.PROGRAM_LABELS
    assert store.GM_PROGRAMS[0] == "Acoustic Grand Piano"
    assert store.GM_PROGRAMS[40] == "Violin"
    assert store.GM_PROGRAMS[-1] == "Gunshot"
    assert store.PROGRAM_LABELS[0] == "0: Acoustic Grand Piano"
    assert store.PROGRAM_LABELS[40] == "40: Violin"
    assert store.PROGRAM_LABELS[-1] == "127: Gunshot"
    assert [label.split(":")[0] for label in store.PROGRAM_LABELS] == [str(index) for index in range(128)]


def test_a_program_that_is_not_a_number_falls_back(settings_file) -> None:
    settings_file.write_text(json.dumps({"playback": {"program": True}}))
    assert store.get_value(store.load(), "playback", "program") == 0
    settings_file.write_text(json.dumps({"playback": {"program": 200}}))
    assert store.get_value(store.load(), "playback", "program") == 0
    settings_file.write_text(json.dumps({"playback": {"program": 40}}))
    assert store.get_value(store.load(), "playback", "program") == 40


def test_a_caption_that_names_its_unit_does_not_repeat_it_in_the_field() -> None:
    for spec in store.FIELD_SPECS.values():
        unit = spec.suffix.strip()
        assert not unit or unit not in spec.caption, f"{spec.name} says its unit twice"


def test_every_spec_field_is_a_field_of_its_section() -> None:
    defaults = store.Settings()
    for section in store.SECTIONS:
        for item in section.fields:
            assert store.get_value(defaults, section.name, item.name) == item.default
            assert item.caption
            assert len(item.labels) in (0, len(item.choices)), item.name
            if item.kind in ("int", "float"):
                assert item.high > item.low, item.name  # a range nobody can be inside of
                assert item.low <= item.default <= item.high, item.name
    assert len(store.FIELD_SPECS) == sum(len(section.fields) for section in store.SECTIONS)


def test_the_pages_bring_every_field_to_the_dialog() -> None:
    shown = {(section.name, item.name) for section in store.SECTIONS for item in section.fields if not item.hidden}
    assert ("analysis", "channels") in shown
    assert ("extraction", "d3pm_steps") in shown
    assert ("session", "geometry") not in shown  # the window state is stored, never typed in
    assert {section.page for section in store.SECTIONS} >= {"Analysis", "Display", "Playback", "Editor", "Tempo"}
