# SPDX-License-Identifier: AGPL-3.0-only
"""Note tracks: the metadata a note's `track` index points at.

Qt-free on purpose, like project.py and settings.py: the project file reads and writes these, the
players receive their playback values, and only the UI draws them. Notes never live here — they
carry the track index and stay in one flat sequence.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

TRACK_LIMIT = 16  # one MIDI channel per track
COLOR_HEX = 7


@dataclass(frozen=True)
class Track:
    """One track of the document. An empty `color` lets the theme assign one."""

    name: str = ""
    color: str = ""
    channel: int = 0
    program: int = 0
    volume: int = 100  # 0-127, 100 being unity
    mute: bool = False
    visible: bool = True
    lock: bool = False

    @property
    def label(self) -> str:
        return self.name or "Track"


def default_track(index: int, program: int = 0) -> Track:
    return Track(name=f"Track {index + 1}", channel=index, program=program)


def set_field(track: Track, **fields) -> Track:
    return replace(track, **fields)


def free_channel(tracks) -> int | None:
    """The lowest channel no track plays on, or None when all sixteen are taken."""
    used = {track.channel for track in tracks}
    return next((channel for channel in range(TRACK_LIMIT) if channel not in used), None)


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
