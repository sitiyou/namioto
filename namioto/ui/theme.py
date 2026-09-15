# SPDX-License-Identifier: AGPL-3.0-only
"""Where the colours live: a token set per theme, the stylesheet built from it, and the palette.

Qt draws most of the chrome from `stylesheet()`; the parts this app paints itself - the roll, the
ruler, the keyboard and the spectrum - read `canvas()`. Both come from the same theme name, which is
what lets a second theme be one more token set rather than a second drawing routine.
"""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication

ACCENT = "#3b9dff"

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


CANVASES: dict[str, Canvas] = {
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
QToolButton[checkable="true"]:checked { background: %ACCENT_SOFT%; border: 1px solid %ACCENT%; }
QToolButton:disabled { color: %DISABLED%; }
QToolButton#textButton { padding: 1px 6px; }
QToolButton#textButton:checked { background: %ACCENT_SOFT%; border: 1px solid %ACCENT%; }
QToolButton:focus { outline: none; border: 1px solid %ACCENT%; }
QSlider::groove:horizontal { height: 4px; background: %GRID_LINE%; border-radius: 2px; }
QSlider::sub-page:horizontal { background: %ACCENT%; border-radius: 2px; }
QSlider::handle:horizontal {
    width: 9px; height: 14px; margin: -5px 0; background: %TEXT%; border-radius: 2px;
}
QSlider::handle:horizontal:hover { background: %POSITION%; }
/* The spin boxes are left out on purpose: the style draws their arrows in colours meant for its own
   field, which are unreadable on ours. Styling the field and leaving the arrows to the style is not a
   middle ground - it is the worst of both. */
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
QToolBar::separator { background: %SEPARATOR%; width: 1px; margin: 4px 6px; }
"""


def stylesheet(theme: str = "dark", accent: str = ACCENT) -> str:
    """The application stylesheet, with the theme's tokens filled in."""
    sheet = _QSS
    for token, value in TOKENS[theme].items():
        sheet = sheet.replace(f"%{token}%", value)
    return sheet.replace("%ACCENT%", accent).replace("%ACCENT_SOFT%", _accent_soft(accent))


def _accent_soft(accent: str) -> str:
    colour = QColor(accent)
    return f"rgba({colour.red()}, {colour.green()}, {colour.blue()}, 40)"


def palette(theme: str = "dark", accent: str = ACCENT) -> QPalette:
    """What the widgets Qt draws without asking us - native dialogs, menus - take their colours from."""
    tokens = TOKENS[theme]
    result = QPalette()
    for role, colour in (
        (QPalette.ColorRole.Window, tokens["PANEL"]),
        (QPalette.ColorRole.WindowText, tokens["TEXT"]),
        (QPalette.ColorRole.Base, tokens["BG"]),
        (QPalette.ColorRole.AlternateBase, tokens["PANEL"]),
        (QPalette.ColorRole.Text, tokens["TEXT"]),
        (QPalette.ColorRole.Button, tokens["BUTTON_BG"]),
        (QPalette.ColorRole.ButtonText, tokens["TEXT"]),
        (QPalette.ColorRole.Highlight, accent),
        (QPalette.ColorRole.HighlightedText, _ACCENT_TEXT),
        (QPalette.ColorRole.ToolTipBase, tokens["TOOLTIP_BG"]),
        (QPalette.ColorRole.ToolTipText, tokens["TOOLTIP_TEXT"]),
        (QPalette.ColorRole.PlaceholderText, tokens["PLACEHOLDER"]),
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


def canvas(theme: str = "dark") -> Canvas:
    """The colours the painted widgets use."""
    return CANVASES[theme]


def apply(app: QApplication, theme: str = "dark", accent: str = ACCENT) -> None:
    """Dress the whole application: the palette first, so nothing is drawn light, then the sheet."""
    app.setPalette(palette(theme, accent))
    app.setStyleSheet(stylesheet(theme, accent))
