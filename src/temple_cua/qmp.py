"""Small synchronous QMP client with bounded reads and command deadlines."""

from __future__ import annotations

import json
from pathlib import Path
import socket
import threading
import time
from typing import Any


class QMPError(RuntimeError):
    """QEMU rejected a command or disconnected from its monitor."""


class QMPTimeout(QMPError, TimeoutError):
    """A QMP greeting or command did not arrive before its deadline."""


class QMPClient:
    """One local UNIX monitor connection; asynchronous events are skipped.

    A timeout closes the connection, so late replies cannot be confused with
    replies to later commands. The client serializes commands across threads.
    """

    MAX_MESSAGE_BYTES = 8 * 1024 * 1024

    def __init__(self, path: Path | str, timeout: float = 10.0):
        if timeout <= 0:
            raise ValueError("QMP timeout must be positive")
        self.path = Path(path)
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._buffer = bytearray()
        self._next_id = 0
        self._lock = threading.RLock()

    def connect(self) -> QMPClient:
        with self._lock:
            if self._socket is not None:
                return self
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(self.timeout)
            try:
                connection.connect(str(self.path))
                self._socket = connection
                greeting = self._read_message(time.monotonic() + self.timeout)
                if "QMP" not in greeting:
                    raise QMPError("QEMU did not send a QMP greeting")
                self.execute("qmp_capabilities")
            except socket.timeout as exc:
                connection.close()
                self.close()
                raise QMPTimeout("QMP connection timed out") from exc
            except (OSError, QMPError):
                connection.close()
                self.close()
                raise
            return self

    def _read_message(self, deadline: float) -> dict[str, Any]:
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._buffer[:newline]).strip()
                del self._buffer[: newline + 1]
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeDecodeError) as exc:
                    raise QMPError("QEMU sent invalid JSON") from exc
                if not isinstance(message, dict):
                    raise QMPError("QEMU sent an invalid QMP message")
                return message
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise QMPTimeout("QMP response timed out")
            if self._socket is None:
                raise QMPError("QMP connection is closed")
            self._socket.settimeout(remaining)
            try:
                chunk = self._socket.recv(65536)
            except socket.timeout as exc:
                raise QMPTimeout("QMP response timed out") from exc
            except OSError as exc:
                raise QMPError(f"QMP receive failed: {exc}") from exc
            if not chunk:
                raise QMPError("QEMU closed its QMP connection")
            self._buffer.extend(chunk)
            if len(self._buffer) > self.MAX_MESSAGE_BYTES:
                raise QMPError("QMP message exceeded the size limit")

    def execute(
        self,
        command: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        with self._lock:
            if self._socket is None:
                raise QMPError("QMP connection is closed")
            duration = self.timeout if timeout is None else timeout
            if duration <= 0:
                raise ValueError("QMP command timeout must be positive")
            self._next_id += 1
            command_id = self._next_id
            request: dict[str, Any] = {"execute": command, "id": command_id}
            if arguments is not None:
                request["arguments"] = arguments
            deadline = time.monotonic() + duration
            try:
                self._socket.settimeout(duration)
                self._socket.sendall(json.dumps(request).encode("utf-8") + b"\n")
                while True:
                    reply = self._read_message(deadline)
                    if "event" in reply:
                        continue
                    if reply.get("id") != command_id:
                        raise QMPError("QMP reply had an unexpected command id")
                    if "error" in reply:
                        error = reply["error"]
                        raise QMPError(
                            f"QMP {command} failed: {error.get('class', 'Error')}: "
                            f"{error.get('desc', error)}"
                        )
                    if "return" not in reply:
                        raise QMPError(f"QMP {command} reply has no return value")
                    return reply["return"]
            except QMPTimeout:
                self.close()
                raise
            except OSError as exc:
                self.close()
                raise QMPError(f"QMP {command} transport failed: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            if self._socket is not None:
                self._socket.close()
                self._socket = None
            self._buffer.clear()

    def __enter__(self) -> QMPClient:
        return self.connect()

    def __exit__(self, *_: object) -> None:
        self.close()
