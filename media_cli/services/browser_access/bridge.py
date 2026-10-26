"""Per-session authenticated messaging; no browser-debug endpoint is exposed.

The private extension polls once per command and submits a matching result.
Provider URL/operation validation belongs to the browser-session owner, not this
transport. Credentials must only be written inside its private app profile.
"""

from __future__ import annotations

import hmac
import json
import math
import secrets
import socket
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from .profiles import BrowserAccessCancelled

_CONNECTION_SECONDS = 3.0


class BrowserBridgeClosed(RuntimeError):
    """The session's messaging channel is not running."""


class BrowserRequestTimeout(TimeoutError):
    """A command exceeded its total queue-and-response deadline."""


def _reject_constant(value: str) -> None:
    raise ValueError("Nonfinite JSON value")


@dataclass
class _Pending:
    id: str
    message: bytes = field(repr=False)
    event: threading.Event = field(default_factory=threading.Event, repr=False)
    claimed: bool = False
    reply: dict[str, Any] | None = field(default=None, repr=False)


class _Server(HTTPServer):
    allow_reuse_address = False

    def __init__(self, owner: BrowserBridge):
        self.owner = owner
        self._clients: dict[socket.socket, threading.Timer] = {}
        self._clients_lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), _Handler)

    def get_request(self):
        connection, address = super().get_request()
        with self._clients_lock:
            if self.owner._closed.is_set():
                connection.close()
                raise OSError("Bridge closed")
            connection.settimeout(1)
            timer = threading.Timer(_CONNECTION_SECONDS, self._expire_connection, args=(connection,))
            timer.daemon = True
            self._clients[connection] = timer
            try:
                timer.start()
            except RuntimeError:
                self._clients.pop(connection, None)
                connection.close()
                raise OSError("Browser connection deadline unavailable") from None
        return connection, address

    def _expire_connection(self, connection: socket.socket) -> None:
        with self._clients_lock:
            if connection in self._clients:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def close_request(self, request):
        with self._clients_lock:
            timer = self._clients.pop(request, None)
        if timer is not None:
            timer.cancel()
            timer.join(timeout=1)
        super().close_request(request)

    def interrupt_clients(self) -> None:
        with self._clients_lock:
            for connection, timer in self._clients.items():
                timer.cancel()
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def handle_error(self, request, client_address):
        # HTTP/JSON errors must not dump extension credentials or provider data.
        pass


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, *args):
        pass

    def _send(self, status: int, body: bytes = b"") -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _authorized(self) -> bool:
        owner = self.server.owner
        if (self.headers.get("Host") != owner._host
                or not hmac.compare_digest(self.headers.get("Authorization", "").encode("utf-8"),
                                           ("Bearer " + owner._token).encode("ascii"))):
            self._send(403)
            return False
        if owner._closed.is_set():
            self._send(410)
            return False
        return True

    def do_GET(self):
        if not self._authorized():
            return
        if self.path != "/command":
            self._send(404)
            return
        with self.server.owner._state_lock:
            pending = self.server.owner._pending
            message = None
            if pending is not None and not pending.claimed:
                pending.claimed = True
                message = pending.message
        self._send(200 if message is not None else 204, message or b"")

    def do_POST(self):
        if not self._authorized():
            return
        if self.path != "/result":
            self._send(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send(400)
            return
        if length > self.server.owner.max_message_bytes:
            self._send(413)
            return
        if (length <= 0 or self.headers.get("Transfer-Encoding") is not None
                or self.headers.get_content_type() != "application/json"):
            self._send(400)
            return
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("Incomplete JSON")
            value = json.loads(raw, parse_constant=_reject_constant)
            if not isinstance(value, dict) or not isinstance(value.get("result"), dict):
                raise ValueError("Invalid result envelope")
            self.server.owner._encode(value)
        except (ValueError, TypeError, RecursionError, OSError):
            self._send(400)
            return
        with self.server.owner._state_lock:
            pending = self.server.owner._pending
            if (pending is None or not pending.claimed or pending.reply is not None
                    or value.get("id") != pending.id):
                status = 409
            else:
                pending.reply = value["result"]
                pending.event.set()
                status = 204
        self._send(status)


class BrowserBridge:
    """One isolated browser session's bounded, authenticated command channel."""

    def __init__(self, *, max_message_bytes: int = 2 * 1024 * 1024):
        if (isinstance(max_message_bytes, bool) or not isinstance(max_message_bytes, int)
                or not 64 <= max_message_bytes <= 16 * 1024 * 1024):
            raise ValueError("Invalid browser message limit")
        self.max_message_bytes = max_message_bytes
        self._token = secrets.token_urlsafe(32)
        self._host = ""
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._request_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._closed = threading.Event()
        self._pending: _Pending | None = None

    def __enter__(self) -> BrowserBridge:
        with self._state_lock:
            if self._closed.is_set():
                raise BrowserBridgeClosed("Browser bridge is closed")
            if self._server is not None:
                raise RuntimeError("Browser bridge is already running")
            server = _Server(self)
            self._host = f"127.0.0.1:{server.server_port}"
            self._server = server
            self._thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            try:
                self._thread.start()
            except Exception:
                server.server_close()
                self._closed.set()
                raise
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def extension_config(self) -> dict[str, str]:
        self._check_running()
        return {"endpoint": "http://" + self._host, "token": self._token}

    def _encode(self, value: dict[str, Any]) -> bytes:
        try:
            data = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError, RecursionError):
            raise ValueError("Invalid browser JSON message") from None
        if len(data) > self.max_message_bytes:
            raise ValueError("Browser message exceeds size limit")
        return data

    def _check_running(self) -> None:
        if self._closed.is_set() or self._server is None:
            raise BrowserBridgeClosed("Browser bridge is not running")

    def _check_wait(self, deadline: float, cancel: threading.Event | None) -> float:
        if cancel is not None and cancel.is_set():
            raise BrowserAccessCancelled("Browser request cancelled")
        self._check_running()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BrowserRequestTimeout("Browser request timed out")
        return min(0.05, remaining)

    def request(self, payload: dict[str, Any], *, timeout: float = 30,
                cancel: threading.Event | None = None) -> dict[str, Any]:
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("Browser request timeout must be finite and positive")
        deadline = time.monotonic() + timeout
        if not math.isfinite(deadline) or not isinstance(payload, dict):
            raise ValueError("Invalid browser request")
        pending = _Pending(secrets.token_hex(16), b"")
        pending.message = self._encode({"id": pending.id, "payload": payload})
        while not self._request_lock.acquire(timeout=self._check_wait(deadline, cancel)):
            pass
        try:
            self._check_wait(deadline, cancel)
            with self._state_lock:
                self._pending = pending
            while True:
                interval = self._check_wait(deadline, cancel)
                with self._state_lock:
                    if pending.reply is not None:
                        self._check_wait(deadline, cancel)
                        return pending.reply
                pending.event.wait(interval)
        finally:
            with self._state_lock:
                if self._pending is pending:
                    self._pending = None
            self._request_lock.release()

    def close(self) -> None:
        with self._close_lock:
            with self._state_lock:
                if self._closed.is_set():
                    return
                self._closed.set()
                if self._pending is not None:
                    self._pending.event.set()
            if self._server is not None:
                self._server.interrupt_clients()
                if self._thread is not None and self._thread.is_alive():
                    self._server.shutdown()
                self._server.server_close()
            if self._thread is not None:
                self._thread.join(timeout=2)
