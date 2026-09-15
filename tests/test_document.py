# SPDX-License-Identifier: AGPL-3.0-only
"""The score model: note normalisation and the channel invariants, with no Qt in sight."""

from __future__ import annotations

from namioto.channels import Channel
from namioto.document import MIN_DURATION, PITCH_MAX, PITCH_MIN, Document, Note


def test_a_note_lands_inside_the_roll() -> None:
    note = Note(pitch=PITCH_MAX + 40, start=-3.0, duration=0.0, channel=99)
    assert note.pitch == PITCH_MAX
    assert note.start == 0.0
    assert note.duration == MIN_DURATION
    assert note.channel == 15
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


def test_removing_a_channel_takes_its_notes_and_leaves_the_numbers_alone() -> None:
    document = Document(
        channels=[Channel(name="A", channel=0), Channel(name="B", channel=1), Channel(name="C", channel=3)]
    )
    document.add_note(Note(60, 0.0, 1.0, channel=0))
    document.add_note(Note(62, 0.0, 1.0, channel=1))
    document.add_note(Note(64, 0.0, 1.0, channel=3))

    removed = document.remove_channel(1)
    assert [note.pitch for note in removed] == [62]
    assert [channel.name for channel in document.channels] == ["A", "C"]
    assert [(note.pitch, note.channel) for note in document.notes] == [(60, 0), (64, 3)]


def test_the_last_channel_stays() -> None:
    document = Document()
    assert document.remove_channel(0) is None
    assert len(document.channels) == 1


def test_a_note_on_a_channel_the_document_never_heard_of_gets_an_entry() -> None:
    document = Document(channels=[Channel(name="A")], notes=[Note(60, 0.0, 1.0, channel=4)])
    assert [channel.channel for channel in document.channels] == [0, 4]


def test_setting_channels_keeps_the_notes_on_the_channels_left_out() -> None:
    document = Document(channels=[Channel(name="A"), Channel(name="B", channel=5)])
    document.add_note(Note(60, 0.0, 1.0, channel=5))
    document.set_channels([Channel(name="A")])
    assert [channel.channel for channel in document.channels] == [0, 5]
    assert [(note.pitch, note.channel) for note in document.notes] == [(60, 5)]


def test_setting_a_channel_field_reaches_the_right_channel() -> None:
    document = Document(channels=[Channel(name="A"), Channel(name="B", channel=5)])
    document.set_channel_field(5, name="Lead", program=40)
    assert [(channel.channel, channel.name, channel.program) for channel in document.channels] == [
        (0, "A", 0),
        (5, "Lead", 40),
    ]
