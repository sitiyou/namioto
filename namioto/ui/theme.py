# SPDX-License-Identifier: AGPL-3.0-only
"""Where the colours live: two token sets, the stylesheet and palette built from them, and the
canvas the painted widgets draw with.

One of the two is in force at a time. Qt draws most of the chrome from `stylesheet()`; the parts this
app paints itself - the roll, the ruler, the keyboard and the spectrum - read `canvas()`. Both come
from the tokens below, so a colour is named once and the drawing code never spells one out. Which
set is in force is the `theme` setting: `apply` resolves it, tells the platform so that a style that
draws from the desktop's own light/dark state follows as well, and only then installs the colours.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QGuiApplication, QPalette, QStyleHints
from PyQt6.QtWidgets import QApplication

ACCENT = "#3b9dff"

SCHEMES = ("auto", "light", "dark")

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

TOKENS: dict[str, dict[str, str]] = {
    "dark": {
        "BG": "#191c23",
        "PANEL": "#20242c",
        "CARD": "#232833",
        "CARD_BORDER": "#2f3644",
        "SEPARATOR": "#3f4757",
        "GRID_LINE": "#2f3541",
        "TEXT": "#dbe2ee",
        "TEXT_DIM": "#b6c0ce",
        "PLACEHOLDER": "#98a4b8",
        "POSITION": "#eef2f8",
        "DISABLED": "#5c6474",
        "BUTTON_BG": "#262b34",
        "BUTTON_HOVER": "#2b3140",
        "BUTTON_PRESSED": "#313848",
        "FIELD_BG": "#1c2129",
        "FIELD_BORDER": "#3a4152",
        "FIELD_BORDER_HOVER": "#4a5468",
        "SCROLL_HANDLE": "#3a4152",
        "SCROLL_HANDLE_HOVER": "#4a5468",
        "MENU_BG": "#262b34",
        "MENU_BORDER": "#3a4152",
        "TAB_BG": "#262b34",
        "TAB_SELECTED_BG": "#1f3a5c",
        "TOOLTIP_BG": "#262b34",
        "TOOLTIP_TEXT": "#e6ecf5",
    },
    "light": {
        "BG": "#f4f5f8",
        "PANEL": "#eceef3",
        "CARD": "#ffffff",
        "CARD_BORDER": "#d7dbe3",
        "SEPARATOR": "#c9cfd9",
        "GRID_LINE": "#dde1e8",
        "TEXT": "#20252e",
        "TEXT_DIM": "#4d5563",
        "PLACEHOLDER": "#8b93a1",
        "POSITION": "#0d1117",
        "DISABLED": "#a6adb9",
        "BUTTON_BG": "#e9ecf1",
        "BUTTON_HOVER": "#dfe3ea",
        "BUTTON_PRESSED": "#d3d9e2",
        "FIELD_BG": "#ffffff",
        "FIELD_BORDER": "#c6cdd8",
        "FIELD_BORDER_HOVER": "#a3acba",
        "SCROLL_HANDLE": "#c3cad6",
        "SCROLL_HANDLE_HOVER": "#a8b1c0",
        "MENU_BG": "#ffffff",
        "MENU_BORDER": "#d7dbe3",
        "TAB_BG": "#e9ecf1",
        "TAB_SELECTED_BG": "#d3e3fb",
        "TOOLTIP_BG": "#ffffff",
        "TOOLTIP_TEXT": "#20252e",
    },
}

_ACCENT_TEXT = "#101318"


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
        hover_band=QColor(255, 255, 255, 85),
        hover_key=QColor("#ff4040"),
        playhead=QColor("#e6ecf5"),
        key_white=QColor("#d8dde6"),
        key_black=QColor("#15181e"),
        key_text=QColor("#454c5a"),
        key_line=QColor("#101318"),
    ),
    # The spectrum keeps its own dark canvas in both themes: it is the colour map of the analysis,
    # and cells fade into what they are drawn on, so a light one would wash the quiet frames out.
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
        hover_band=QColor(0, 0, 0, 26),
        hover_key=QColor("#ff4040"),
        playhead=QColor("#20252e"),
        key_white=QColor("#ffffff"),
        key_black=QColor("#23272f"),
        key_text=QColor("#6b7381"),
        key_line=QColor("#b9c0cc"),
    ),
}

_QSS = """
QMainWindow, QDialog, QStatusBar { background: %BG%; }
QWidget { color: %TEXT%; }
QWidget#cluster {
    background: %CARD%;
    border: 1px solid %CARD_BORDER%;
    border-radius: 6px;
}
QWidget#separator { background: %SEPARATOR%; }
QWidget#suggestion {
    background: %CARD%;
    border: 1px solid %CARD_BORDER%;
    border-radius: 6px;
}
QWidget#channelPanel { background: %PANEL%; border-right: 1px solid %CARD_BORDER%; }
QWidget#channelCard { background: %CARD%; border: 1px solid %CARD_BORDER%; border-radius: 6px; }
QWidget#channelCard[active="true"] { background: %ACCENT_SOFT%; border-color: %ACCENT%; }
QWidget#corner { background: %PANEL%; }
QWidget#rubber { background: %ACCENT_SOFT%; border: 1px solid %ACCENT%; }
QLabel { color: %TEXT_DIM%; background: transparent; }
QLabel#sliderValue, QLabel#cursorNote { color: %TEXT%; }
QLabel#position { color: %POSITION%; font-size: 13px; }
/* A button that does something carries a frame, the one the dialogs' own buttons wear. The switches
   stay bare - a frame would compete with the fill they take when on - and so does the transport,
   where five frames in a row weigh more than the playback they stand for. */
QToolButton {
    color: %TEXT%;
    background: %BUTTON_BG%;
    border: 1px solid %FIELD_BORDER%;
    border-radius: 4px;
    padding: 2px;
}
QToolButton:hover { background: %BUTTON_HOVER%; }
QToolButton:pressed { background: %BUTTON_PRESSED%; }
QToolButton[checkable="true"], QToolButton#playbackButton {
    background: transparent;
    border: 1px solid transparent;
}
QToolButton[checkable="true"]:hover, QToolButton#playbackButton:hover { background: %BUTTON_HOVER%; }
QToolButton[checkable="true"]:pressed, QToolButton#playbackButton:pressed { background: %BUTTON_PRESSED%; }
QToolButton[checkable="true"]:checked { background: %ACCENT_SOFT%; border: 1px solid %ACCENT%; }
QToolButton:disabled { color: %DISABLED%; }
QToolButton#textButton { padding: 1px 6px; }
QToolButton:focus { outline: none; border: 1px solid %ACCENT%; }
/* The advanced rows fold away under their own heading: bare, so the section reads as a heading, with
   the disclosure arrow saying it opens. */
QToolButton#sectionHeader { font-weight: 600; }
QToolButton#sectionHeader:checked { background: transparent; border: 1px solid transparent; }
QSlider::groove:horizontal { height: 4px; background: %GRID_LINE%; border-radius: 2px; }
QSlider::sub-page:horizontal { background: %ACCENT%; border-radius: 2px; }
QSlider::handle:horizontal {
    width: 9px; height: 14px; margin: -5px 0; background: %TEXT%; border-radius: 2px;
}
QSlider::handle:horizontal:hover { background: %POSITION%; }
/* The spin boxes are left out on purpose: the style draws their arrows in colours meant for its own
   field, which are unreadable on ours. Styling the field and leaving the arrows to the style is not a
   middle ground - it is the worst of both. `apply` tells the desktop's own style which way the window
   is dressed instead, which is what keeps a spin box drawn by it in step with the rest. */
QComboBox, QLineEdit {
    background: %FIELD_BG%;
    color: %TEXT%;
    border: 1px solid %FIELD_BORDER%;
    border-radius: 3px;
    padding: 1px 4px;
}
QComboBox:hover, QLineEdit:hover { border-color: %FIELD_BORDER_HOVER%; }
QComboBox:focus, QLineEdit:focus { border-color: %ACCENT%; }
QComboBox:disabled { color: %DISABLED%; }
QComboBox QAbstractItemView {
    background: %MENU_BG%;
    color: %TEXT%;
    border: 1px solid %MENU_BORDER%;
    padding: 2px;
    selection-background-color: %ACCENT_SOFT%;
    selection-color: %POSITION%;
    outline: none;
}
QCheckBox { color: %TEXT%; spacing: 6px; }
QTabWidget::pane { border: 1px solid %CARD_BORDER%; }
QTabBar::tab { background: %TAB_BG%; color: %TEXT_DIM%; padding: 5px 11px; }
QTabBar::tab:selected { background: %TAB_SELECTED_BG%; color: %POSITION%; }
QScrollBar:horizontal, QScrollBar:vertical { background: %BG%; border: 0; }
QScrollBar:horizontal { height: 11px; }
QScrollBar:vertical { width: 11px; }
QScrollBar::handle:horizontal, QScrollBar::handle:vertical {
    background: %SCROLL_HANDLE%; border-radius: 5px;
}
QScrollBar::handle:horizontal { min-width: 24px; }
QScrollBar::handle:vertical { min-height: 24px; }
QScrollBar::handle:hover { background: %SCROLL_HANDLE_HOVER%; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QMenu { background: %MENU_BG%; color: %TEXT%; border: 1px solid %MENU_BORDER%; padding: 3px; }
QMenu::item { padding: 4px 18px; border-radius: 3px; }
QMenu::item:selected { background: %ACCENT_SOFT%; color: %POSITION%; }
QToolTip { background: %TOOLTIP_BG%; color: %TOOLTIP_TEXT%; border: 1px solid %MENU_BORDER%; padding: 3px; }
QStatusBar::item { border: 0; }
"""

# The theme in force, as the name of one of the two token sets above.
_current = "dark"


def current() -> str:
    """Which of the two is in force."""
    return _current


def tokens(name: str = "") -> dict[str, str]:
    """The named token set, or the one in force."""
    return TOKENS[name or _current]


def canvas(name: str = "") -> Canvas:
    """The named canvas, or the one in force."""
    return CANVAS[name or _current]


def running_app() -> QApplication:
    """The application every window and every colour lives on."""
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    return app


def hints() -> QStyleHints:
    """Qt's platform hints, the desktop's light or dark state among them."""
    found = QGuiApplication.styleHints()
    assert found is not None
    return found


def resolve(scheme: str) -> str:
    """Which of the two a setting asks for, `auto` meaning whichever the desktop is using.

    A desktop that does not say - the offscreen platform, mostly - gets the dark one, the way the
    editor looks when nothing tells it otherwise.
    """
    if scheme in ("light", "dark"):
        return scheme
    return "light" if hints().colorScheme() == Qt.ColorScheme.Light else "dark"


def stylesheet(scheme: str = "", accent: str = ACCENT) -> str:
    """The application stylesheet, with the tokens filled in."""
    sheet = _QSS
    for token, value in tokens(scheme).items():
        sheet = sheet.replace(f"%{token}%", value)
    return sheet.replace("%ACCENT%", accent).replace("%ACCENT_SOFT%", _accent_soft(accent))


def _accent_soft(accent: str) -> str:
    colour = QColor(accent)
    return f"rgba({colour.red()}, {colour.green()}, {colour.blue()}, 40)"


def palette(scheme: str = "", accent: str = ACCENT) -> QPalette:
    """What the widgets Qt draws without asking us - native dialogs, menus - take their colours from."""
    colours = tokens(scheme)
    result = QPalette()
    for role, colour in (
        (QPalette.ColorRole.Window, colours["PANEL"]),
        (QPalette.ColorRole.WindowText, colours["TEXT"]),
        (QPalette.ColorRole.Base, colours["FIELD_BG"]),
        (QPalette.ColorRole.AlternateBase, colours["PANEL"]),
        (QPalette.ColorRole.Text, colours["TEXT"]),
        (QPalette.ColorRole.Button, colours["BUTTON_BG"]),
        (QPalette.ColorRole.ButtonText, colours["TEXT"]),
        (QPalette.ColorRole.Highlight, accent),
        (QPalette.ColorRole.HighlightedText, _ACCENT_TEXT),
        (QPalette.ColorRole.ToolTipBase, colours["TOOLTIP_BG"]),
        (QPalette.ColorRole.ToolTipText, colours["TOOLTIP_TEXT"]),
        (QPalette.ColorRole.PlaceholderText, colours["PLACEHOLDER"]),
    ):
        result.setColor(role, QColor(colour))
    return result


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


def apply(app: QApplication, scheme: str = "auto", accent: str = ACCENT) -> str:
    """Dress the whole application and say which of the two is now in force.

    The desktop's own style paints the widgets the sheet leaves alone - spin boxes, dialog buttons -
    from the light or dark state *it* is told about, not from the palette, so the scheme goes out
    first; the palette follows it, so nothing that reads one is left over from the other; the sheet
    comes last, because it is what carries the colours the styles built into Qt do not have.
    """
    global _current

    hints().setColorScheme(
        {"auto": Qt.ColorScheme.Unknown, "light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}[scheme]
    )
    name = resolve(scheme)
    _current = name
    app.setPalette(palette(name, accent))
    app.setStyleSheet(stylesheet(name, accent))
    return name
