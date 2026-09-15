# SPDX-License-Identifier: AGPL-3.0-only
"""Note tracks: the metadata a note's `track` index points at.

Qt-free on purpose, like project.py and settings.py: the project file reads and writes these, the
players receive their playback values, and only the UI draws them. Notes never live here — they
carry the track index and stay in one flat sequence.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

DRUM_CHANNEL = 9
TRACK_LIMIT = 15  # one melodic MIDI channel per track: 0-15 without the drum channel
COLOR_HEX = 7


@dataclass(frozen=True)
class Track:
    """One track of the document. An empty `color` lets the theme assign one."""

    name: str = ""
    color: str = ""
    program: int = 0
    volume: int = 100  # 0-127, 100 being unity
    mute: bool = False
    visible: bool = True
    lock: bool = False

    @property
    def label(self) -> str:
        return self.name or "Track"


def default_track(index: int, program: int = 0) -> Track:
    return Track(name=f"Track {index + 1}", program=program)


def set_field(track: Track, **fields) -> Track:
    return replace(track, **fields)


def channel_of(track: int) -> int:
    """The MIDI channel a track plays on: the drum channel is skipped for melodic instruments."""
    return track if track < DRUM_CHANNEL else track + 1


def audible(tracks) -> list[int]:
    """Indexes of the tracks that sound; the sidebar's mute is the only filter."""
    return [index for index, track in enumerate(tracks) if not track.mute]


def valid_color(value) -> str:
    """A `#rrggbb` string, or "" for anything else."""
    if isinstance(value, str) and len(value) == COLOR_HEX and value[0] == "#":
        try:
            int(value[1:], 16)
        except ValueError:
            return ""
        return value.lower()
    return ""
