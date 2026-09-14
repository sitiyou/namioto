# SPDX-License-Identifier: AGPL-3.0-only
"""Shared test setup: the suite runs headless, so it needs no display and no session of its own."""

from __future__ import annotations

import os

import pytest

# assigned, not setdefault: a desktop session usually exports QT_QPA_PLATFORM=wayland;xcb, and the
# tests must not care which session, or whether there is one at all
os.environ["QT_QPA_PLATFORM"] = "offscreen"


@pytest.fixture(scope="session", autouse=True)
def isolated_settings(tmp_path_factory):
    """No test reads or writes the settings file of whoever is running the suite.

    Session wide, so that it is in place before the module-wide windows are built: a window reads the
    settings the moment it is constructed.
    """
    path = tmp_path_factory.mktemp("settings") / "settings.json"
    os.environ["NAMIOTO_SETTINGS"] = str(path)
    return path
