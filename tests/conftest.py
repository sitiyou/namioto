# SPDX-License-Identifier: AGPL-3.0-only
"""Shared test setup: the suite runs headless, so it needs no display and no session of its own."""

from __future__ import annotations

import os
import sys
import types

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


class FakeMidiOut:
    """The MIDI backend, with one synth in its port list and nowhere to send to.

    It is a class because `rtmidi.MidiOut` is called as one, once per player: each gets its own. The
    port is named like the synth a desktop usually runs, since that is the name the player looks for.
    """

    opened: int | None = None

    def get_ports(self) -> list[str]:
        return ["TiMidity:Fake TiMidity port 0 128:0"]

    def get_port_name(self, index: int) -> str:
        return self.get_ports()[index]

    def open_port(self, index: int, name: str = "") -> None:
        self.opened = index

    def send_message(self, message) -> None:
        if self.opened is None:  # a real port refuses this, and so does this one
            raise RuntimeError("the port was not opened")

    def close_port(self) -> None:
        self.opened = None


@pytest.fixture(scope="session", autouse=True)
def quiet_midi():
    """No test may open a real synthesiser.

    Every window builds a player, and the external synth is the backend it prefers, so without this
    the suite would open whatever port the machine has - TiMidity, on a desktop that runs it - and
    play its notes through the user's own sound system. The whole `rtmidi` module is swapped for a
    fake one, which keeps the backend the same on every machine, whether or not MIDI is installed.
    """
    fake_midi = types.ModuleType("rtmidi")
    fake_midi.MidiOut = FakeMidiOut
    patcher = pytest.MonkeyPatch()
    patcher.setitem(sys.modules, "rtmidi", fake_midi)
    yield
    patcher.undo()
