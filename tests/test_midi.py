# SPDX-License-Identifier: AGPL-3.0-only
"""Reading and writing MIDI files: the codec, its tempo walk and the WaveTone lead-in."""

from __future__ import annotations

from typing import Any

import mido
import pytest

from namioto import midi, project
from namioto.tracks import Track


def file_with(tracks, *, ppq: int = 480, tempo: int = 500000) -> mido.MidiFile:
    """A type 1 file with the conductor track every other one has."""
    mid = mido.MidiFile(type=1, ticks_per_beat=ppq, charset="utf-8")
    conductor = mido.MidiTrack()
    conductor.append(mido.MetaMessage("set_tempo", tempo=tempo, time=0))
    conductor.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    mid.tracks.append(conductor)
    for name, messages in tracks:
        track = mido.MidiTrack()
        if name:
            track.append(mido.MetaMessage("track_name", name=name, time=0))
        track.extend(messages)
        mid.tracks.append(track)
    return mid


def note(channel: int, pitch: int, start: int, end: int, velocity: int = 100) -> list:
    return [
        mido.Message("note_on", channel=channel, note=pitch, velocity=velocity, time=start),
        mido.Message("note_on", channel=channel, note=pitch, velocity=0, time=end - start),
    ]


def absolute(track) -> list[tuple[int, Any]]:
    """(tick, message) with the delta times added up, the way a reader has to."""
    tick, events = 0, []
    for message in track:
        tick += message.time
        events.append((tick, message))
    return events


def test_a_round_trip_keeps_the_notes_and_the_tracks(tmp_path) -> None:
    tracks = (
        Track(name="Piano", channel=0, program=4, volume=90),
        Track(name="Strings", channel=3, program=48, volume=127),
    )
    notes = (
        project.Note(1.0, 0.5, 60, 0),
        project.Note(1.25, 0.25, 64, 1),
        project.Note(2.0, 1.0, 67, 0),
    )
    imported = midi.read(midi.write(tmp_path / "out.mid", tracks, notes, 120.0))

    assert [(track.name, track.channel, track.program, track.volume) for track in imported.tracks] == [
        ("Piano", 0, 4, 90),
        ("Strings", 3, 48, 127),
    ]
    assert [(note.pitch, note.track) for note in imported.notes] == [(60, 0), (64, 1), (67, 0)]
    for written, back in zip(notes, imported.notes, strict=True):
        assert abs(written.start - back.start) < 0.002
        assert abs(written.duration - back.duration) < 0.002


def test_the_export_puts_every_note_where_it_belongs(tmp_path) -> None:
    """A note sits at its own tick, with nothing in front of it: WaveTone starts one bar late,
    and copying that would make our own round trips drift a bar."""
    tracks = (Track(name="Piano", channel=0),)
    notes = (project.Note(1.5, 0.5, 60, 0),)
    plain = midi.write(tmp_path / "plain.mid", tracks, notes, 120.0)
    events = absolute(mido.MidiFile(plain).tracks[1])
    # program change and volume, then the note 1.5 s in, which is 3 beats at 120 BPM over 960 ppq
    assert [tick for tick, message in events if not message.is_meta] == [0, 0, 2880, 3840]

    late = midi.write(tmp_path / "late.mid", tracks, notes, 120.0, wavetone=True)
    played = [tick for tick, message in absolute(mido.MidiFile(late).tracks[1]) if not message.is_meta]
    assert played == [
        0,
        0,
        2880 + midi.LEAD_IN_BEATS * midi.PPQ,
        3840 + midi.LEAD_IN_BEATS * midi.PPQ,
    ]
    assert midi.read(late, wavetone=True).notes[0].start == pytest.approx(1.5, abs=0.002)


def test_wavetone_files_lose_their_lead_in_only_when_asked(tmp_path) -> None:
    """WaveTone writes every note a bar (4/4) late: see a `vocal.mid` it exported."""
    path = tmp_path / "wavetone.mid"
    file_with([("", [*note(0, 60, 1920 + 480, 1920 + 960)])]).save(path)

    assert midi.read(path, wavetone=False).notes[0].start == pytest.approx(2.5)
    assert midi.read(path, wavetone=True).notes[0].start == pytest.approx(0.5)


def test_a_file_that_starts_at_once_keeps_its_notes(tmp_path) -> None:
    """Nothing may be lost to a bar that only a WaveTone file has in front of it."""
    path = tmp_path / "plain.mid"
    messages = [
        mido.Message("note_on", channel=0, note=60, velocity=100, time=0),
        mido.Message("note_on", channel=0, note=64, velocity=100, time=240),
        mido.Message("note_on", channel=0, note=60, velocity=0, time=240),
        mido.Message("note_on", channel=0, note=64, velocity=0, time=240),
    ]
    file_with([("", messages)]).save(path)

    imported = midi.read(path, wavetone=True)
    assert [(note.pitch, note.start) for note in imported.notes] == [(60, 0.0), (64, 0.25)]
    assert imported.dropped == 0


def test_exact_time_keeps_a_tenth_of_a_millisecond(tmp_path) -> None:
    notes = (project.Note(3.7001, 0.1234, 60, 0),)
    path = midi.write(tmp_path / "exact.mid", (Track(channel=0),), notes, 137.0, exact=True)
    imported = midi.read(path)

    assert imported.bpm == midi.EXACT_BPM
    assert imported.notes[0].start == pytest.approx(3.7001, abs=1e-9)
    assert imported.notes[0].duration == pytest.approx(0.1234, abs=1e-9)


def test_a_type_zero_file_is_split_by_channel(tmp_path) -> None:
    track = mido.MidiTrack()
    for channel, program, volume in ((0, 4, 90), (9, 0, 127)):
        track.append(mido.Message("program_change", channel=channel, program=program, time=0))
        track.append(mido.Message("control_change", channel=channel, control=7, value=volume, time=0))
    track.extend(note(0, 60, 0, 480))
    track.extend(note(9, 36, 480, 960))
    flat = mido.MidiFile(type=0, ticks_per_beat=480, charset="utf-8")
    flat.tracks.append(track)
    flat.save(tmp_path / "flat.mid")

    imported = midi.read(tmp_path / "flat.mid")
    # the percussion channel is a channel like any other
    assert [track.channel for track in imported.tracks] == [0, 9]
    assert [track.program for track in imported.tracks] == [4, 0]
    assert [track.volume for track in imported.tracks] == [90, 127]
    assert [(note.pitch, note.track) for note in imported.notes] == [(60, 0), (36, 1)]


def test_the_tempo_map_is_walked_and_the_first_tempo_is_the_grid(tmp_path) -> None:
    path = tmp_path / "tempo.mid"
    mid = file_with([("", [*note(0, 60, 480, 960)])])
    mid.tracks[0].insert(1, mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(60), time=480))
    mid.save(path)

    imported = midi.read(path)
    assert imported.bpm == 120.0
    assert imported.tempo_changes == 1
    # the first beat is 0.5 s at 120, the second a whole second at 60
    assert imported.notes[0].start == pytest.approx(0.5)
    assert imported.notes[0].duration == pytest.approx(1.0)


def test_a_note_that_never_ends_is_dropped(tmp_path) -> None:
    messages = [
        mido.Message("note_on", channel=0, note=60, velocity=100, time=0),
        mido.Message("note_on", channel=0, note=64, velocity=100, time=480),
        mido.Message("note_on", channel=0, note=60, velocity=0, time=0),
    ]
    path = tmp_path / "open.mid"
    file_with([("", messages)]).save(path)

    imported = midi.read(path)
    assert [note.pitch for note in imported.notes] == [60]
    assert imported.dropped == 1


def test_overlapping_notes_of_one_pitch_pair_in_order(tmp_path) -> None:
    messages = [
        mido.Message("note_on", channel=0, note=60, velocity=100, time=0),
        mido.Message("note_on", channel=0, note=60, velocity=90, time=480),
        mido.Message("note_on", channel=0, note=60, velocity=0, time=480),
        mido.Message("note_on", channel=0, note=60, velocity=0, time=480),
    ]
    path = tmp_path / "overlap.mid"
    file_with([("", messages)]).save(path)

    imported = midi.read(path)
    assert [(round(note.start, 3), round(note.duration, 3)) for note in imported.notes] == [
        (0.0, 1.0),
        (0.5, 1.0),
    ]


def test_channels_past_the_limit_are_left_out(tmp_path) -> None:
    messages = []
    for channel in range(4):
        messages.extend(note(channel, 60 + channel, channel, channel + 480))
    path = tmp_path / "many.mid"
    file_with([("", messages)]).save(path)

    imported = midi.read(path, limit=2)
    assert [track.channel for track in imported.tracks] == [0, 1]
    assert imported.left_out == (2, 3)
    assert len(imported.notes) == 2


def test_only_the_tracks_asked_for_are_written(tmp_path) -> None:
    tracks = (Track(name="One", channel=0), Track(name="Two", channel=1))
    notes = (project.Note(0.0, 0.5, 60, 0), project.Note(0.0, 0.5, 62, 1))
    path = midi.write(tmp_path / "some.mid", tracks, notes, 120.0, included=(1,))

    imported = midi.read(path)
    assert [track.name for track in imported.tracks] == ["Two"]
    assert [note.pitch for note in imported.notes] == [62]


def test_quantizing_rounds_the_notes_onto_the_grid(tmp_path) -> None:
    notes = (project.Note(0.12, 0.30, 60, 0),)  # 0.24 beats in, 0.84 beats out at 120 BPM
    path = midi.write(tmp_path / "snap.mid", (Track(channel=0),), notes, 120.0, quantize=0.25)

    back = midi.read(path).notes[0]
    assert back.start == pytest.approx(0.125, abs=0.002)
    assert back.duration == pytest.approx(0.25, abs=0.002)


def test_a_note_shorter_than_a_tick_still_has_one(tmp_path) -> None:
    path = midi.write(tmp_path / "tiny.mid", (Track(channel=0),), (project.Note(0.0, 0.00001, 60, 0),), 120.0)
    assert len(midi.read(path).notes) == 1


def test_a_track_name_that_is_not_ascii_survives(tmp_path) -> None:
    path = midi.write(
        tmp_path / "unicode.mid", (Track(name="钢琴", channel=0),), (project.Note(0.0, 0.5, 60, 0),), 120.0
    )
    assert midi.read(path).tracks[0].name == "钢琴"


def test_a_track_without_its_own_metadata_gets_the_plain_defaults(tmp_path) -> None:
    path = tmp_path / "plain.mid"
    file_with([("", [*note(2, 60, 0, 480)])]).save(path)

    imported = midi.read(path)
    assert imported.tracks[0].name == "Track 1"
    assert imported.tracks[0].program == 0
    assert imported.tracks[0].volume == midi.DEFAULT_VOLUME
    assert imported.tracks[0].channel == 2


def test_the_ticks_per_beat_of_the_file_are_respected(tmp_path) -> None:
    path = tmp_path / "ppq.mid"
    file_with([("", [*note(0, 60, 960, 1920)])], ppq=960).save(path)

    imported = midi.read(path)
    assert imported.notes[0].start == pytest.approx(0.5)
    assert imported.notes[0].duration == pytest.approx(0.5)


def test_a_file_that_is_not_midi_says_so(tmp_path) -> None:
    bogus = tmp_path / "bogus.mid"
    bogus.write_bytes(b"this is not a MIDI file, it is a note to self")
    with pytest.raises(OSError):
        midi.read(bogus)

    empty = tmp_path / "empty.mid"
    empty.write_bytes(b"")
    with pytest.raises((EOFError, OSError, ValueError)):
        midi.read(empty)
