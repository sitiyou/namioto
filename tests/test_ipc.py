# SPDX-License-Identifier: AGPL-3.0-only
"""Qt-free IPC discovery, protocol and client tests against a minimal local server."""

from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path

import pytest

from namioto import ipc


class TinyServer:
    """A minimal Unix JSON-lines server for testing the Qt-free client."""

    def __init__(self):
        self.endpoint = ipc.listen_endpoint()
        Path(self.endpoint.address).parent.mkdir(parents=True, exist_ok=True)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(self.endpoint.address)
        self.sock.listen(4)
        ipc.write_run(self.endpoint)
        self.dispatcher = ipc.Dispatcher()
        self.dispatcher.register("ping", lambda params: {"pong": True})
        self.dispatcher.register("echo", lambda params: params)
        self.dispatcher.register("boom", self._boom)
        self._running = True
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    @staticmethod
    def _boom(_params):
        raise ipc.IpcError("teapot", "no coffee here")

    def _accept(self) -> None:
        while self._running:
            try:
                connection, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def _serve(self, connection: socket.socket) -> None:
        with connection, connection.makefile("rwb") as stream:
            for line in stream:
                if line.strip():
                    stream.write(ipc.encode(self.dispatcher.handle(ipc.decode(line))))
                    stream.flush()

    def close(self) -> None:
        self._running = False
        self.sock.close()
        ipc.remove_run(self.endpoint.instance)
        Path(self.endpoint.address).unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def discovery(tmp_path, monkeypatch):
    monkeypatch.setattr(ipc, "runtime_dir", lambda *parts: tmp_path.joinpath(*parts))


@pytest.fixture
def server():
    if os.name == "nt":
        pytest.skip("the minimal server uses Unix sockets; Qt integration tests exercise Windows pipes")
    running = TinyServer()
    yield running
    running.close()


def test_endpoints_are_automatic_and_unique():
    first, second = ipc.listen_endpoint(), ipc.listen_endpoint()
    assert first.instance != second.instance
    assert first.address != second.address
    assert first.kind == ("pipe" if os.name == "nt" else "unix")
    assert ipc.Endpoint.from_dict(first.to_dict()) == first
    assert ipc.Endpoint.from_dict({"kind": "tcp", "address": "127.0.0.1:9000"}) is None


def test_run_file_round_trip():
    endpoint = ipc.listen_endpoint()
    path = ipc.write_run(endpoint)
    assert path == ipc.run_path(endpoint.instance)
    assert ipc.resolve_endpoint() == endpoint
    assert ipc.list_instances()[0].pid == os.getpid()
    ipc.remove_run("other")
    assert ipc.resolve_endpoint() == endpoint
    ipc.remove_run(endpoint.instance)
    assert ipc.list_instances() == []


def test_first_instance_is_default_and_explicit_selection_works(monkeypatch):
    first, second = ipc.listen_endpoint(), ipc.listen_endpoint()
    monkeypatch.setattr(ipc.time, "time_ns", lambda: 20)
    ipc.write_run(second)
    monkeypatch.setattr(ipc.time, "time_ns", lambda: 10)
    ipc.write_run(first)
    assert ipc.resolve_endpoint() == first
    assert ipc.resolve_endpoint(second.instance) == second
    ipc.remove_run(first.instance)
    assert ipc.resolve_endpoint() == second
    with pytest.raises(ipc.IpcError, match="no running editor with instance ID"):
        ipc.resolve_endpoint(first.instance)


def test_discovery_removes_dead_instances_and_sockets(monkeypatch):
    endpoint = ipc.listen_endpoint()
    path = ipc.write_run(endpoint, pid=1234)
    socket_path = ipc.runtime_dir("ipc", f"{endpoint.instance}.sock")
    if endpoint.kind == "unix":
        socket_path.touch()
    monkeypatch.setattr(ipc, "_alive", lambda pid: False)
    assert ipc.list_instances() == []
    assert not path.exists()
    assert not socket_path.exists()


def test_discovery_ignores_invalid_records():
    root = ipc.runtime_dir("ipc")
    root.mkdir(parents=True)
    (root / "broken.json").write_text("{")
    (root / "array.json").write_text("[]")
    (root / "old.json").write_text(json.dumps({"protocol": 0}))
    assert ipc.list_instances() == []


def test_address_environment_variables_are_ignored(monkeypatch):
    for name in ("NAMIOTO_IPC", "NAMIOTO_IPC_RUN", "NAMIOTO_IPC_TOKEN"):
        monkeypatch.setenv(name, "/tmp/not-used")
    endpoint = ipc.listen_endpoint()
    ipc.write_run(endpoint)
    assert ipc.resolve_endpoint() == endpoint


def test_dispatcher_answers_every_failure():
    dispatcher = ipc.Dispatcher()
    dispatcher.register("echo", lambda params: params)
    assert dispatcher.handle({"id": 1, "method": "echo", "params": {"a": 1}}) == {
        "id": 1,
        "ok": True,
        "result": {"a": 1},
    }
    assert dispatcher.handle("not a request")["error"]["code"] == "bad_request"
    assert dispatcher.handle({"id": 2})["error"]["code"] == "bad_request"
    assert dispatcher.handle({"id": 3, "method": "echo", "params": []})["error"]["code"] == "bad_params"
    assert dispatcher.handle({"id": 4, "method": "nope"})["error"]["code"] == "unknown_method"
    assert dispatcher.handle({"id": 5, "method": "boom"})["ok"] is False
    assert dispatcher.describe() == [{"method": "echo", "summary": "", "params": {}}]


def test_client_request_and_error(server):
    with ipc.Client(server.endpoint) as client:
        assert client.request("ping") == {"pong": True}
        assert client.request("echo", {"a": [1, 2]}) == {"a": [1, 2]}
        with pytest.raises(ipc.IpcError) as failure:
            client.request("boom")
        assert failure.value.code == "teapot"


def test_parse_params_and_aliases():
    assert ipc._parse_params(["seconds=1.5", "channel=2", "force=true"]) == {
        "seconds": 1.5,
        "channel": 2,
        "force": True,
    }
    with pytest.raises(ValueError):
        ipc._parse_params(["nonsense"])
    parser = ipc.build_parser()
    assert ipc._resolve_call(parser.parse_args(["bpm", "132"])) == ("transport.set_bpm", {"bpm": 132})
    assert ipc._resolve_call(parser.parse_args(["seek", "0.25"])) == ("transport.seek", {"seconds": 0.25})
    assert ipc._resolve_call(parser.parse_args(["status"])) == ("state.get", {})
    assert ipc._resolve_call(parser.parse_args(["document.notes"])) == ("document.notes", {})
    with pytest.raises(ValueError):
        ipc._resolve_call(parser.parse_args(["play", "1"]))


def test_cli_prints_the_result(server, capsys):
    assert ipc.main(["ping"]) == 0
    assert json.loads(capsys.readouterr().out) == {"pong": True}
    assert ipc.main(["--instance", server.endpoint.instance, "boom"]) == 1
    assert "teapot" in capsys.readouterr().err


def test_cli_lists_instances(capsys):
    endpoint = ipc.listen_endpoint()
    ipc.write_run(endpoint)
    assert ipc.main(["--list"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["instance"] == endpoint.instance


def test_cli_reports_no_editor(capsys):
    assert ipc.main(["ping"]) == 1
    assert "no running editor" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--endpoint", "--token", "--run"])
def test_cli_rejects_custom_connection_options(flag):
    with pytest.raises(SystemExit):
        ipc.build_parser().parse_args([flag, "custom"])
