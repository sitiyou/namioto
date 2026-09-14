# SPDX-License-Identifier: AGPL-3.0-only
"""Shared test setup: the suite runs headless, so it needs no display and no session of its own."""

from __future__ import annotations

import os

# assigned, not setdefault: a desktop session usually exports QT_QPA_PLATFORM=wayland;xcb, and the
# tests must not care which session, or whether there is one at all
os.environ["QT_QPA_PLATFORM"] = "offscreen"
