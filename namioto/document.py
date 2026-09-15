# SPDX-License-Identifier: AGPL-3.0-only
"""The score a session edits: the notes and the tracks they belong to.

Qt-free on purpose, like tracks.py and project.py: the roll draws it, the players and the project
file read it, and nothing here knows about a widget or a scene. Notes are timed in beats, the unit
the roll works in; the seconds a file or the audio uses are a conversion at that boundary, not a
second home for the data.
"""

from __future__ import annotations

from dataclasses import dataclass

from namioto.tracks import TRACK_LIMIT, Track
from namioto.tracks import set_field as track_set_field

PITCH_MIN = 21
PITCH_MAX = 108
PITCH_COUNT = PITCH_MAX - PITCH_MIN + 1
MIN_DURATION = 0.0625


@dataclass(eq=False)
class Note:
    """One note, in beats (the roll's scene unit) and semitone rows.

    Identity is the note itself, not its fields: two notes may sound and sit exactly alike, and a
    roll that looks one up by value would drop the wrong one.
    """

    pitch: int
    start: float
    duration: float
    track: int = 0

    def __post_init__(self) -> None:
        self.pitch = min(PITCH_MAX, max(PITCH_MIN, int(self.pitch)))
        self.start = max(0.0, float(self.start))
        self.duration = max(MIN_DURATION, float(self.duration))
        self.track = min(TRACK_LIMIT - 1, max(0, int(self.track)))

    @property
    def end(self) -> float:
        return self.start + self.duration

    def set_duration(self, duration: float) -> None:
        self.duration = max(MIN_DURATION, float(duration))

    def set_range(self, start: float, pitch: int) -> None:
        self.start = max(0.0, float(start))
        self.pitch = min(PITCH_MAX, max(PITCH_MIN, int(pitch)))


class Document:
    """The notes and the tracks of one score, with the invariants the roll relies on.

    It holds the data, not the drawing: the roll builds one item per note and reads the note back.
    """

    def __init__(self, tracks=None, notes=None):
        self.tracks: list[Track] = list(tracks) if tracks else [Track(name="Track 1")]
        self.notes: list[Note] = list(notes) if notes else []

    def add_note(self, note: Note) -> Note:
        self.notes.append(note)
        return note

    def remove_note(self, note: Note) -> None:
        self.notes.remove(note)

    def clear_notes(self) -> None:
        self.notes.clear()

    def replace_notes(self, notes) -> None:
        self.notes = list(notes)

    def set_tracks(self, tracks) -> None:
        """Replace the track list; the notes on tracks that go away are dropped with them."""
        self.tracks = list(tracks) or [Track(name="Track 1")]
        last = len(self.tracks) - 1
        self.notes = [note for note in self.notes if note.track <= last]

    def add_track(self, track: Track) -> None:
        self.tracks.append(track)

    def remove_track(self, index: int) -> list[Note] | None:
        """Drop a track and the notes on it and shift the ones after it down; None when it is the
        last, which stays."""
        if len(self.tracks) <= 1:
            return None
        self.tracks.pop(index)
        removed = [note for note in self.notes if note.track == index]
        self.notes = [note for note in self.notes if note.track != index]
        for note in self.notes:
            if note.track > index:
                note.track -= 1
        return removed

    def set_track_field(self, index: int, **fields) -> None:
        self.tracks[index] = track_set_field(self.tracks[index], **fields)
