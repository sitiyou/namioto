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
    "select": "mdi.selection",
    "edit": "mdi.pencil-ruler",
    "beat": "mdi.metronome",
    "page": "mdi.book-open-page-variant",
    "overtone": "mdi.sine-wave",
    "snap": "mdi.music-note-eighth",
    "check": "mdi.check",
    "cross": "mdi.close",
    "gear": "mdi.cog",
    "refresh": "mdi.refresh",
    "restore": "mdi.restore",
    "open": "mdi.folder-open",
    "save": "mdi.content-save",
    "export": "mdi.file-music-outline",
    "channels": "mdi.layers",
    "lock": "mdi.lock",
    "unlock": "mdi.lock-open-variant",
    "eye": "mdi.eye",
    "eyeoff": "mdi.eye-off",
    "mute": "mdi.volume-off",
    "sound": "mdi.volume-high",
}

def icon(kind: str, color: str = "") -> QIcon:
    """The glyph named by our own word for it, so a call site never spells out a font name.

    The disabled shade comes with it: a plain pixmap has no greyed mode of its own, so without it a
    button that is off looks exactly like one that is on.
    """
    return qta.icon(
        GLYPHS[kind],
        color=color or theme.TOKENS["dark"]["TEXT"],
        color_disabled=theme.TOKENS["dark"]["DISABLED"],
    )
