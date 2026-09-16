# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for the two themes, the setting that picks between them and what follows a switch."""

from __future__ import annotations

import pytest
from PyQt6.QtGui import QColor, QIcon, QPalette
from PyQt6.QtWidgets import QApplication

from namioto import settings as store
from namioto.ui import icons, theme
from namioto.ui.app import MainWindow


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    app.setStyle("Fusion")
    yield app


@pytest.fixture(autouse=True)
def the_dark_one_again(qt_app):
    """The suite's other checks read the colours as they are on a desktop that says nothing."""
    yield
    theme.apply(qt_app, "auto")


def test_auto_follows_the_desktop_and_a_silent_one_gets_the_dark_theme() -> None:
    assert theme.resolve("auto") == "dark"  # offscreen, so the desktop has no preference to follow
    assert theme.resolve("light") == "light"
    assert theme.resolve("dark") == "dark"


def test_the_two_themes_name_the_same_colours() -> None:
    assert set(theme.TOKENS["light"]) == set(theme.TOKENS["dark"])
    assert theme.TOKENS["light"] != theme.TOKENS["dark"]
    assert set(theme.CANVAS["light"].__dict__) == set(theme.CANVAS["dark"].__dict__)


def test_applying_a_theme_puts_its_colours_in_force(qt_app) -> None:
    assert theme.apply(qt_app, "light") == "light"
    assert theme.current() == "light"
    assert theme.canvas() is theme.CANVAS["light"]
    assert theme.tokens() == theme.TOKENS["light"]
    assert qt_app.palette().color(QPalette.ColorRole.Base) == QColor(theme.TOKENS["light"]["FIELD_BG"])
    assert theme.TOKENS["light"]["CARD"] in qt_app.styleSheet()
    assert "%" not in qt_app.styleSheet()  # every token was filled in

    assert theme.apply(qt_app, "auto") == "dark"
    assert theme.canvas() is theme.CANVAS["dark"]
    assert theme.TOKENS["dark"]["CARD"] in qt_app.styleSheet()


def test_an_icon_takes_its_colours_from_the_theme_in_force(qt_app) -> None:
    play = icons.icon("play")  # built before either theme is applied, and still follows both

    def most_of(pixmap) -> str:
        image = pixmap.toImage()
        tones: dict[str, int] = {}
        for x in range(image.width()):
            for y in range(image.height()):
                colour = image.pixelColor(x, y)
                if colour.alpha() > 200:
                    tones[colour.name()] = tones.get(colour.name(), 0) + 1
        return max(tones, key=lambda name: tones[name])

    theme.apply(qt_app, "dark")
    assert most_of(play.pixmap(24, 24)) == theme.TOKENS["dark"]["TEXT"]
    assert most_of(play.pixmap(24, 24, QIcon.Mode.Disabled)) == theme.TOKENS["dark"]["DISABLED"]

    theme.apply(qt_app, "light")
    assert most_of(play.pixmap(24, 24)) == theme.TOKENS["light"]["TEXT"]
    assert most_of(play.pixmap(24, 24, QIcon.Mode.Disabled)) == theme.TOKENS["light"]["DISABLED"]


def row_colours(window) -> set[str]:
    image = window.view.grab().toImage()
    return {image.pixelColor(300, y).name() for y in range(image.height())}


def test_the_window_wears_the_theme_its_setting_asks_for(qt_app) -> None:
    settings = store.load()
    store.set_value(settings, "appearance", "theme", "light")
    window = MainWindow(settings=settings)
    try:
        window.resize(1200, 720)
        window.show()
        qt_app.processEvents()
        assert theme.current() == "light"
        assert theme.CANVAS["light"].row_white.name() in row_colours(window)
        assert theme.CANVAS["dark"].row_white.name() not in row_colours(window)

        store.set_value(settings, "appearance", "theme", "dark")
        window.settings_store.apply(settings)
        qt_app.processEvents()
        assert theme.current() == "dark"
        assert theme.CANVAS["dark"].row_white.name() in row_colours(window)
        assert theme.CANVAS["light"].row_white.name() not in row_colours(window)
    finally:
        window.close()
