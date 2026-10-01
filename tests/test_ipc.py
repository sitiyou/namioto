# SPDX-License-Identifier: AGPL-3.0-only
"""The remote-control interface without Qt: the endpoint, the run file, the dispatcher and the
client, against a plain socket server this module runs itself."""

from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path

import pytest

from namioto import ipc


class TinyServer:
    """A minimal line-framed server over a unix socket, built from the same `Dispatcher`.

    It exists to exercise the client without Qt: it speaks the protocol the Qt server does, and
    nothing more.
    """

    def __init__(self, path: Path, token: str = ""):
        self.token = token
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(path))
        self.sock.listen(4)
        self.endpoint = ipc.Endpoint("unix", str(path), token)
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
                if not line.strip():
                    continue
                request = ipc.decode(line)
                ident = request.get("id")
                if self.token and request.get("token") != self.token:
                    stream.write(ipc.encode(ipc.error_for(ident, "unauthorized", "no")))
                    stream.flush()
                    continue
                stream.write(ipc.encode(self.dispatcher.handle(request)))
                stream.flush()

    def close(self) -> None:
        self._running = False
        self.sock.close()


@pytest.fixture
def server(tmp_path):
    running = TinyServer(tmp_path / "control.sock")
    yield running
    running.close()


def test_parse_endpoint():
    assert ipc.parse_endpoint("") == ipc.Endpoint("unix", str(ipc.default_socket_path()))
    assert ipc.parse_endpoint("/tmp/song.sock") == ipc.Endpoint("unix", "/tmp/song.sock")
    assert ipc.parse_endpoint("unix:///tmp/song.sock") == ipc.Endpoint("unix", "/tmp/song.sock")
    assert ipc.parse_endpoint("tcp://127.0.0.1:9000") == ipc.Endpoint("tcp", "127.0.0.1:9000")
    assert ipc.parse_endpoint("127.0.0.1:9000") == ipc.Endpoint("tcp", "127.0.0.1:9000")
    named = ipc.parse_endpoint("second")
    assert named.kind == "unix" and named.address.endswith(os.sep + "namioto" + os.sep + "second")
    assert ipc.parse_endpoint("/tmp/x", token="secret").token == "secret"


def test_split_tcp():
    assert ipc.split_tcp("127.0.0.1:9000") == ("127.0.0.1", 9000)
    assert ipc.split_tcp(":9000") == (ipc.DEFAULT_HOST, 9000)
    with pytest.raises(ipc.IpcError):
        ipc.split_tcp("nonsense")


def test_run_file_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv(ipc.ENV_RUN, str(tmp_path / "run.json"))
    assert ipc.read_run() is None
    endpoint = ipc.Endpoint("tcp", "127.0.0.1:9000", "token")
    ipc.write_run(endpoint, pid=os.getpid())
    found = ipc.read_run()
    assert found == (endpoint, os.getpid())
    ipc.remove_run(pid=os.getpid() + 1)  # another process's record is left alone
    assert ipc.read_run() is not None
    ipc.remove_run(pid=os.getpid())
    assert ipc.read_run() is None


def test_run_file_ignores_a_dead_process(tmp_path, monkeypatch):
    monkeypatch.setenv(ipc.ENV_RUN, str(tmp_path / "run.json"))
    # a pid that cannot be running: negative and far past any real process table
    ipc.write_run(ipc.Endpoint("unix", "/tmp/gone.sock"), pid=2**30)
    assert ipc.read_run() is None


def test_resolve_endpoint_prefers_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv(ipc.ENV_RUN, str(tmp_path / "run.json"))
    ipc.write_run(ipc.Endpoint("unix", "/tmp/run.sock"), pid=os.getpid())
    monkeypatch.setenv(ipc.ENV_ENDPOINT, "/tmp/env.sock")
    assert ipc.resolve_endpoint().address == "/tmp/env.sock"
    assert ipc.resolve_endpoint("/tmp/cli.sock").address == "/tmp/cli.sock"
    monkeypatch.delenv(ipc.ENV_ENDPOINT)
    assert ipc.resolve_endpoint().address == "/tmp/run.sock"
    assert ipc.resolve_endpoint(discover=False) == ipc.default_endpoint()


def test_listen_endpoint_generates_a_tcp_token(monkeypatch):
    monkeypatch.delenv(ipc.ENV_TOKEN, raising=False)
    unix = ipc.listen_endpoint("/tmp/x.sock")
    assert unix.token == ""
    tcp = ipc.listen_endpoint("tcp://127.0.0.1:0")
    assert tcp.kind == "tcp" and len(tcp.token) == 32
    explicit = ipc.listen_endpoint("tcp://127.0.0.1:0", token="given")
    assert explicit.token == "given"


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
    unknown = dispatcher.handle({"id": 4, "method": "nope"})
    assert unknown["error"]["code"] == "unknown_method"
    assert dispatcher.handle({"id": 5, "method": "boom"})["ok"] is False
    assert dispatcher.describe() == [{"method": "echo", "summary": "", "params": {}}]


def test_client_request_and_error(server):
    with ipc.Client(server.endpoint) as client:
        assert client.request("ping") == {"pong": True}
        assert client.request("echo", {"a": [1, 2]}) == {"a": [1, 2]}
        with pytest.raises(ipc.IpcError) as failure:
            client.request("boom")
        assert failure.value.code == "teapot"


def test_client_rejects_a_wrong_token(tmp_path):
    server = TinyServer(tmp_path / "control.sock", token="right")
    try:
        with ipc.Client(ipc.Endpoint("unix", server.endpoint.address, "wrong")) as client:
            with pytest.raises(ipc.IpcError) as failure:
                client.request("ping")
            assert failure.value.code == "unauthorized"
        with ipc.Client(ipc.Endpoint("unix", server.endpoint.address, "right")) as client:
            assert client.request("ping") == {"pong": True}
    finally:
        server.close()


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
    code = ipc.main(["--endpoint", server.endpoint.address, "ping"])
    assert code == 0
    assert json.loads(capsys.readouterr().out) == {"pong": True}
    code = ipc.main(["--endpoint", server.endpoint.address, "boom"])
    assert code == 1
    assert "teapot" in capsys.readouterr().err


def test_cli_reports_an_unreachable_endpoint(tmp_path, capsys):
    code = ipc.main(["--endpoint", str(tmp_path / "nothing.sock"), "ping"])
    assert code == 2
    assert "cannot reach" in capsys.readouterr().err
