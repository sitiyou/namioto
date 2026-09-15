# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for the track model and how it rides in the project file."""

from __future__ import annotations

import json

from namioto import project
from namioto import settings as store
from namioto.tracks import (
    DRUM_CHANNEL,
    TRACK_LIMIT,
    Track,
    audible,
    channel_of,
    set_field,
    valid_color,
)


def make(**values) -> project.Project:
    return project.Project(values=store.project_values(store.Settings()), **values)


def test_the_channel_of_a_track_skips_the_drum_channel() -> None:
    assert channel_of(0) == 0
    assert channel_of(8) == 8
    assert channel_of(9) == 10
    assert channel_of(TRACK_LIMIT - 1) == 15
    assert DRUM_CHANNEL not in {channel_of(index) for index in range(TRACK_LIMIT)}


def test_audible_leaves_out_the_muted_tracks() -> None:
    tracks = (Track(), Track(mute=True), Track())
    assert audible(tracks) == [0, 2]


def test_set_field_returns_a_changed_copy() -> None:
    track = Track(name="Bass", program=32)
    changed = set_field(track, program=33, mute=True)
    assert changed.program == 33 and changed.mute
    assert track.program == 32 and not track.mute


def test_a_colour_must_be_a_six_digit_hex() -> None:
    assert valid_color("#ff2f2f") == "#ff2f2f"
    assert valid_color("#FF2F2F") == "#ff2f2f"
    for broken in ("", "ff2f2f", "#ff2f2", "#ff2f2fx", "#gggggg", 123456):
        assert valid_color(broken) == ""


def test_a_v1_file_without_tracks_gets_one_default_track() -> None:
    data = {
        "format": "namioto",
        "version": 1,
        "notes": [{"start": 0.0, "duration": 1.0, "pitch": 60}],
    }
    opened = project.from_dict(data)
    assert len(opened.tracks) == 1
    assert opened.tracks[0].program == 0
    assert opened.notes == (project.Note(0.0, 1.0, 60, 0),)


def test_tracks_and_note_tracks_survive_a_round_trip(tmp_path) -> None:
    path = tmp_path / "song.nto"
    tracks = (Track(name="Vocal", color="#ff2f2f"), Track(name="Bass", program=32, volume=90, mute=True))
    notes = (project.Note(0.0, 1.0, 60, 0), project.Note(1.0, 1.0, 43, 1))
    project.save(make(tracks=tracks, notes=notes), path)
    opened = project.load(path)
    assert opened.tracks == tracks
    assert opened.notes == notes


def test_a_track_index_past_the_list_lands_on_the_last_track() -> None:
    data = {
        "format": "namioto",
        "tracks": [{"name": "Only"}],
        "notes": [{"start": 0.0, "duration": 1.0, "pitch": 60, "track": 7}],
    }
    assert project.from_dict(data).notes == (project.Note(0.0, 1.0, 60, 0),)


def test_a_broken_track_falls_back_to_its_defaults() -> None:
    data = {
        "format": "namioto",
        "tracks": [
            {"name": 12, "color": "nope", "program": 999, "volume": "loud", "mute": "yes", "visible": "no", "lock": 3},
        ],
    }
    opened = project.from_dict(data)
    assert opened.tracks == (Track(),)


def test_the_track_list_is_capped() -> None:
    data = {"format": "namioto", "tracks": [{"name": f"T{i}"} for i in range(TRACK_LIMIT + 10)]}
    assert len(project.from_dict(data).tracks) == TRACK_LIMIT


def test_the_written_file_carries_the_tracks(tmp_path) -> None:
    path = tmp_path / "song.nto"
    project.save(make(tracks=(Track(name="Vocal", color="#ff2f2f", program=4),)), path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["tracks"] == [
        {
            "name": "Vocal",
            "color": "#ff2f2f",
            "program": 4,
            "volume": 100,
            "mute": False,
            "visible": True,
            "lock": False,
        }
    ]
    assert data["notes"] == []
