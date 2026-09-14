# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for the project file: what it holds, what it leaves to the settings, and what it refuses."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from namioto import project
from namioto import settings as store


def make(**values) -> project.Project:
    return project.Project(values=store.project_values(store.Settings()), **values)


def test_a_saved_project_can_be_read_back(tmp_path) -> None:
    path = tmp_path / "song.nto"
    saved = make(audio="vocal.wav", notes=(project.Note(0.73, 0.37, 63), project.Note(1.5, 2.0, 55)))
    project.save(saved, path)
    opened = project.load(path)
    assert opened.audio == "vocal.wav"
    assert opened.notes == (project.Note(0.73, 0.37, 63), project.Note(1.5, 2.0, 55))
    assert opened.values == saved.values


def test_the_file_is_text_with_a_name_and_a_version(tmp_path) -> None:
    path = tmp_path / "song.nto"
    project.save(make(), path)
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    data = json.loads(text)
    assert (data["format"], data["version"]) == (project.FORMAT, project.VERSION)
    assert data["notes"] == []


def test_only_the_values_that_belong_to_the_document_are_written() -> None:
    settings = store.Settings()
    settings.playback.backend = "external"  # the machine's business
    settings.playback.midi_port = "TiMidity:0"
    settings.tempo.estimator = "tempocnn"
    settings.extraction.model_size = "large"
    settings.editor.start_in_edit_mode = True
    written = project.to_dict(project.Project(values=store.project_values(settings)))
    assert "backend" not in json.dumps(written)
    assert set(written) == {
        "format",
        "version",
        "audio",
        "notes",
        "analysis",
        "spectrum",
        "playback",
        "editor",
        "tempo",
        "session",
    }
    assert set(written["playback"]) == {"velocity", "program", "audio_volume", "midi_volume", "speed"}
    assert set(written["session"]) == {"center_x", "center_y"}


def test_the_machine_values_in_a_file_are_ignored(tmp_path) -> None:
    path = tmp_path / "song.nto"
    path.write_text(
        json.dumps(
            {
                "format": "namioto",
                "version": 1,
                "playback": {"backend": "external", "midi_port": "TiMidity:0", "velocity": 64},
                "extraction": {"model_size": "large"},
                "notes": [],
            }
        )
    )
    opened = project.load(path)
    assert opened.values["playback"]["velocity"] == 64
    assert "backend" not in opened.values["playback"]
    assert "extraction" not in opened.values


def test_an_empty_or_partial_file_still_gives_every_value(tmp_path) -> None:
    path = tmp_path / "song.nto"
    path.write_text(json.dumps({"format": "namioto", "tempo": {"bpm": 93.0}}))
    opened = project.load(path)
    assert opened.values["tempo"]["bpm"] == 93.0
    assert opened.values["analysis"]["a4"] == 440.0
    assert opened.notes == ()


def test_a_value_out_of_range_is_brought_back_in_line(tmp_path) -> None:
    path = tmp_path / "song.nto"
    path.write_text(
        json.dumps(
            {
                "format": "namioto",
                "tempo": {"bpm": 9999.0},
                "analysis": {"channels": "middle", "a4": "high"},
                "playback": {"program": True, "velocity": 1000},
            }
        )
    )
    opened = project.load(path)
    assert opened.values["tempo"]["bpm"] == 300.0
    assert opened.values["analysis"]["channels"] == "mono"
    assert opened.values["analysis"]["a4"] == 440.0
    assert opened.values["playback"]["program"] == 0
    assert opened.values["playback"]["velocity"] == 127


def test_another_json_file_is_refused() -> None:
    with pytest.raises(ValueError, match="not a namioto project"):
        project.from_dict(store.to_dict(store.Settings()))
    with pytest.raises(ValueError, match="not a namioto project"):
        project.from_dict([1, 2, 3])


def test_a_file_that_is_not_json_is_refused(tmp_path) -> None:
    path = tmp_path / "song.nto"
    path.write_text("this is not json")
    with pytest.raises(ValueError):
        project.load(path)
    with pytest.raises(OSError):
        project.load(tmp_path / "missing.nto")


def test_a_file_from_a_later_version_is_still_read(tmp_path) -> None:
    path = tmp_path / "song.nto"
    path.write_text(json.dumps({"format": "namioto", "version": 99, "tempo": {"bpm": 100.0}}))
    assert project.load(path).values["tempo"]["bpm"] == 100.0


def test_notes_are_rounded_to_a_tenth_of_a_millisecond() -> None:
    written = project.to_dict(make(notes=(project.Note(0.7300000001, 0.37000001, 63),)))
    assert written["notes"] == [{"start": 0.73, "duration": 0.37, "pitch": 63}]


def test_a_note_that_makes_no_sense_is_left_out(recwarn) -> None:
    data = {
        "format": "namioto",
        "notes": [
            {"start": 1.0, "duration": 1.0, "pitch": 60},
            {"start": -2.0, "duration": 1.0, "pitch": 60.4},  # brought back in line, not dropped
            {"start": 0.0, "duration": 0.0, "pitch": 60},
            {"start": 0.0, "duration": -1.0, "pitch": 60},
            {"start": "x", "duration": 1.0, "pitch": 60},
            {"start": 0.0, "duration": 1.0},
            {"start": 0.0, "duration": 1.0, "pitch": True},
            {"start": float("inf"), "duration": 1.0, "pitch": 60},
            [1.0, 1.0, 60],
        ],
    }
    assert project.from_dict(data).notes == (project.Note(1.0, 1.0, 60), project.Note(0.0, 1.0, 60))
    assert "7 of the notes" in str(recwarn[0].message)


def test_notes_that_are_not_a_list_leave_an_empty_project() -> None:
    assert project.from_dict({"format": "namioto", "notes": "lots"}).notes == ()


def test_the_audio_is_stored_beside_the_project_when_it_can_be(tmp_path) -> None:
    (tmp_path / "song.wav").write_bytes(b"")
    assert project.store_audio(tmp_path / "song.nto", tmp_path / "song.wav") == "song.wav"
    assert project.store_audio(tmp_path / "song.nto", str(tmp_path / "song.wav")) == "song.wav"
    assert project.store_audio(tmp_path / "song.nto", "/elsewhere/song.wav") == "/elsewhere/song.wav"
    assert project.store_audio(tmp_path / "song.nto", None) == ""
    assert project.store_audio(tmp_path / "song.nto", "") == ""


def test_the_audio_is_found_next_to_the_project_first(tmp_path) -> None:
    assert project.resolve_audio(tmp_path / "song.nto", "song.wav") == tmp_path / "song.wav"
    assert project.resolve_audio(tmp_path / "song.nto", "/elsewhere/song.wav") == Path("/elsewhere/song.wav")
    assert project.resolve_audio(tmp_path / "song.nto", "~/song.wav") == Path.home() / "song.wav"
    assert project.resolve_audio(tmp_path / "song.nto", "") is None


def test_the_suffix_says_whether_a_file_is_one_of_ours() -> None:
    assert project.SUFFIX == ".nto"
    assert project.looks_like_project("song.nto")
    assert project.looks_like_project(Path("SONG.NTO"))
    assert not project.looks_like_project("song.wav")
    assert not project.looks_like_project("song")


def test_saving_leaves_no_half_written_file_behind(tmp_path) -> None:
    path = tmp_path / "song.nto"
    project.save(make(notes=(project.Note(1.0, 1.0, 60),)), path)
    project.save(make(notes=(project.Note(2.0, 1.0, 62),)), path)
    assert [item.name for item in tmp_path.iterdir()] == ["song.nto"]
    assert project.load(path).notes == (project.Note(2.0, 1.0, 62),)
