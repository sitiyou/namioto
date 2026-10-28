# SPDX-License-Identifier: AGPL-3.0-only
"""The remote-control interface: the wire protocol, where a running editor is found, and the
`namioto-ctl` command line that talks to one.

Qt-free on purpose. The editor uses `QLocalServer` in `namioto.ui.ipc_server`; the client
uses Unix sockets or Windows named pipes without importing Qt.

The protocol is one JSON object per line, UTF-8, with no framing beyond the newline: a request
`{"id": 1, "method": "transport.play", "params": {}}` is answered by
`{"id": 1, "ok": true, "result": {...}}`, or by `{"id": 1, "ok": false, "error": {...}}`. The server
may also send an event, `{"event": "state", "data": {...}}`, to every client connected.

Each editor owns an automatically named endpoint and discovery record. Clients select an instance
by ID, or the first surviving instance in startup order when no ID is given. Addresses are internal.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import secrets
import socket
import sys
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from namioto import __version__
from namioto.utils import runtime_dir, write_json

PROTOCOL = 1
APP = "namioto"
LINE_LIMIT = 1 << 20  # one line may carry a whole transcription; past this the client is cut off
CONNECT_TIMEOUT = 5.0

# the words `namioto-ctl` accepts in place of a full method name, and the parameter a lone
# positional argument fills in
ALIASES: dict[str, tuple[str, str]] = {
    "play": ("transport.play", ""),
    "pause": ("transport.pause", ""),
    "stop": ("transport.stop", ""),
    "toggle": ("transport.toggle", ""),
    "status": ("state.get", ""),
    "state": ("state.get", ""),
    "notes": ("document.notes", ""),
    "channels": ("channels.list", ""),
    "methods": ("commands", ""),
    "seek": ("transport.seek", "seconds"),
    "bpm": ("transport.set_bpm", "bpm"),
    "speed": ("transport.set_speed", "speed"),
}


class IpcError(Exception):
    """A request that could not be served, with the machine-readable code that says why."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def error_for(ident, code: str, message: str, details: dict | None = None) -> dict:
    error = {"code": code, "message": message}
    if details:
        error["details"] = details
    return {"id": ident, "ok": False, "error": error}


# --- the network shape of an endpoint ---------------------------------------


@dataclass(frozen=True)
class Endpoint:
    """An internally managed Unix socket or Windows named pipe."""

    kind: str
    address: str
    instance: str

    def describe(self) -> str:
        return f"{self.kind}://{self.address}"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "address": self.address, "instance": self.instance}

    @classmethod
    def from_dict(cls, data: Any) -> Endpoint | None:
        if not isinstance(data, dict) or data.get("kind") not in ("unix", "pipe"):
            return None
        address = data.get("address")
        instance = data.get("instance")
        if not isinstance(address, str) or not address or not isinstance(instance, str) or not instance.isalnum():
            return None
        return cls(data["kind"], address, instance)


@dataclass(frozen=True)
class Instance:
    """A live editor in discovery order."""

    endpoint: Endpoint
    pid: int
    started: int

    def to_dict(self) -> dict:
        return {"instance": self.endpoint.instance, "pid": self.pid, "started": self.started}


def run_path(instance: str) -> Path:
    return runtime_dir("ipc", f"{instance}.json")


def write_run(endpoint: Endpoint, pid: int | None = None) -> Path:
    """Record the live endpoint, so a client started with no address of its own can find it."""
    return write_json(
        {
            "protocol": PROTOCOL,
            "app": APP,
            "version": __version__,
            "pid": os.getpid() if pid is None else pid,
            "started": time.time_ns(),
            "endpoint": endpoint.to_dict(),
        },
        run_path(endpoint.instance),
    )


def list_instances() -> list[Instance]:
    """Discover live editors in startup order, removing records of exited processes."""
    instances = []
    for path in runtime_dir("ipc").glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or data.get("protocol") != PROTOCOL:
            continue
        endpoint = Endpoint.from_dict(data.get("endpoint"))
        pid = data.get("pid")
        started = data.get("started")
        if endpoint is None or not isinstance(pid, int) or pid <= 0 or not isinstance(started, int):
            continue
        if path != run_path(endpoint.instance):
            continue
        if not _alive(pid):
            with contextlib.suppress(OSError):
                path.unlink()
            if endpoint.kind == "unix":
                with contextlib.suppress(OSError):
                    runtime_dir("ipc", f"{endpoint.instance}.sock").unlink()
            continue
        instances.append(Instance(endpoint, pid, started))
    return sorted(instances, key=lambda item: (item.started, item.endpoint.instance))


def remove_run(instance: str) -> None:
    with contextlib.suppress(OSError):
        run_path(instance).unlink()


def _alive(pid: int) -> bool:
    if os.name == "nt":
        from namioto.ipc_windows import process_alive

        return process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # another user's process: alive as far as this one can tell
        return True
    return True


def resolve_endpoint(instance: str | None = None) -> Endpoint:
    """Select the requested instance, or the first surviving editor."""
    for running in list_instances():
        if instance is None or running.endpoint.instance == instance:
            return running.endpoint
    if instance is not None:
        raise IpcError("no_instance", f"no running editor with instance ID {instance!r}")
    raise IpcError("no_instance", "no running editor has remote control enabled")


def listen_endpoint() -> Endpoint:
    """Allocate a unique local endpoint for one editor."""
    instance = secrets.token_hex(8)
    if os.name == "nt":
        return Endpoint("pipe", rf"\\.\pipe\namioto-{instance}", instance)
    return Endpoint("unix", str(runtime_dir("ipc", f"{instance}.sock")), instance)


# --- the protocol messages --------------------------------------------------


def encode(message: dict) -> bytes:
    return (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def decode(line: bytes | str) -> dict:
    """One line as a message; anything that is not a JSON object is a protocol error."""
    if isinstance(line, bytes):
        line = line.decode("utf-8")
    data = json.loads(line)
    if not isinstance(data, dict):
        raise ValueError("a message must be a JSON object")
    return data


def unwrap(reply: dict) -> Any:
    """The result of a response, or the error it carries raised for the caller."""
    if reply.get("ok"):
        return reply.get("result")
    error = reply.get("error")
    if not isinstance(error, dict):
        raise IpcError("failed", "the server returned no error")
    raise IpcError(str(error.get("code", "failed")), str(error.get("message", "")), error.get("details"))


# --- dispatching a request --------------------------------------------------


@dataclass(frozen=True)
class Command:
    name: str
    summary: str
    params: dict[str, str]
    handler: Callable[[dict], Any]


class Dispatcher:
    """The methods a server offers, each a plain callable over a parameter dict.

    It knows nothing about sockets or Qt: the server hands it a decoded request and writes back what
    it returns, and `commands` lets a client discover what is there. A handler raises `IpcError` to
    name its own failure; anything else it raises becomes a `failed` error.
    """

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(
        self,
        name: str,
        handler: Callable[[dict], Any],
        *,
        summary: str = "",
        params: dict[str, str] | None = None,
    ) -> Callable[[dict], Any]:
        self._commands[name] = Command(name, summary, dict(params or {}), handler)
        return handler

    def describe(self) -> list[dict]:
        return [
            {"method": command.name, "summary": command.summary, "params": command.params}
            for command in sorted(self._commands.values(), key=lambda item: item.name)
        ]

    def handle(self, request: Any) -> dict:
        if not isinstance(request, dict):
            return error_for(None, "bad_request", "a request must be a JSON object")
        ident = request.get("id")
        method = request.get("method")
        if not isinstance(method, str) or not method:
            return error_for(ident, "bad_request", "the request names no method")
        params = request.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return error_for(ident, "bad_params", "params must be a JSON object")
        command = self._commands.get(method)
        if command is None:
            return error_for(
                ident,
                "unknown_method",
                f"no method named {method!r}",
                {"methods": sorted(self._commands)},
            )
        try:
            return {"id": ident, "ok": True, "result": command.handler(params)}
        except IpcError as error:
            return error_for(ident, error.code, error.message, error.details)
        except Exception as error:  # a handler's own bug must come back as an answer, not a crash
            return error_for(ident, "failed", f"{type(error).__name__}: {error}")


# --- the client -------------------------------------------------------------


class Client:
    """A blocking JSON-lines client over a Unix socket or Windows named pipe.

    One call opens the connection; `request` sends one method and waits for its answer, skipping
    any event that arrives in between. Events are kept unless `keep_events=False`, and `events`
    yields those and everything the server pushes afterwards.
    """

    def __init__(self, endpoint: Endpoint, timeout: float = CONNECT_TIMEOUT, *, keep_events: bool = True):
        self.endpoint = endpoint
        self.timeout = timeout
        self.keep_events = keep_events
        self._socket: Any = None
        self._buffer = bytearray()
        self._id = 0
        self._events: deque[dict] = deque()

    def connect(self) -> Client:
        if self._socket is not None:
            return self
        if self.endpoint.kind == "unix":
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(self.timeout)
            connection.connect(self.endpoint.address)
        else:
            from namioto.ipc_windows import PipeSocket

            connection = PipeSocket(self.endpoint.address, self.timeout)
        self._socket = connection
        return self

    def close(self) -> None:
        connection, self._socket = self._socket, None
        self._buffer.clear()
        if connection is not None:
            connection.close()

    def _readline(self) -> bytes:
        while True:
            index = self._buffer.find(b"\n")
            if index >= 0:
                line = bytes(self._buffer[: index + 1])
                del self._buffer[: index + 1]
                return line
            chunk = self._socket.recv(65536)
            if not chunk:
                return b""
            self._buffer.extend(chunk)

    def request(self, method: str, params: dict | None = None, *, timeout: float | None = None) -> Any:
        self.connect()
        self._id += 1
        ident = self._id
        message: dict = {"id": ident, "method": method}
        if params:
            message["params"] = params
        assert self._socket is not None
        previous = self._socket.gettimeout()
        if timeout is not None:
            self._socket.settimeout(timeout)
        try:
            self._socket.sendall(encode(message))
            while True:
                line = self._readline()
                if not line:
                    raise IpcError("closed", "the server closed the connection")
                reply = decode(line)
                if "event" in reply:
                    if self.keep_events:
                        self._events.append(reply)
                    continue
                if reply.get("id") != ident:
                    continue  # a late answer to a request that already timed out
                return unwrap(reply)
        except TimeoutError as error:
            raise IpcError("timeout", f"{method} did not answer in time") from error
        finally:
            if timeout is not None:
                self._socket.settimeout(previous)

    def events(self, timeout: float | None = None) -> Iterator[dict]:
        """Every event the server sends, from now on; blocks between them.

        `timeout` bounds one wait, after which `None` is yielded and the loop carries on, so a caller
        can stop the stream without closing the socket.
        """
        self.connect()
        while self._events:
            yield self._events.popleft()
        assert self._socket is not None
        previous = self._socket.gettimeout()
        self._socket.settimeout(timeout)
        try:
            while True:
                try:
                    line = self._readline()
                except TimeoutError:
                    yield None
                    continue
                if not line:
                    return
                try:
                    message = decode(line)
                except ValueError:
                    continue
                if "event" in message:
                    yield message
        finally:
            self._socket.settimeout(previous)

    def __enter__(self) -> Client:
        return self.connect()

    def __exit__(self, *_error) -> None:
        self.close()


# --- the command line -------------------------------------------------------


def _value(text: str) -> Any:
    """A command-line value with its JSON type when it has one: `132`, `0.25`, `[1, 2]`."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def _parse_params(pairs: list[str]) -> dict:
    """`NAME=VALUE` arguments as a parameter dict; a value that parses as JSON keeps its type."""
    params: dict[str, Any] = {}
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if not sep or not name:
            raise ValueError(f"{pair!r} is not a NAME=VALUE parameter")
        params[name] = _value(value)
    return params


def _resolve_call(args: argparse.Namespace) -> tuple[str, dict]:
    method = args.method
    positional = [item for item in args.params if "=" not in item]
    if positional:
        if len(positional) != len(args.params):
            raise ValueError("positional values and NAME=VALUE parameters cannot be mixed")
        if len(positional) > 1:
            raise ValueError("give at most one positional value")
        name = ALIASES.get(method, ("", ""))[1]
        if not name:
            raise ValueError(f"{method} takes no positional argument; use NAME=VALUE")
        params = {name: _value(positional[0])}
    else:
        params = _parse_params(args.params)
    return ALIASES.get(method, (method,))[0], params


def _print(value: Any, compact: bool) -> None:
    if compact:
        print(json.dumps(value, ensure_ascii=False, separators=(",", ":")), flush=True)
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="namioto-ctl",
        description="Remote control for a running Namioto editor.",
    )
    parser.add_argument(
        "method",
        nargs="?",
        default="state.get",
        help="a method name (see `namioto-ctl methods`), or one of "
        + ", ".join(sorted(ALIASES))
        + " (default: state.get)",
    )
    parser.add_argument(
        "params",
        nargs="*",
        help="NAME=VALUE parameters, values read as JSON when they parse (notes='[...]')",
    )
    parser.add_argument("--instance", help="instance ID (default: the first running editor)")
    parser.add_argument("--list", action="store_true", help="list running editor instances")
    parser.add_argument("-t", "--timeout", type=float, default=CONNECT_TIMEOUT, help="seconds to wait for an answer")
    parser.add_argument("-c", "--compact", action="store_true", help="print the result on one line")
    parser.add_argument("-w", "--watch", action="store_true", help="keep reading the server's events after the answer")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        method, params = _resolve_call(args)
    except ValueError as error:
        print(f"namioto-ctl: {error}", file=sys.stderr)
        return 2
    if args.list:
        _print([instance.to_dict() for instance in list_instances()], args.compact)
        return 0
    try:
        endpoint = resolve_endpoint(args.instance)
        with Client(endpoint, timeout=args.timeout) as client:
            result = client.request(method, params, timeout=args.timeout)
            _print(result, args.compact)
            if args.watch:
                for event in client.events():
                    if event is not None:
                        _print(event, args.compact)
    except IpcError as error:
        print(f"namioto-ctl: {error.message} ({error.code})", file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(f"namioto-ctl: cannot reach {endpoint.describe()}: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
