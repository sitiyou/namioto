# SPDX-License-Identifier: AGPL-3.0-only
"""The remote-control interface end to end: a real `MainWindow`, a real local `IpcServer`,
and the Qt-free client driving both on Unix sockets or Windows named pipes.

The server runs on the GUI thread, so the client is asked from a worker thread while this one keeps
pumping `processEvents`; `call` and `failure` below wrap that so a test reads as one line.
"""

from __future__ import annotations

import base64
import os
import threading
import time

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from namioto import export_cli, ipc, midi, subtitle
from namioto import settings as store
from namioto.karaoke import ExportSettings
from namioto.ui.app import MainWindow

TIMEOUT = 5.0


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    return app


@pytest.fixture(scope="module")
def window(qt_app, tmp_path_factory):
    """One window for the module, reset between tests: a fresh one per test would leave the closed
    ones to be collected, and a headless run cannot afford a pile of live top-level widgets."""
    root = tmp_path_factory.mktemp("ipc")
    patcher = pytest.MonkeyPatch()
    patcher.setattr(ipc, "runtime_dir", lambda *parts: root.joinpath(*parts))
    previous_state = os.environ.get("NAMIOTO_STATE")
    os.environ["NAMIOTO_STATE"] = str(root / "state.json")
    built = MainWindow()
    built.resize(1000, 640)
    built.show()
    yield built
    built.project_dirty = False  # a close prompt would block a headless run
    built.project_path = None
    built.stop_ipc()
    built.close()
    built.deleteLater()
    qt_app.processEvents()
    patcher.undo()
    if previous_state is None:
        os.environ.pop("NAMIOTO_STATE", None)
    else:
        os.environ["NAMIOTO_STATE"] = previous_state


@pytest.fixture(autouse=True)
def clean_window(window):
    window.stop_ipc()
    window.reset_document()
    yield
    window.stop_ipc()


@pytest.fixture
def endpoint(window):
    bound = window.start_ipc()
    assert bound is not None
    return bound


def _run(app, action, timeout=TIMEOUT):
    """Run a blocking client call while the server's event loop keeps running."""
    box: dict = {}

    def worker():
        try:
            box["value"] = action()
        except Exception as error:  # noqa: BLE001 - handed back to the test as it is
            box["error"] = error

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout
    while thread.is_alive() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)
    thread.join(0.5)
    if "error" in box:
        raise box["error"]
    assert "value" in box, action
    return box["value"]


def call(app, endpoint, method, params=None):
    with ipc.Client(endpoint) as client:
        return _run(app, lambda: client.request(method, params, timeout=TIMEOUT))


def failure(app, endpoint, method, params=None) -> ipc.IpcError:
    with pytest.raises(ipc.IpcError) as raised:
        call(app, endpoint, method, params)
    return raised.value


class Watcher:
    """A long-lived client that collects the events the server broadcasts, off the main thread."""

    def __init__(self, endpoint):
        self.client = ipc.Client(endpoint)
        self.client.connect()
        self.events: list[dict] = []
        self.lock = threading.Lock()
        self._stop = False
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self):
        for event in self.client.events(timeout=0.2):
            if event is None:
                if self._stop:
                    return
                continue
            with self.lock:
                self.events.append(event)

    def wait_for(self, app, name, timeout=TIMEOUT) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            with self.lock:
                for event in self.events:
                    if event.get("event") == name:
                        return event
            time.sleep(0.005)
        raise AssertionError(f"no {name!r} event among {self.events}")

    def stop(self):
        self._stop = True
        self._thread.join(1.0)
        self.client.close()


@pytest.fixture
def watcher(endpoint):
    watching = Watcher(endpoint)
    yield watching
    watching.stop()


def test_ipc_command_line_overrides():
    from namioto.ui.app import parse_args

    assert parse_args([]).ipc is None
    assert parse_args(["--ipc"]).ipc is True
    assert parse_args(["--no-ipc"]).ipc is False


def test_ping_version_and_commands(qt_app, endpoint):
    assert call(qt_app, endpoint, "ping") == {"pong": True}
    version = call(qt_app, endpoint, "version")
    assert version["app"] == "namioto" and version["protocol"] == ipc.PROTOCOL
    methods = {entry["method"] for entry in call(qt_app, endpoint, "commands")["methods"]}
    assert {"transport.play", "transport.seek", "document.add_notes", "project.save", "channels.set"} <= methods


def test_state_describes_the_session(qt_app, endpoint):
    state = call(qt_app, endpoint, "state.get")
    assert state["project"]["path"] is None and state["project"]["dirty"] is False
    assert state["document"]["notes"] == 0
    assert state["transport"]["bpm"] == 120.0
    assert state["view"]["division"] == "beats"
    assert {channel["channel"] for channel in state["document"]["channels"]} == {0}


def test_transport_edits_reach_the_window(qt_app, window, endpoint):
    transport = call(qt_app, endpoint, "transport.set_bpm", {"bpm": 96.5})
    assert transport["bpm"] == 96.5 and window.view.bpm == 96.5
    assert call(qt_app, endpoint, "transport.set_speed", {"speed": 1.5})["speed"] == 1.5
    assert window.view.offset == 0.0
    call(qt_app, endpoint, "transport.set_grid_offset", {"ms": -120})
    assert window.view.offset == pytest.approx(-0.12)


def test_notes_round_trip(qt_app, endpoint):
    added = call(
        qt_app,
        endpoint,
        "document.add_notes",
        {"notes": [{"pitch": 60, "start": 0.5, "duration": 1.0}, {"pitch": 64, "start": 2.0, "duration": 0.5}]},
    )
    assert added == {"added": 2, "notes": 2}
    notes = call(qt_app, endpoint, "document.notes")["notes"]
    assert [(note["pitch"], note["start"], note["duration"]) for note in notes] == [(60, 0.5, 1.0), (64, 2.0, 0.5)]
    removed = call(qt_app, endpoint, "document.remove_notes", {"notes": [{"pitch": 60, "start": 0.5}]})
    assert removed == {"removed": 1, "notes": 1}
    assert call(qt_app, endpoint, "document.clear_notes") == {"notes": 0}


def test_remote_edits_preserve_existing_note_ids_and_allocator(qt_app, window, endpoint):
    view = window.view
    view.set_notes([(60, 0.0, 1.0, 0, 100), (62, 2.0, 1.0, 0, 200)], next_id=500)
    view.undo_stack.clear()
    call(qt_app, endpoint, "document.add_notes", {"notes": [{"pitch": 64, "start": 2.0, "duration": 0.5}]})
    assert [note.id for note in view.notes()] == [100, 200, 500]
    assert view.document.next_id == 501
    view.undo()
    assert [note.id for note in view.notes()] == [100, 200]
    assert view.document.next_id == 501
    view.redo()
    assert [note.id for note in view.notes()] == [100, 200, 500]
    call(qt_app, endpoint, "document.remove_notes", {"notes": [{"pitch": 64, "start": 2.0}]})
    assert [note.id for note in view.notes()] == [100, 200]
    assert view.document.next_id == 501
    view.undo()
    assert [note.id for note in view.notes()] == [100, 200, 500]
    view.redo()
    call(qt_app, endpoint, "document.replace_notes", {"notes": [{"pitch": 67, "start": 3.0, "duration": 0.5}]})
    assert [note.id for note in view.notes()] == [501]
    assert view.document.next_id == 502


def test_replace_notes_carries_channels(qt_app, endpoint):
    result = call(
        qt_app,
        endpoint,
        "document.replace_notes",
        {
            "channels": [{"channel": 3, "name": "Vocal", "program": 53, "volume": 90}],
            "notes": [{"pitch": 67, "start": 1.0, "duration": 0.5, "channel": 3}],
        },
    )
    assert result == {"notes": 1}
    state = call(qt_app, endpoint, "state.get")
    assert [channel["channel"] for channel in state["document"]["channels"]] == [3]
    assert state["document"]["channels"][0]["name"] == "Vocal"
    assert call(qt_app, endpoint, "document.notes")["notes"][0]["channel"] == 3


def test_quantize_snaps_to_the_named_cell(qt_app, endpoint):
    call(qt_app, endpoint, "document.add_notes", {"notes": [{"pitch": 60, "start": 0.31, "duration": 0.37}]})
    assert call(qt_app, endpoint, "document.quantize", {"cell": 0.5}) == {"quantized": True}
    note = call(qt_app, endpoint, "document.notes")["notes"][0]
    assert note["start"] == pytest.approx(0.25)
    assert note["duration"] == pytest.approx(0.5)


def test_channels_are_edited(qt_app, endpoint):
    added = call(qt_app, endpoint, "channels.add", {"channel": 4, "program": 24, "name": "Guitar"})
    assert added["channel"] == 4 and added["name"] == "Guitar"
    changed = call(qt_app, endpoint, "channels.set", {"channel": 4, "volume": 42, "mute": True})
    assert changed["volume"] == 42 and changed["mute"] is True
    assert call(qt_app, endpoint, "channels.activate", {"channel": 4}) == {"active_channel": 4}
    assert any(channel["channel"] == 4 for channel in call(qt_app, endpoint, "channels.list")["channels"])
    removed = call(qt_app, endpoint, "channels.remove", {"channel": 4})["channels"]
    assert removed == call(qt_app, endpoint, "channels.list")["channels"]
    assert failure(qt_app, endpoint, "channels.remove", {"channel": 0}).code == "conflict"


def test_project_save_and_open_round_trip(qt_app, window, endpoint, tmp_path):
    target = tmp_path / "song.nto"
    call(qt_app, endpoint, "document.add_notes", {"notes": [{"pitch": 72, "start": 1.0, "duration": 2.0}]})
    state = call(qt_app, endpoint, "project.save", {"path": str(target)})
    assert state["project"]["name"] == "song.nto" and state["project"]["dirty"] is False
    assert target.exists()
    call(qt_app, endpoint, "document.clear_notes")
    call(qt_app, endpoint, "project.open", {"path": str(target), "force": True})
    assert call(qt_app, endpoint, "document.notes")["notes"][0]["pitch"] == 72
    # a save to a bare name still lands on a .nto
    bare = tmp_path / "named"
    call(qt_app, endpoint, "project.save", {"path": str(bare)})
    assert (tmp_path / "named.nto").exists()


def test_project_tempo_preserves_short_notes_on_open(qt_app, endpoint, tmp_path):
    path = str(tmp_path / "short.nto")
    call(qt_app, endpoint, "transport.set_bpm", {"bpm": 180})
    call(qt_app, endpoint, "document.add_notes", {"notes": [{"pitch": 72, "start": 1, "duration": 0.025}]})
    call(qt_app, endpoint, "project.save", {"path": path})
    call(qt_app, endpoint, "transport.set_bpm", {"bpm": 120})
    call(qt_app, endpoint, "project.open", {"path": path, "force": True})
    assert call(qt_app, endpoint, "document.notes")["notes"][0]["duration"] == pytest.approx(0.025)


def test_unsaved_changes_need_force(qt_app, window, endpoint, tmp_path):
    target = tmp_path / "other.nto"
    call(qt_app, endpoint, "project.save", {"path": str(target)})
    call(qt_app, endpoint, "document.add_notes", {"notes": [{"pitch": 60, "start": 0.0, "duration": 0.5}]})
    assert window.project_dirty is True
    assert failure(qt_app, endpoint, "project.open", {"path": str(target)}).code == "dirty"
    assert call(qt_app, endpoint, "project.open", {"path": str(target), "force": True})["project"]["dirty"] is False


def test_unknown_method_and_bad_params(qt_app, endpoint):
    assert failure(qt_app, endpoint, "does.not.exist").code == "unknown_method"
    assert failure(qt_app, endpoint, "document.add_notes", {"notes": []}).code == "bad_params"
    bad_pitch = {"notes": [{"pitch": 200, "start": 0, "duration": 1}]}
    assert failure(qt_app, endpoint, "document.add_notes", bad_pitch).code == "bad_params"
    assert failure(qt_app, endpoint, "transport.set_bpm", {"bpm": 1000}).code == "bad_params"


def test_events_are_broadcast(qt_app, endpoint, watcher):
    hello = watcher.wait_for(qt_app, "hello")
    assert hello["protocol"] == ipc.PROTOCOL and hello["pid"] > 0
    call(qt_app, endpoint, "document.add_notes", {"notes": [{"pitch": 60, "start": 0.0, "duration": 1.0}]})
    event = watcher.wait_for(qt_app, "state")
    assert event["data"]["document"]["notes"] == 1


def test_transport_events_are_broadcast(qt_app, endpoint, watcher):
    call(qt_app, endpoint, "document.add_notes", {"notes": [{"pitch": 60, "start": 0.0, "duration": 1.0}]})
    watcher.wait_for(qt_app, "hello")
    call(qt_app, endpoint, "transport.toggle")
    assert watcher.wait_for(qt_app, "transport")["data"]["playing"] is True
    call(qt_app, endpoint, "transport.stop")


def test_status_messages_are_broadcast(qt_app, endpoint, watcher, tmp_path):
    call(qt_app, endpoint, "document.add_notes", {"notes": [{"pitch": 60, "start": 0.0, "duration": 1.0}]})
    watcher.wait_for(qt_app, "hello")
    call(qt_app, endpoint, "project.save", {"path": str(tmp_path / "song.nto")})
    message = watcher.wait_for(qt_app, "message")
    assert "song.nto" in message["data"]["text"]


def test_local_endpoint_is_restricted_to_current_user(window, endpoint):
    from pathlib import Path

    from PySide6.QtNetwork import QLocalServer

    assert window.ipc._local.socketOptions() == QLocalServer.SocketOption.UserAccessOption
    if os.name == "nt":
        assert endpoint.kind == "pipe"
        assert endpoint.address.startswith("\\\\.\\pipe\\")
    else:
        path = Path(endpoint.address)
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700


def test_stop_removes_only_its_own_run_file(window, endpoint):
    other = ipc.listen_endpoint()
    ipc.write_run(other)
    run = ipc.run_path(endpoint.instance)
    assert run.exists()
    window.stop_ipc()
    assert not run.exists()
    assert ipc.resolve_endpoint() == other
    ipc.remove_run(other.instance)


def test_multiple_servers_default_to_first(qt_app, window, endpoint):
    from namioto.ui.ipc_server import IpcServer

    other = IpcServer(ipc.listen_endpoint())
    other.dispatcher.register("ping", lambda params: {"second": True})
    bound = other.start()
    ipc.write_run(bound)
    try:
        assert ipc.resolve_endpoint() == endpoint
        assert call(qt_app, ipc.resolve_endpoint(), "ping") == {"pong": True}
        assert call(qt_app, ipc.resolve_endpoint(bound.instance), "ping") == {"second": True}
        window.stop_ipc()
        assert ipc.resolve_endpoint() == bound
    finally:
        other.stop()
        other.deleteLater()
        ipc.remove_run(bound.instance)


def test_legacy_remote_setting_is_ignored(tmp_path):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text('{"remote":{"enabled":false}}')
    settings = store.load(settings_file)
    assert not hasattr(settings, "remote")


@pytest.mark.parametrize(
    "override, expected", [(None, True), (False, False), (True, True)]
)
def test_startup_and_run_overrides(qt_app, override, expected):
    settings = store.Settings()
    built = MainWindow(settings, ipc_enabled=override)
    try:
        assert (built.ipc is not None) is expected
    finally:
        built.close()
        built.deleteLater()
        qt_app.processEvents()


def test_discovery_write_failure_does_not_leave_a_listener(window, monkeypatch):
    def fail(endpoint):
        raise OSError("cannot write discovery record")

    monkeypatch.setattr(ipc, "write_run", fail)
    assert window.start_ipc() is None
    assert window.ipc is None
    assert "could not start" in window.statusBar().currentMessage()


def test_import_midi_replaces_the_roll(qt_app, window, endpoint, tmp_path):
    from namioto import midi, project
    from namioto.channels import Channel

    call(qt_app, endpoint, "project.save", {"path": str(tmp_path / "song.nto")})
    call(qt_app, endpoint, "document.add_notes", {"notes": [{"pitch": 60, "start": 0.0, "duration": 1.0}]})
    target = tmp_path / "in.mid"
    midi.write(
        target,
        (Channel(channel=0),),
        (project.Note(1.0, 0.5, 64, 0),),
        120.0,
        wavetone=window.settings.midi.wavetone,
    )
    call(qt_app, endpoint, "document.import_midi", {"path": str(target), "force": True})
    assert [note["pitch"] for note in call(qt_app, endpoint, "document.notes")["notes"]] == [64]


def test_view_mix_and_history_commands(qt_app, window, endpoint):
    assert call(qt_app, endpoint, "view.set_zoom", {"x": 60, "y": 20})["zoom"] == [60.0, 20.0]
    assert call(qt_app, endpoint, "view.set_snap", {"beats": 1.0})["snap"] == 1.0
    assert call(qt_app, endpoint, "view.set_division", {"division": "seconds"})["division"] == "seconds"
    call(qt_app, endpoint, "mix.set_volume", {"layer": "audio", "volume": 30})
    assert window.mix.audio_volume.value() == 30
    assert call(qt_app, endpoint, "spectrum.set", {"gain": 300, "contrast": 2.0})["gain"] == 300
    call(qt_app, endpoint, "document.add_notes", {"notes": [{"pitch": 60, "start": 0.0, "duration": 1.0}]})
    assert call(qt_app, endpoint, "undo")["notes"] == 0
    assert call(qt_app, endpoint, "redo")["notes"] == 1


def test_lyrics_get_and_set(qt_app, window, endpoint):
    assert call(qt_app, endpoint, "lyrics.get")["text"] == ""
    result = call(qt_app, endpoint, "lyrics.set", {"text": "ら[la] り[li] る[lu]\n"})
    assert result["lines"] == 1
    assert call(qt_app, endpoint, "lyrics.get")["text"].startswith("ら")
    window._adopt_alignment([[(0.0, 1.0), (1.0, 1.0), (2.0, 1.0)]], "mms")
    assert call(qt_app, endpoint, "lyrics.set", {"text": "あい"}) == {"lines": 1}
    assert window.lyrics_text == "あい"
    assert "mode" not in call(qt_app, endpoint, "lyrics.get")


def _ready_ass(app, window):
    window.transport.bpm.setValue(60.0)
    window.view.set_notes(((60, 0.0, 1.0), (62, 1.0, 1.0)))
    window.lyrics_text = "あ.2"
    window._load_sounds()
    deadline = time.monotonic() + TIMEOUT
    while window.view.lyric_mapping_pending and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)
    assert not window.view.lyric_mapping_pending
    assert window._verification(subtitle=True).open()


def test_export_source_and_ctl_share_the_gui_export_pipeline(qt_app, window, endpoint, tmp_path, monkeypatch):
    _ready_ass(qt_app, window)
    monkeypatch.setattr(window.settings.ass, "offset_ms", 300)
    monkeypatch.setenv("NAMIOTO_ASS_OFFSET_MS", "990")
    source = call(qt_app, endpoint, "project.export_source")
    assert source["krc"] == window._verification().canonical
    assert source["settings"] == {"offset_ms": 300}
    data = base64.b64decode(source["midi"])
    assert len(midi.read_bytes(data, wavetone=source["wavetone"]).notes) == 2
    expected = subtitle.build_ass(
        source["krc"], data, settings=ExportSettings(**source["settings"]), wavetone=source["wavetone"]
    )
    assert not list(tmp_path.glob("*.krc"))
    assert not list(tmp_path.glob("*.mid"))
    result = call(qt_app, endpoint, "project.export_lyrics", {"path": str(tmp_path / "ctl")})
    assert result == {"path": str(tmp_path / "ctl.ass")}
    assert (tmp_path / "ctl.ass").read_text() == expected
    assert window.export_lyrics(tmp_path / "gui.ass")
    assert (tmp_path / "gui.ass").read_text() == expected
    assert not list(tmp_path.glob("*.krc"))
    assert not list(tmp_path.glob("*.mid"))
    call(
        qt_app,
        endpoint,
        "project.export_lyrics",
        {
            "path": str(tmp_path / "saved.ass"),
            "save_krc": str(tmp_path / "saved.krc"),
            "save_midi": str(tmp_path / "saved.mid"),
        },
    )
    assert (tmp_path / "saved.krc").read_text() == source["krc"]
    assert (tmp_path / "saved.mid").read_bytes() == data


def test_session_cli_matches_file_cli_with_saved_exports(qt_app, window, endpoint, tmp_path):
    _ready_ass(qt_app, window)
    saved_krc, saved_midi = tmp_path / "saved.krc", tmp_path / "saved.mid"
    session_ass, file_ass = tmp_path / "session.ass", tmp_path / "file.ass"
    assert (
        _run(
            qt_app,
            lambda: export_cli.main(
                [
                    "--session",
                    "--instance",
                    endpoint.instance,
                    "--out",
                    str(session_ass),
                    "--save-krc",
                    str(saved_krc),
                    "--save-midi",
                    str(saved_midi),
                    "--offset-ms",
                    "800",
                ]
            ),
        )
        == 0
    )
    compatibility = "--wavetone" if window.settings.midi.wavetone else "--no-wavetone"
    assert (
        export_cli.main(
            [
                "--krc",
                str(saved_krc),
                "--midi",
                str(saved_midi),
                "--out",
                str(file_ass),
                "--offset-ms",
                "800",
                compatibility,
            ]
        )
        == 0
    )
    assert session_ass.read_bytes() == file_ass.read_bytes()


def test_session_watch_rebuilds_after_an_edit_and_exits_on_disconnect(qt_app, window, endpoint, tmp_path):
    _ready_ass(qt_app, window)
    target = tmp_path / "watched.ass"
    seen = []

    def observe():
        if target.exists():
            text = target.read_bytes()
            if not seen:
                seen.append(text)
                window.view.set_notes(((60, 0.0, 1.0), (62, 2.0, 1.0)))
            elif text != seen[0]:
                seen.append(text)
                assert not window.view.lyric_mapping_pending
                window.stop_ipc()
                return
        QTimer.singleShot(10, observe)

    QTimer.singleShot(10, observe)
    result = _run(
        qt_app,
        lambda: export_cli.main(
            [
                "--session",
                "--instance",
                endpoint.instance,
                "--out",
                str(target),
                "--watch",
            ]
        ),
    )
    assert result == 1
    assert len(seen) == 2
    assert target.read_bytes() == seen[1]


def test_large_messages_round_trip(qt_app, window, endpoint):
    window.ipc.dispatcher.register("test.echo", lambda params: params)
    payload = {"text": "lyrics 日本語" * 20000}
    assert call(qt_app, endpoint, "test.echo", payload) == payload


def test_event_wait_recovers_after_timeout(qt_app, window, endpoint):
    with ipc.Client(endpoint, keep_events=False) as client:
        assert _run(qt_app, lambda: client.request("ping")) == {"pong": True}
        events = client.events(timeout=0.02)
        try:
            assert _run(qt_app, lambda: next(events)) is None
            assert _run(qt_app, lambda: next(events)) is None
            window.ipc.broadcast("test.event", {"ready": True})
            assert _run(qt_app, lambda: next(events)) == {"event": "test.event", "data": {"ready": True}}
        finally:
            events.close()


@pytest.mark.parametrize("keep_events", [True, False])
def test_polling_clients_can_discard_queued_events(qt_app, endpoint, keep_events):
    with ipc.Client(endpoint, keep_events=keep_events) as client:
        assert _run(qt_app, lambda: client.request("ping")) == {"pong": True}
        assert bool(client._events) is keep_events


def test_ass_revision_tracks_mapping_readiness_but_not_playback(qt_app, window, endpoint):
    initial = call(qt_app, endpoint, "state.get")["subtitle"]["revision"]
    _ready_ass(qt_app, window)
    state = call(qt_app, endpoint, "state.get")["subtitle"]
    assert state["revision"] > initial
    assert state["pending"] is False
    call(qt_app, endpoint, "transport.seek", {"seconds": 1})
    assert call(qt_app, endpoint, "state.get")["subtitle"]["revision"] == state["revision"]
    window.view.lyric_mapping_pending = True
    assert failure(qt_app, endpoint, "project.export_source").code == "failed"
    assert failure(qt_app, endpoint, "project.export_lyrics", {"path": "unused.ass"}).code == "failed"
    window._set_lyric_mapping_pending(False)
    assert call(qt_app, endpoint, "state.get")["subtitle"]["revision"] > state["revision"]


def test_editor_exports_ass_without_loading_a_project_template(qt_app, window, endpoint, tmp_path):
    _ready_ass(qt_app, window)
    assert window.save_project(tmp_path / "local.nto")
    marker = tmp_path / "executed"
    (tmp_path / "template.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")

    target = tmp_path / "local.ass"
    result = call(qt_app, endpoint, "project.export_lyrics", {"path": str(target)})
    assert result["path"] == str(target) and "Dialogue:" in target.read_text()
    assert not marker.exists()

    hook = tmp_path / "local.hook"
    rejected = failure(qt_app, endpoint, "project.export_lyrics", {"path": str(hook), "format": "hook"})
    assert rejected.code == "bad_params"
    assert not hook.exists()


def test_alignment_evidence_job(qt_app, window, endpoint, monkeypatch):
    from namioto.ui.align_dialog import Aligner

    assert failure(qt_app, endpoint, "analysis.align").code == "missing_input"
    monkeypatch.setattr(window, "audio_path", "voice.wav")
    call(qt_app, endpoint, "lyrics.set", {"text": "あ"})
    times = [[(0.1, 0.25)]]
    monkeypatch.setattr(Aligner, "load", lambda self: self.aligned.emit((times, self.model, [], [], [[0.9]], [[None]])))
    job = call(qt_app, endpoint, "analysis.align", {"model": "yohane"})["job"]
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        result = call(qt_app, endpoint, "analysis.alignment", {"job": job})
        if result["status"] == "complete":
            break
    assert result["times"] == [[[0.1, 0.25]]]
    assert result["scores"] == [[0.9]]
    assert failure(qt_app, endpoint, "analysis.alignment", {"job": "old"}).code == "unknown_job"


def test_clip_reply_waits_for_the_worker_while_other_clients_can_call(qt_app, endpoint, monkeypatch, tmp_path):
    from namioto.analysis import clip
    from namioto.ui.ipc_server import _Connection

    assert failure(qt_app, endpoint, "analysis.clip", {"model": "romajiasr"}).code == "missing_input"
    audio = tmp_path / "excerpt.wav"
    options = {"model": "romajiasr", "start": 0.5, "end": 2.0, "audio_origin": 42.0, "device": "gpu"}
    params = {"audio": str(audio), **options}
    started = threading.Event()
    release = threading.Event()
    recognized = {"model": "romajiasr", "transcription": "so ra", "tokens": [{"text": "so", "time": 42.6}]}
    sent_in = []
    send = _Connection.send

    def analyze(path, **clip_options):
        assert path == audio
        assert clip_options == {"model_dir": None, "candidates": [], **options}
        started.set()
        assert release.wait(TIMEOUT)
        return recognized

    def observe_send(connection, message):
        if message.get("result") == recognized:
            sent_in.append(threading.get_ident())
        send(connection, message)

    monkeypatch.setattr(clip, "analyze_clip", analyze)
    monkeypatch.setattr(_Connection, "send", observe_send)
    with ipc.Client(endpoint) as waiting:
        responses = []
        thread = threading.Thread(
            target=lambda: responses.append(waiting.request("analysis.clip", params)), daemon=True
        )
        thread.start()
        try:
            assert _run(qt_app, lambda: started.wait(TIMEOUT))
            assert call(qt_app, endpoint, "ping") == {"pong": True}
            assert failure(qt_app, endpoint, "analysis.clip", params).code == "busy"
            assert not responses
        finally:
            release.set()
            _run(qt_app, lambda: thread.join(TIMEOUT))
        assert responses == [recognized]
        assert _run(qt_app, lambda: waiting.request("ping")) == {"pong": True}
    assert sent_in == [threading.get_ident()]


def test_clip_can_reply_to_a_new_request_after_a_disconnect(qt_app, endpoint, window, monkeypatch):
    from namioto.analysis import clip

    started = threading.Event()
    release = threading.Event()
    params = {"model": "game", "audio": "clip.wav"}
    result = {"notes": []}

    def analyze(_audio, **_options):
        started.set()
        assert release.wait(TIMEOUT)
        return result

    monkeypatch.setattr(clip, "analyze_clip", analyze)
    try:
        with ipc.Client(endpoint) as departing:
            departing._socket.sendall(ipc.encode({"id": 8, "method": "analysis.clip", "params": params}))
            assert _run(qt_app, lambda: started.wait(TIMEOUT))
        assert call(qt_app, endpoint, "ping") == {"pong": True}
    finally:
        release.set()
    deadline = time.monotonic() + TIMEOUT
    while window._bridge._clip_worker is not None and time.monotonic() < deadline:
        qt_app.processEvents()
        time.sleep(0.002)
    assert call(qt_app, endpoint, "analysis.clip", params) == result


def test_clip_reply_reports_a_worker_failure(qt_app, endpoint, monkeypatch):
    from namioto.analysis import clip

    def analyze(_audio, **_options):
        raise ValueError("invalid clip")

    monkeypatch.setattr(clip, "analyze_clip", analyze)
    error = failure(qt_app, endpoint, "analysis.clip", {"model": "game", "audio": "clip.wav"})
    assert error.code == "failed"
    assert error.message == "ValueError: invalid clip"


@pytest.mark.parametrize("model", ["game", "mms"])
def test_clip_ipc_passes_windows_and_model_options(qt_app, endpoint, window, monkeypatch, tmp_path, model):
    from namioto.analysis import clip

    audio = tmp_path / "vocal.wav"
    monkeypatch.setattr(window, "audio_path", str(audio))
    candidates = ["宇宙[そら]"] if model == "mms" else []
    params = {"model": model, "start": 0.5, "end": 2.0, "audio_origin": 42.0}
    options = {**params, "device": "cpu", "model_dir": None, "candidates": candidates}
    if model == "mms":
        assert failure(qt_app, endpoint, "analysis.clip", params).code == "bad_params"
        params["candidates"] = candidates
    else:
        params.update(size="large", language="ja")
        options.update(game_size="large", game_language="ja")
    assert failure(qt_app, endpoint, "analysis.clip", {**params, "end": 0.4}).code == "bad_params"
    result = {"model": model, "audio": str(audio), "window": {"start": 0.5, "end": 2.0, "audio_origin": 42.0}}

    def analyze(path, **clip_options):
        assert path == audio
        assert clip_options == options
        return result

    monkeypatch.setattr(clip, "analyze_clip", analyze)
    assert call(qt_app, endpoint, "analysis.clip", params) == result
    assert not window.project_dirty


def test_spectrum_frame_window(qt_app, window, endpoint):
    import numpy as np

    from namioto.analysis.spectrum import MIDI_OFFSET, NoteSpectrum

    assert failure(qt_app, endpoint, "spectrum.frames", {"start": 0}).code == "not_ready"
    table = np.arange(4 * 84, dtype=float).reshape(4, 84)
    window.view.set_spectrum(NoteSpectrum(table, 10, 48000, 8192, 480, 440, 1))
    result = call(qt_app, endpoint, "spectrum.frames", {"start": 0.024, "count": 10})
    assert result == {"start": 0.02, "step": 0.01, "first_pitch": MIDI_OFFSET, "frames": table[2:].tolist()}
    assert call(qt_app, endpoint, "spectrum.frames", {"start": 0.04})["frames"] == []


def test_transcription_job_crash_is_reported(qt_app, window, endpoint, monkeypatch):
    import sys

    monkeypatch.setattr(window, "audio_path", None)
    assert failure(qt_app, endpoint, "analysis.transcribe").code == "missing_input"
    monkeypatch.setattr(window, "audio_path", "voice.wav")
    assert failure(qt_app, endpoint, "analysis.transcribe", {"size": "unknown"}).code == "bad_params"
    monkeypatch.setattr(sys, "executable", "/missing/namioto-test-python")
    job = call(qt_app, endpoint, "analysis.transcribe")["job"]
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        result = call(qt_app, endpoint, "analysis.transcription", {"job": job})
        if result["status"] == "failed":
            break
    assert result["status"] == "failed" and result["error"]


def test_bpm_candidate_selection(qt_app, endpoint, window, monkeypatch):
    from namioto.analysis import bpm

    monkeypatch.setattr(window, "audio_path", "mix.wav")
    estimate = bpm.BpmEstimate(90, "wavetone", candidates=((90, 1), (180, 0.8)))
    monkeypatch.setattr(bpm, "estimate", lambda path, algorithm: estimate)
    job = call(qt_app, endpoint, "analysis.detect_bpm")["job"]
    deadline = time.monotonic() + TIMEOUT
    while time.monotonic() < deadline:
        result = call(qt_app, endpoint, "analysis.bpm_result", {"job": job})
        if result["status"] == "complete":
            break
    candidate = result["candidates"][1][0]
    assert call(qt_app, endpoint, "transport.set_bpm", {"bpm": candidate})["bpm"] == 180


def test_game_grid_preview_preserves_raw_notes(qt_app, endpoint, window):
    notes = [{"onset": t, "offset": t + 0.23, "pitch": 65} for t in (0.52, 0.77, 1.02)]
    window._bridge._remember_job({"job": "sample", "status": "complete", "kind": "game", "notes": notes})
    call(qt_app, endpoint, "transport.set_bpm", {"bpm": 120})
    params = {"job": "sample", "cells": 2, "estimate_phase": True}
    grid = call(qt_app, endpoint, "analysis.transcription_grid", params)
    assert grid["offset_ms"] == 20
    assert grid["cell_seconds"] == 0.25
    assert grid["notes"][0]["offset"] == pytest.approx(0.77)
    assert call(qt_app, endpoint, "analysis.transcription", {"job": "sample"})["notes"] == notes
    assert window.transport.grid_offset.value() == 0


def test_phrase_flags_audible_release_removed_by_eighth_note_grid(qt_app, endpoint, window, tmp_path, monkeypatch):
    import numpy as np
    import soundfile as sf

    path = tmp_path / "voice.wav"
    time_axis = np.arange(12000) / 12000
    tone = 0.3 * np.sin(2 * np.pi * 440 * time_axis)
    sf.write(path, tone * ((time_axis >= 0.25) & (time_axis < 0.62)), 12000)
    monkeypatch.setattr(window, "audio_path", str(path))
    call(qt_app, endpoint, "lyrics.set", {"text": "あ"})
    call(qt_app, endpoint, "transport.set_bpm", {"bpm": 120})
    window._bridge._remember_job(
        {
            "job": "raw",
            "audio": str(path),
            "status": "complete",
            "kind": "game",
            "notes": [{"onset": 0.25, "offset": 0.62, "pitch": 69}],
        }
    )
    params = {"line": 0, "start": 0.2, "end": 0.8, "estimate_phase": False}
    row = call(qt_app, endpoint, "analysis.phrase", params)
    (detail,) = row["game"]["quantization"]
    assert detail["offset"] == {
        "raw": 0.62,
        "snapped": 0.5,
        "shift_ms": -120,
        "midpoint_distance_ms": 5,
        "finer_grid": 0.625,
        "review": True,
    }
    assert not detail["onset"]["review"]
    (signal,) = row["signal_summary"]["notes"]
    assert signal["raw_index"] == detail["raw_index"]
    release = signal["offset"]
    assert release["between"] == pytest.approx(release["before"], abs=1)
    assert release["after"][0] < release["before"][0] - 12
    assert release["harmonic_drop"] == pytest.approx(0.63, abs=0.01)
    assert "spectrum" not in row and "rms" not in row
    assert "signal_summary" not in call(qt_app, endpoint, "analysis.phrase", dict(params, signal_summary=False))


def test_phrase_summarizes_a_pitch_change_and_a_high_band_sound_in_a_mapped_gap(
    qt_app, endpoint, window, tmp_path, monkeypatch
):
    import numpy as np
    import soundfile as sf

    from namioto.analysis.spectrum import wave_notes

    rate = 16000
    t = np.arange(rate * 2) / rate
    audio = 0.3 * np.sin(2 * np.pi * 261.626 * t) * ((t >= 0.25) & (t < 0.5))
    audio += 0.3 * np.sin(2 * np.pi * 440 * t) * ((t >= 0.5) & (t < 0.75))
    audio += 0.1 * np.sin(2 * np.pi * 3500 * t) * ((t >= 0.81) & (t < 0.87))
    path = tmp_path / "voice.wav"
    sf.write(path, audio, rate)
    monkeypatch.setattr(window, "audio_path", str(path))
    window.view.set_spectrum(wave_notes(audio, rate))
    call(qt_app, endpoint, "lyrics.set", {"text": "あいし.0て"})
    call(
        qt_app,
        endpoint,
        "document.add_notes",
        {
            "notes": [
                {"start": 0.25, "duration": 0.25, "pitch": 60},
                {"start": 0.5, "duration": 0.25, "pitch": 69},
                {"start": 1.0, "duration": 0.2, "pitch": 69},
            ]
        },
    )
    row = call(qt_app, endpoint, "analysis.phrase", {"line": 0})
    summary = row["signal_summary"]
    assert summary["note_spectrum"] == {"backend": "wavetone", "step_ms": 10}
    change = summary["notes"][1]["pitch_change_before"]
    assert change["pitches"] == [60, 69]
    assert change["before_db"] < -12
    assert change["after_db"] > 12
    (gap,) = summary["unmapped_gaps"]
    assert gap["sounds"] == [2]
    assert gap["start"] == pytest.approx(0.75)
    assert gap["end"] == pytest.approx(1.0)
    high_band = gap["high_band"]
    assert 0.81 < high_band["peak_at"] < 0.87
    assert high_band["peak_dbfs"] > high_band["median_dbfs"] + 20
    assert "spectrum" not in row


def test_phrase_edit_preview_commit_undo_and_stale_revision(qt_app, window, endpoint):
    def request(method, **params):
        return call(qt_app, endpoint, method, params)

    request("lyrics.set", text="あいうえ\nお")
    request("document.add_notes", notes=[{"start": i, "duration": 0.5, "pitch": 62} for i in range(5)])
    before = request("analysis.phrase", line=0, context=0)
    # Drop one sound, share a note, and sing the final vowel on two notes.
    notes = before["notes"][:3]
    payload = {
        "line": 0,
        "revision": before["revision"],
        "notes": notes,
        "mapping": [{"sounds": [0], "slots": 0}, {"sounds": [1, 2], "slots": 1}, {"sounds": [3], "slots": 2}],
    }
    depth = window.view.undo_stack.index()
    preview = request("document.apply_phrase", **payload)
    assert preview["preview"] and preview["next_sound"]["before"] == preview["next_sound"]["after"]
    assert window.view.undo_stack.index() == depth
    request("document.apply_phrase", preview=False, **payload)
    assert window.view.undo_stack.index() == depth + 1
    after = request("analysis.phrase", line=0, context=0)
    ids = [n["id"] for n in notes]
    assert [s["note_ids"] for s in after["sounds"]] == [[], ids[:1], ids[:1], ids[1:]]
    assert failure(qt_app, endpoint, "document.apply_phrase", payload).code == "conflict"
    request("undo")
    assert request("analysis.phrase", line=0, context=0)["revision"] == before["revision"]
    request("redo")
    assert request("analysis.phrase", line=0, context=0)["mapping"] == after["mapping"]


def test_phrase_joins_both_alignments_and_reuses_game(qt_app, window, endpoint, monkeypatch):
    from namioto.ui.align_dialog import Aligner

    monkeypatch.setattr(window, "audio_path", "voice.wav")
    call(qt_app, endpoint, "lyrics.set", {"text": "あ@1{い}う\nえ"})

    def emit(worker):
        times = [[(t, t + 0.25)] for t in (1, 2, 3)]
        worker.aligned.emit((times, worker.model, [], [], [[0.9]] * 3, [[None]] * 3))

    monkeypatch.setattr(Aligner, "load", emit)
    jobs = {}
    for model in ("mms", "yohane"):
        job = call(qt_app, endpoint, "analysis.align", {"model": model})["job"]
        jobs[model] = job
        for _ in range(100):
            if call(qt_app, endpoint, "analysis.alignment", {"job": job})["status"] == "complete":
                break
    assert call(qt_app, endpoint, "analysis.alignment", {"job": jobs["mms"]})["model"] == "mms"
    raw = [{"onset": 0.99, "offset": 1.24, "pitch": 64}]
    window._bridge._remember_job(
        {"job": "raw", "audio": "voice.wav", "status": "complete", "notes": raw, "kind": "game"}
    )
    row = call(qt_app, endpoint, "analysis.phrase", {"line": 0})
    assert row["alignment_jobs"] == jobs and row["game"]["raw"] == raw
    assert row["grid"]["cells"] == 2 and row["grid"]["cell_seconds"] == 0.25
    assert [(s["reading"], s["alignment"]["mms"]["times"][0]) for s in row["sounds"]] == [
        ("あ", 1),
        ("う", 2),
        ("え", 3),
    ]
    call(qt_app, endpoint, "lyrics.set", {"text": "お"})
    assert set(call(qt_app, endpoint, "analysis.phrase", {"line": 0})["missing"]) == {"mms", "yohane"}


def test_phrase_rejects_cross_line_edit_and_pages_signal(qt_app, window, endpoint, tmp_path, monkeypatch):
    import numpy as np
    import soundfile as sf

    from namioto.analysis.spectrum import NoteSpectrum

    path = tmp_path / "voice.wav"
    sf.write(path, np.full((8000 * 7, 2), [0.25, 0.5]), 8000)
    monkeypatch.setattr(window, "audio_path", str(path))
    window.view.set_spectrum(NoteSpectrum(np.ones((700, 84)), 10, 8000, 1024, 80, 440, 1))
    call(qt_app, endpoint, "lyrics.set", {"text": "あ@1{い}\nう"})
    call(
        qt_app,
        endpoint,
        "document.add_notes",
        {
            "notes": [
                {"start": 1, "duration": 0.3, "pitch": 60},
                {"start": 2, "duration": 0.3, "pitch": 62},
                {"start": 1, "duration": 0.4, "pitch": 70, "channel": 1},
            ]
        },
    )
    row = call(qt_app, endpoint, "analysis.phrase", {"line": 0, "start": 0, "end": 6, "spectrum": True, "rms": True})
    assert row["rms"]["linear_rms"][0] == pytest.approx([0.25, 0.5])
    assert row["rms"]["next_start"] == 5.12
    assert row["spectrum"]["next_start"] == 2.56
    assert len(row["notes"]) == len(row["context_notes"]) == 1
    assert [note["note_id"] for note in row["signal_summary"]["notes"]] == [
        row["notes"][0]["id"],
        row["context_notes"][0]["id"],
    ]
    payload = {
        "line": 0,
        "revision": row["revision"],
        "preview": False,
        "mapping": row["mapping"],
        "notes": [dict(row["notes"][0], start=3)],
    }
    depth = window.view.undo_stack.index()
    assert failure(qt_app, endpoint, "document.apply_phrase", payload).code == "bad_params"
    assert window.view.undo_stack.index() == depth
    payload["notes"][0]["start"] = 1.1
    call(qt_app, endpoint, "document.apply_phrase", payload)
    harmony = call(qt_app, endpoint, "analysis.phrase", {"line": 0, "channel": 1})
    assert harmony["notes"][0]["start"] == 1
