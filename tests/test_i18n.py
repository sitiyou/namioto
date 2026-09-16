# SPDX-License-Identifier: AGPL-3.0-only
"""Checks for the interface language: the catalog, the system's own pick, and the fallback."""

from __future__ import annotations

import pytest

from namioto import i18n
from namioto import settings as store


@pytest.fixture(autouse=True)
def english_again():
    """Put the language back, so one test's switch cannot change what another one sees."""
    i18n.set_language(i18n.DEFAULT)
    yield
    i18n.set_language(i18n.DEFAULT)


def test_english_is_the_one_before_anything_sets_it() -> None:
    assert i18n.DEFAULT == "en"
    assert i18n.current() == "en"


def test_the_system_language_comes_from_the_locale(monkeypatch) -> None:
    monkeypatch.setenv("LANG", "zh_CN.UTF-8")
    assert i18n.system_language() == "zh"

    monkeypatch.setenv("LANG", "en_GB.UTF-8")
    assert i18n.system_language() == "en"


def test_an_unsupported_locale_falls_back_to_english(monkeypatch) -> None:
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    assert i18n.system_language() == "en"


def test_the_choice_names_the_system_or_a_language() -> None:
    assert i18n.resolve(i18n.SYSTEM) in {code for code, _label in i18n.LANGUAGES}
    assert i18n.resolve("zh") == "zh"
    assert len(i18n.LANGUAGE_LABELS) == len(i18n.LANGUAGE_CODES)


def test_a_translation_replaces_the_source_and_a_missing_one_keeps_it() -> None:
    assert i18n.tr("Settings") == "Settings"
    i18n.set_language("zh")
    assert i18n.current() == "zh"
    assert i18n.tr("Settings") == "设置"
    assert i18n.tr("nothing was translated") == "nothing was translated"


def test_a_translation_fills_in_what_the_source_leaves_open() -> None:
    i18n.set_language("zh")
    assert i18n.tr("Channel {number}", number=7) == "通道 7"


def test_the_stored_language_is_what_the_settings_offer() -> None:
    spec = store.FIELD_SPECS[("general", "language")]
    assert spec.choices == i18n.LANGUAGE_CODES
    assert spec.default == i18n.SYSTEM
    assert len(spec.labels) == len(spec.choices)


def test_every_catalog_entry_says_something() -> None:
    for code, catalog in i18n.CATALOGS.items():
        assert code in {language for language, _label in i18n.LANGUAGES}
        for source, translated in catalog.items():
            assert source.strip()
            assert translated.strip()
