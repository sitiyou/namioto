# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for the track model and how it rides in the project file."""

from __future__ import annotations

import json

from namioto import project
from namioto import settings as store
from namioto.tracks import (
    TRACK_LIMIT,
    Track,
    audible,
    default_track,
    free_channel,
    set_field,
    valid_color,
)


def make(**values) -> project.Project:
    return project.Project(values=store.project_values(store.Settings()), **values)


def test_a_track_keeps_the_channel_it_was_given() -> None:
    assert Track().channel == 0
    assert Track(channel=9).channel == 9  # the percussion channel is not special
    assert [default.channel for default in (default_track(0), default_track(15))] == [0, 15]


def test_a_free_channel_is_the_lowest_one_nobody_plays_on() -> None:
    assert free_channel(()) == 0
    assert free_channel((Track(channel=0), Track(channel=2))) == 1
    assert free_channel(tuple(Track(channel=channel) for channel in range(TRACK_LIMIT))) is None


def test_a_track_round_trips_through_the_project_file() -> None:
    track = Track(name="Drums", channel=9, program=0, volume=80)
    stored = project.to_dict(project.Project(tracks=(track,), notes=()))["tracks"][0]
    assert stored["channel"] == 9
    assert project.from_dict({"format": "namioto", "tracks": [stored]}).tracks[0] == track


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


def test_a_file_without_tracks_gets_one_default_track() -> None:
    data = {"format": "namioto", "notes": [{"start": 0.0, "duration": 1.0, "pitch": 60}]}
    opened = project.from_dict(data)
    assert opened.tracks == (Track(name="Track 1"),)
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
            "channel": 0,
            "program": 4,
            "volume": 100,
            "mute": False,
            "visible": True,
            "lock": False,
        }
    ]
    assert data["notes"] == []
