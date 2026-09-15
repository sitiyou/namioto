# SPDX-License-Identifier: AGPL-3.0-only
"""Icons: one glyph font, so the bars and the settings window draw the same shapes."""

from __future__ import annotations

import qtawesome as qta
from PyQt6.QtGui import QIcon

from namioto.ui import theme

GLYPHS = {
    "rewind": "mdi.rewind",
    "forward": "mdi.fast-forward",
    "play": "mdi.play",
    "pause": "mdi.pause",
    "playstart": "mdi.skip-previous",
    "stop": "mdi.stop",
    "pen": "mdi.pen",
    "select": "mdi.cursor-default",
    "edit": "mdi.pencil-ruler",
    "beat": "mdi.metronome",
    "snap": "mdi.music-note-eighth",
    "check": "mdi.check",
    "cross": "mdi.close",
    "gear": "mdi.cog",
    "refresh": "mdi.refresh",
    "open": "mdi.folder-open",
    "save": "mdi.content-save",
    "tracks": "mdi.layers",
    "lock": "mdi.lock",
    "unlock": "mdi.lock-open-variant",
    "eye": "mdi.eye",
    "eyeoff": "mdi.eye-off",
    "mute": "mdi.volume-off",
    "sound": "mdi.volume-high",
}

SECTION_GLYPHS = {
    "analysis": "mdi.tune",
    "spectrum": "mdi.monitor",
    "playback": "mdi.music-note",
    "editor": "mdi.vector-square",
    "tempo": "mdi.metronome",
    "extraction": "mdi.robot",
    "paths": "mdi.folder-outline",
    "session": "mdi.restore",
}


def icon(kind: str, color: str = "") -> QIcon:
    """The glyph named by our own word for it, so a call site never spells out a font name."""
    return qta.icon(GLYPHS[kind], color=color or theme.TOKENS["dark"]["TEXT"])


def section_icon(name: str, color: str = "") -> QIcon:
    return qta.icon(SECTION_GLYPHS[name], color=color or theme.TOKENS["dark"]["TEXT"])
