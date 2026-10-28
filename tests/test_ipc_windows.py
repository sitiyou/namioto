# SPDX-License-Identifier: AGPL-3.0-only
"""Native Windows discovery checks; local transport round trips live in test_ipc_server."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires Windows process handles")


def test_process_discovery_does_not_signal_processes(monkeypatch):
    windows = importlib.import_module("namioto.ipc_windows")

    def refuse(*args):
        raise AssertionError("Windows process discovery must not use os.kill")

    monkeypatch.setattr(os, "kill", refuse)
    assert windows.process_alive(os.getpid())
    with subprocess.Popen([sys.executable, "-c", "pass"]) as child:
        child.wait(timeout=10)
        assert not windows.process_alive(child.pid)
