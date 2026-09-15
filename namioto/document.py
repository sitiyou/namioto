# SPDX-License-Identifier: AGPL-3.0-only
"""The score a session edits: the notes and the MIDI channels they play on.

Qt-free on purpose, like channels.py and project.py: the roll draws it, the players and the project
file read it, and nothing here knows about a widget or a scene. Notes are timed in beats, the unit
the roll works in; the seconds a file or the audio uses are a conversion at that boundary, not a
second home for the data.
"""

from __future__ import annotations

from dataclasses import dataclass

from namioto.channels import CHANNEL_COUNT, Channel, arranged
from namioto.channels import set_field as channel_set_field

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
    channel: int = 0

    def __post_init__(self) -> None:
        self.pitch = min(PITCH_MAX, max(PITCH_MIN, int(self.pitch)))
        self.start = max(0.0, float(self.start))
        self.duration = max(MIN_DURATION, float(self.duration))
        self.channel = min(CHANNEL_COUNT - 1, max(0, int(self.channel)))

    @property
    def end(self) -> float:
        return self.start + self.duration

    def set_duration(self, duration: float) -> None:
        self.duration = max(MIN_DURATION, float(duration))

    def set_range(self, start: float, pitch: int) -> None:
        self.start = max(0.0, float(start))
        self.pitch = min(PITCH_MAX, max(PITCH_MIN, int(pitch)))


class Document:
    """The notes and the channels of one score, with the invariants the roll relies on.

    It holds the data, not the drawing: the roll builds one item per note and reads the note back.
    A channel's number is its identity, so removing one never renumbers the others - the notes that
    stay keep the MIDI channel they play on.
    """

    def __init__(self, channels=None, notes=None):
        self.channels: list[Channel] = arranged(channels or ()) or [Channel()]
        self.notes: list[Note] = list(notes) if notes else []
        self._fill_channels()

    def _fill_channels(self) -> None:
        """Every channel a note names gets an entry, so nothing draws against a missing one."""
        present = {channel.channel for channel in self.channels}
        for number in sorted({note.channel for note in self.notes} - present):
            self.channels.append(Channel(channel=number))
        self.channels.sort(key=lambda channel: channel.channel)

    def add_note(self, note: Note) -> Note:
        self.notes.append(note)
        self._fill_channels()
        return note

    def remove_note(self, note: Note) -> None:
        self.notes.remove(note)

    def clear_notes(self) -> None:
        self.notes.clear()

    def replace_notes(self, notes) -> None:
        self.notes = list(notes)
        self._fill_channels()

    def set_channels(self, channels) -> None:
        """Replace the channel list; a note whose channel is gone gets a plain entry back."""
        self.channels = arranged(channels or ()) or [Channel()]
        self._fill_channels()

    def add_channel(self, channel: Channel) -> None:
        self.channels = arranged([*self.channels, channel])

    def remove_channel(self, number: int) -> list[Note] | None:
        """Drop a channel and the notes on it, leaving the other numbers alone; None when it is the
        last, which stays."""
        if len(self.channels) <= 1:
            return None
        self.channels = [channel for channel in self.channels if channel.channel != number]
        removed = [note for note in self.notes if note.channel == number]
        self.notes = [note for note in self.notes if note.channel != number]
        return removed

    def set_channel_field(self, number: int, **fields) -> None:
        for index, channel in enumerate(self.channels):
            if channel.channel == number:
                self.channels[index] = channel_set_field(channel, **fields)
                return
