# SPDX-License-Identifier: AGPL-3.0-only
"""Small labels the bars and the roll share: note names and the transport's clock."""

from __future__ import annotations

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def note_name(pitch: int) -> str:
    return f"{NOTE_NAMES[pitch % 12]}{pitch // 12 - 1}"


def format_time(seconds: float) -> str:
    """Time as the transport shows it, mm:ss.mmm."""
    minutes, rest = divmod(max(0.0, seconds), 60.0)
    return f"{int(minutes):02d}:{rest:06.3f}"
