# SPDX-License-Identifier: AGPL-3.0-only
"""Qt-free Windows named-pipe transport for the JSON-lines IPC client.

Raw overlapped byte I/O preserves the same framing as Unix sockets and supports bounded waits
without a GUI event loop. Imported only on Windows; process discovery never uses os.kill there.
"""

from __future__ import annotations

import _winapi
import ctypes
import math
import time


def process_alive(pid: int) -> bool:
    try:
        handle = _winapi.OpenProcess(_winapi.SYNCHRONIZE, False, pid)
    except OSError as error:
        return error.winerror == 5
    try:
        return _winapi.WaitForSingleObject(handle, 0) == _winapi.WAIT_TIMEOUT
    finally:
        _winapi.CloseHandle(handle)


class PipeSocket:
    """The socket operations the IPC client needs, over a Windows byte-mode pipe."""

    def __init__(self, address: str, timeout: float):
        self._timeout = timeout
        deadline = time.monotonic() + timeout
        while True:
            try:
                self._handle = _winapi.CreateFile(
                    address,
                    _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
                    0,
                    _winapi.NULL,
                    _winapi.OPEN_EXISTING,
                    _winapi.FILE_FLAG_OVERLAPPED,
                    _winapi.NULL,
                )
                break
            except OSError as error:
                if error.winerror != _winapi.ERROR_PIPE_BUSY:
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("the editor's named pipe is busy") from error
                try:
                    _winapi.WaitNamedPipe(address, max(1, math.ceil(remaining * 1000)))
                except OSError as wait_error:
                    if wait_error.winerror == _winapi.ERROR_SEM_TIMEOUT:
                        raise TimeoutError("the editor's named pipe is busy") from wait_error
                    raise

    def gettimeout(self) -> float | None:
        return self._timeout

    def settimeout(self, timeout: float | None) -> None:
        self._timeout = timeout

    def _finish(self, operation, error: int) -> int:
        try:
            if error == _winapi.ERROR_IO_PENDING:
                milliseconds = _winapi.INFINITE if self._timeout is None else math.ceil(self._timeout * 1000)
                if _winapi.WaitForSingleObject(operation.event, milliseconds) == _winapi.WAIT_TIMEOUT:
                    operation.cancel()
                    count, error = operation.GetOverlappedResult(True)
                    if error == _winapi.ERROR_OPERATION_ABORTED:
                        raise TimeoutError("named pipe operation timed out")
                else:
                    count, error = operation.GetOverlappedResult(True)
            else:
                count, error = operation.GetOverlappedResult(True)
        except BaseException:
            operation.cancel()
            operation.GetOverlappedResult(True)
            raise
        if error:
            raise ctypes.WinError(error)
        return count

    def sendall(self, data: bytes) -> None:
        while data:
            operation, error = _winapi.WriteFile(self._handle, data, overlapped=True)
            count = self._finish(operation, error)
            if not count:
                raise BrokenPipeError("the editor closed the named pipe")
            data = data[count:]

    def recv(self, size: int) -> bytes:
        try:
            operation, error = _winapi.ReadFile(self._handle, size, overlapped=True)
            self._finish(operation, error)
            return bytes(operation.getbuffer())
        except OSError as error:
            if getattr(error, "winerror", None) in (_winapi.ERROR_BROKEN_PIPE, _winapi.ERROR_NETNAME_DELETED):
                return b""
            raise

    def close(self) -> None:
        if self._handle is not None:
            _winapi.CloseHandle(self._handle)
            self._handle = None
