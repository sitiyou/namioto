# SPDX-License-Identifier: AGPL-3.0-only
"""The score model: note normalisation and the track invariants, with no Qt in sight."""

from __future__ import annotations

from namioto.document import MIN_DURATION, PITCH_MAX, PITCH_MIN, Document, Note
from namioto.tracks import Track


def test_a_note_lands_inside_the_roll() -> None:
    note = Note(pitch=PITCH_MAX + 40, start=-3.0, duration=0.0, track=99)
    assert note.pitch == PITCH_MAX
    assert note.start == 0.0
    assert note.duration == MIN_DURATION
    assert note.track == 15
    assert Note(pitch=PITCH_MIN - 40, start=0.0, duration=1.0).pitch == PITCH_MIN


def test_a_note_keeps_its_end_with_it() -> None:
    note = Note(60, 2.0, 1.5)
    assert note.end == 3.5
    note.set_range(4.0, 72)
    note.set_duration(0.5)
    assert (note.start, note.pitch, note.end) == (4.0, 72, 4.5)


def test_two_notes_that_sound_alike_are_still_two() -> None:
    first = Note(60, 1.0, 1.0)
    twin = Note(60, 1.0, 1.0)
    document = Document(notes=[first, twin])
    assert first is not twin
    document.remove_note(twin)
    assert len(document.notes) == 1 and document.notes[0] is first


def test_removing_a_track_takes_its_notes_and_shifts_the_rest() -> None:
    document = Document(tracks=[Track(name="A"), Track(name="B"), Track(name="C")])
    document.add_note(Note(60, 0.0, 1.0, track=0))
    document.add_note(Note(62, 0.0, 1.0, track=1))
    document.add_note(Note(64, 0.0, 1.0, track=2))

    removed = document.remove_track(1)
    assert [note.pitch for note in removed] == [62]
    assert [track.name for track in document.tracks] == ["A", "C"]
    assert [(note.pitch, note.track) for note in document.notes] == [(60, 0), (64, 1)]


def test_the_last_track_stays() -> None:
    document = Document()
    assert document.remove_track(0) is None
    assert len(document.tracks) == 1


def test_a_shorter_track_list_drops_the_notes_past_its_end() -> None:
    document = Document(tracks=[Track(name="A"), Track(name="B")], notes=[Note(60, 0.0, 1.0, track=1)])
    document.set_tracks([Track(name="A")])
    assert document.notes == []
    assert len(document.tracks) == 1
