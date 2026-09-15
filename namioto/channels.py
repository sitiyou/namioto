# SPDX-License-Identifier: AGPL-3.0-only
"""The channels a note plays on: one MIDI channel per entry, with the values it plays with.

Qt-free on purpose, like project.py and settings.py: the project file reads and writes these, the
players receive their playback values, and only the UI draws them. Notes never live here — they
carry the channel number and stay in one flat sequence.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

CHANNEL_COUNT = 16
COLOR_HEX = 7


@dataclass(frozen=True)
class Channel:
    """One MIDI channel of the document. An empty `color` lets the theme assign one."""

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
        return self.name or f"Channel {self.channel + 1}"


def set_field(channel: Channel, **fields) -> Channel:
    return replace(channel, **fields)


def arranged(channels) -> list[Channel]:
    """The channels in channel order, one entry per number: the first of a repeated number wins."""
    unique: dict[int, Channel] = {}
    for channel in channels:
        unique.setdefault(channel.channel, channel)
    return [unique[number] for number in sorted(unique)]


def free_channel(channels) -> int | None:
    """The lowest channel no entry plays on, or None when all sixteen are taken."""
    used = {channel.channel for channel in channels}
    return next((channel for channel in range(CHANNEL_COUNT) if channel not in used), None)


def audible(channels) -> list[int]:
    """The channel numbers that sound; the sidebar's mute is the only filter."""
    return [channel.channel for channel in channels if not channel.mute]


def valid_color(value) -> str:
    """A `#rrggbb` string, or "" for anything else."""
    if isinstance(value, str) and len(value) == COLOR_HEX and value[0] == "#":
        try:
            int(value[1:], 16)
        except ValueError:
            return ""
        return value.lower()
    return ""
