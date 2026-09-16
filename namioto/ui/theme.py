# SPDX-License-Identifier: AGPL-3.0-only
"""Where the colours this app paints itself live: the two canvases, and the palette that picks one.

The chrome is the widget style's: it draws every control, the dialogs' own buttons and the menus
included, from the palette the desktop hands out. The parts this app paints itself - the roll, the
ruler, the keyboard and the spectrum - read `canvas()`, and the set they get is the one that fits
the window the desktop dressed: the light one on a light palette, the dark one on a dark. A colour
there is named once, so the drawing code never spells one out.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtGui import QColor, QGuiApplication, QPalette, QStyleHints
from PyQt6.QtWidgets import QApplication, QStyleFactory

# One body colour per channel; the first equals the single note colour the editor had before channels.
NOTE_PALETTE = (
    "#ff2f2f",
    "#ff9d2f",
    "#ffd52f",
    "#b8e22f",
    "#4fe06a",
    "#2fe0c8",
    "#38b6ff",
    "#4d6bff",
    "#8a5cff",
    "#c25cff",
    "#ff5cd0",
    "#ff5c8a",
    "#ff7a5c",
    "#8fa3bf",
    "#d4a24f",
    "#7fe0a8",
)


@dataclass(frozen=True)
class Canvas:
    """The colours the painted widgets draw with."""

    background: QColor
    row_white: QColor
    row_black: QColor
    grid_line: QColor
    grid_beat: QColor
    grid_bar: QColor
    note_colors: tuple[QColor, ...]
    note_selected: QColor
    note_selected_edge: QColor
    text: QColor
    panel: QColor
    ruler_line: QColor
    spectrum_background: QColor
    spectrum_octave: QColor
    spectrum_beat: QColor
    spectrum_bar: QColor
    spectrum_hover_band: QColor
    hover_band: QColor
    hover_key: QColor
    playhead: QColor
    key_white: QColor
    key_black: QColor
    key_text: QColor
    key_line: QColor


CANVAS: dict[str, Canvas] = {
    "dark": Canvas(
        background=QColor("#191c23"),
        row_white=QColor("#262b34"),
        row_black=QColor("#20242c"),
        grid_line=QColor("#2f3541"),
        grid_beat=QColor("#434c5c"),
        grid_bar=QColor("#6d7a92"),
        note_colors=tuple(QColor(hex) for hex in NOTE_PALETTE),
        note_selected=QColor("#fecfcf"),
        note_selected_edge=QColor("#fe7474"),
        text=QColor("#b6c0ce"),
        panel=QColor("#20242c"),
        ruler_line=QColor("#3a4152"),
        spectrum_background=QColor("#000000"),
        spectrum_octave=QColor("#c0c0c0"),
        spectrum_beat=QColor("#606060"),
        spectrum_bar=QColor("#c0c0c0"),
        spectrum_hover_band=QColor(255, 255, 255, 85),
        hover_band=QColor(255, 255, 255, 85),
        hover_key=QColor("#ff4040"),
        playhead=QColor("#e6ecf5"),
        key_white=QColor("#d8dde6"),
        key_black=QColor("#15181e"),
        key_text=QColor("#454c5a"),
        key_line=QColor("#101318"),
    ),
    # The spectrum keeps its own dark canvas in both: it is the colour map of the analysis, and its
    # cells fade into what they are drawn on, so a light backdrop would wash the quiet frames out.
    "light": Canvas(
        background=QColor("#f4f5f8"),
        row_white=QColor("#ffffff"),
        row_black=QColor("#eceff4"),
        grid_line=QColor("#dde1e8"),
        grid_beat=QColor("#bcc4d1"),
        grid_bar=QColor("#8d97a7"),
        note_colors=tuple(QColor(hex) for hex in NOTE_PALETTE),
        note_selected=QColor("#fecfcf"),
        note_selected_edge=QColor("#fe7474"),
        text=QColor("#4d5563"),
        panel=QColor("#eceef3"),
        ruler_line=QColor("#c9cfd9"),
        spectrum_background=QColor("#000000"),
        spectrum_octave=QColor("#c0c0c0"),
        spectrum_beat=QColor("#606060"),
        spectrum_bar=QColor("#c0c0c0"),
        spectrum_hover_band=QColor(255, 255, 255, 85),
        hover_band=QColor(0, 0, 0, 26),
        hover_key=QColor("#ff4040"),
        playhead=QColor("#20252e"),
        key_white=QColor("#ffffff"),
        key_black=QColor("#23272f"),
        key_text=QColor("#6b7381"),
        key_line=QColor("#b9c0cc"),
    ),
}

# The canvas in force, as the name of one of the two above.
_current = "dark"

# The style the desktop handed out, kept because a choice of ours replaces it and "System default"
# has to be able to put it back.
_desktop_style = ""


def current() -> str:
    """Which of the two canvases is in force."""
    return _current


def canvas(name: str = "") -> Canvas:
    """The named canvas, or the one in force."""
    return CANVAS[name or _current]


def running_app() -> QApplication:
    """The application every window and every colour lives on."""
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    return app


def platform_style() -> str:
    """The style the desktop draws with, the one the setting leaves alone when it names none."""
    global _desktop_style
    if not _desktop_style:
        _desktop_style = running_app().style().objectName()
    return _desktop_style


def apply_style(name: str = "") -> None:
    """Draw the widgets with the named style, an empty name leaving the desktop's own in force.

    Qt hands a style's own palette over with it, so the palette in force before the switch is put
    back: the chosen style draws the controls, the desktop keeps the colours. A name this build has
    no style for is ignored, the way Qt ignores one it cannot find.
    """
    app = running_app()
    wanted = name or platform_style()
    if wanted == app.style().objectName():
        return
    style = QStyleFactory.create(wanted)
    if style is None:
        return
    palette = app.palette()
    app.setStyle(style)
    app.setPalette(palette)


def hints() -> QStyleHints:
    """Qt's platform hints; `colorSchemeChanged` is how a window hears the desktop switch its look."""
    found = QGuiApplication.styleHints()
    assert found is not None
    return found


def note_shades(body: QColor) -> tuple[QColor, QColor, QColor]:
    """A note body and the light and dark bevel edges painted around it.

    Two thirds of the way to white and to black keeps the first palette entry at the very shades
    the single-colour roll used to draw. Integer arithmetic, so the match is exact.
    """

    def shade(target: QColor) -> QColor:
        return QColor(
            body.red() + (target.red() - body.red()) * 2 // 3,
            body.green() + (target.green() - body.green()) * 2 // 3,
            body.blue() + (target.blue() - body.blue()) * 2 // 3,
        )

    return body, shade(QColor("#ffffff")), shade(QColor("#000000"))


def apply(app: QApplication, style: str = "") -> str:
    """Put the chosen widget style in force and say which canvas the desktop's palette asks for.

    There is no theme to pick: the window wears the colours the desktop dressed it in, and the roll
    under it is drawn light or dark to match, whichever palette that turns out to be.
    """
    global _current

    apply_style(style)
    _current = "light" if app.palette().color(QPalette.ColorRole.Window).lightness() > 127 else "dark"
    return _current
