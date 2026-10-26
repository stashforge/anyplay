"""Run Senshi's rotating player handshake with a permission-restricted worker."""

from __future__ import annotations

import base64
import json
import os
import re
import select
import subprocess
import sys
import time
from pathlib import Path
from threading import Event
from typing import Any
from urllib.parse import urlsplit

from ..core.api import ProviderDependencyError
from ..core.processes import is_termux
from ..services.http import UA as USER_AGENT
from ..services.network_affinity import ipv4_curl_options
from ..services.requirements import require_executable, require_python

_RUNTIME = "https://cdn.vidcloud.se/vjs/vendor.js"
_LIMIT = 1048576


class SenshiRuntimeError(RuntimeError):
    """Fixed messages only: never expose signed URLs, protocol records or keys."""


def _check(deadline: float, cancel: Event | None) -> float:
    if cancel is not None and cancel.is_set():
        raise SenshiRuntimeError("Senshi extraction cancelled")
    remaining = deadline - time.monotonic()
    if remaining < .001:
        raise SenshiRuntimeError("Senshi extraction timed out")
    return remaining


def fetch_bytes(session, method: str, url: str, headers: dict, body: bytes | None, deadline: float,
                cancel: Event | None, limit: int = _LIMIT,
                allow_redirect_response: bool = False) -> tuple[Any, bytes]:
    """Bounded transport; callers validate fixed origins and each redirect target."""
    content = bytearray()
    from curl_cffi.curl import CURL_WRITEFUNC_ERROR

    def receive(chunk: bytes) -> int:
        if len(content) + len(chunk) > limit or (cancel is not None and cancel.is_set()):
            return CURL_WRITEFUNC_ERROR
        content.extend(chunk)
        return len(chunk)
    try:
        response = session.request(method, url, headers=headers, data=body, allow_redirects=False,
                                   timeout=min(10, _check(deadline, cancel)), content_callback=receive)
    except Exception:
        _check(deadline, cancel)
        raise SenshiRuntimeError("Senshi runtime transport failed") from None
    _check(deadline, cancel)
    if not 200 <= response.status_code < 300 and not (allow_redirect_response and response.status_code in {301, 302, 303, 307, 308}):
        raise SenshiRuntimeError("Senshi runtime HTTP request failed")
    return response, bytes(content)


def _fetch_runtime(session, deadline: float, cancel: Event | None) -> str:
    _, content = fetch_bytes(session, "GET", _RUNTIME, {"Referer": "https://senshi.to/", "User-Agent": USER_AGENT},
                       None, deadline, cancel, limit=512 * 1024)
    return content.decode("utf-8")


def _write(fd: int, value: dict, deadline: float, cancel: Event | None) -> None:
    data = (json.dumps(value) + "\n").encode()
    if len(data) > 2 * _LIMIT:
        raise SenshiRuntimeError("Senshi protocol record too large")
    while data:
        if select.select([], [fd], [], min(.05, _check(deadline, cancel)))[1]:
            try:
                written = os.write(fd, data[:16384])
            except BlockingIOError:
                continue
            data = data[written:]


def _request(message: dict, session, deadline: float, cancel: Event | None) -> dict:
    url = message.get("url")
    if not isinstance(url, str) or len(url) > 8192:
        raise SenshiRuntimeError("Senshi runtime destination not permitted")
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname != "s.vidcloud.se" or parts.port not in (None, 443)
            or parts.username is not None or parts.password is not None or "#" in url
            or any(ord(char) <= 32 or char == "\\" for char in url)):
        raise SenshiRuntimeError("Senshi runtime destination not permitted")
    method = message.get("method", "GET")
    headers = message.get("headers", {})
    if method not in {"GET", "POST"} or not isinstance(headers, dict) or len(headers) > 16:
        raise SenshiRuntimeError("Senshi runtime request not permitted")
    safe = {"Referer": "https://senshi.to/", "Origin": "https://senshi.to", "User-Agent": USER_AGENT}
    for key, value in headers.items():
        if (not isinstance(key, str) or key.casefold() not in {"accept", "content-type"}
                or not isinstance(value, str) or len(value) > 1024 or any(ord(c) < 32 or ord(c) > 126 for c in value)):
            raise SenshiRuntimeError("Senshi runtime headers not permitted")
        safe[key] = value
    encoded = message.get("body")
    if encoded is not None and (not isinstance(encoded, str) or len(encoded) > 350000):
        raise SenshiRuntimeError("Senshi protocol record too large")
    body = base64.b64decode(encoded, validate=True) if encoded is not None else None
    response, data = fetch_bytes(session, method, url, safe, body, deadline, cancel)
    return {"id": message["id"], "status": response.status_code,
            "headers": {k: v for k, v in response.headers.items() if k.casefold() != "set-cookie"},
            "body": base64.b64encode(data).decode()}


def _linux_sandbox_tools() -> tuple[str, str]:
    if sys.platform != "linux":
        raise ProviderDependencyError(
            "Senshi local extraction requires Linux with bubblewrap, util-linux and Node.js 26 or newer. "
            "Termux uses Anyplay WebView instead. Choose another source or provider on this platform."
        )
    return (require_executable("bwrap", "Senshi local extraction"),
            require_executable("prlimit", "Senshi local extraction"))


def _run(node: str, runtime: str, remote_id: int, session, deadline: float, cancel: Event | None) -> list[dict]:
    worker = Path(__file__).with_suffix(".cjs").resolve()
    sandbox, limiter = _linux_sandbox_tools()
    # The OS sandbox exposes no home/config files or network namespace. Node's
    # permission layer is additional defense; node:vm is not a security boundary.
    # Node reserves roughly 1.3 GiB of virtual space before using the heap.
    # RLIMIT_AS also bounds ArrayBuffer/WASM allocations, unlike the V8 heap flag.
    command = [limiter, "--core=0", "--as=1610612736", "--", sandbox, "--unshare-all",
               "--die-with-parent", "--new-session", "--ro-bind", "/usr", "/usr"]
    for library in ("/lib", "/lib64"):
        if Path(library).exists():
            command.extend(["--ro-bind", library, library])
    command.extend(["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--dir", "/app",
                    "--ro-bind", str(worker), "/app/worker.cjs", "--ro-bind", node, "/app/node",
                    "--chdir", "/app", "--clearenv", "--setenv", "LANG", "C.UTF-8",
                    "/app/node", "--permission", "--allow-fs-read=/app/worker.cjs", "--max-old-space-size=64",
                    "--disable-wasm-trap-handler",
                    "/app/worker.cjs"])
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        env={"PATH": os.defpath, "LANG": "C.UTF-8"}, cwd=worker.parent,
    )
    assert process.stdin is not None and process.stdout is not None
    try:
        os.set_blocking(process.stdin.fileno(), False)
        os.set_blocking(process.stdout.fileno(), False)
        _write(process.stdin.fileno(), {"runtime": runtime, "remoteId": remote_id, "userAgent": USER_AGENT}, deadline, cancel)
        pending = bytearray()
        count = 0
        while True:
            if b"\n" not in pending:
                if not select.select([process.stdout], [], [], min(.05, _check(deadline, cancel)))[0]:
                    continue
                try:
                    chunk = os.read(process.stdout.fileno(), 16384)
                except BlockingIOError:
                    continue
                if not chunk:
                    raise SenshiRuntimeError("Senshi player runtime failed")
                pending.extend(chunk)
                if len(pending) > 2 * _LIMIT:
                    raise SenshiRuntimeError("Senshi protocol record too large")
                if b"\n" not in pending:
                    continue
            line, _, rest = pending.partition(b"\n")
            pending = bytearray(rest)
            message = json.loads(line)
            _check(deadline, cancel)
            if not isinstance(message, dict):
                raise SenshiRuntimeError("Senshi player runtime failed")
            if message.get("kind") == "fetch":
                count += 1
                if count > 8:
                    raise SenshiRuntimeError("Senshi runtime request limit reached")
                _write(process.stdin.fileno(), _request(message, session, deadline, cancel), deadline, cancel)
            elif message.get("kind") == "result":
                result = message.get("result")
                if isinstance(result, dict):
                    result = [result]
                if not isinstance(result, list) or not 1 <= len(result) <= 16 or not all(isinstance(x, dict) for x in result):
                    raise SenshiRuntimeError("Senshi returned no stream sources")
                return result
            else:
                raise SenshiRuntimeError("Senshi player runtime failed")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
        process.stdin.close()
        process.stdout.close()


def _resolve_webview(remote_id: int, *, deadline: float, cancel: Event | None) -> list[dict]:
    from .senshi_webview import resolve
    return resolve(remote_id, deadline=deadline, cancel=cancel)


def resolve_vidcloud(remote_id: int | str, *, timeout: float = 30, cancel: Event | None = None) -> list[dict]:
    """Resolve current source metadata without browser profiles or personal data."""
    try:
        if isinstance(remote_id, bool) or not re.fullmatch(r"[1-9][0-9]{0,11}", str(remote_id)):
            raise SenshiRuntimeError("Invalid Senshi episode source ID")
        if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not 0 < timeout <= 60:
            raise SenshiRuntimeError("Invalid Senshi extraction timeout")
        deadline = time.monotonic() + timeout
        _check(deadline, cancel)
        if is_termux():
            return _resolve_webview(int(remote_id), deadline=deadline, cancel=cancel)
        _linux_sandbox_tools()
        node = require_executable("node", "Senshi local extraction (Node.js 26 or newer)")
        try:
            version = subprocess.run([node, "--version"], capture_output=True, text=True,
                                     timeout=min(2, _check(deadline, cancel)), env={"PATH": os.defpath})
        except (OSError, subprocess.TimeoutExpired):
            raise ProviderDependencyError(
                "Senshi could not run Node.js. Install a working Node.js 26 or newer and retry."
            ) from None
        if version.returncode or not re.match(r"v(?:2[6-9]|[3-9][0-9])\.", version.stdout):
            raise ProviderDependencyError(
                "Senshi local extraction requires Node.js 26 or newer. Upgrade Node.js and retry."
            )
        Session = require_python("curl_cffi.requests", "Senshi local extraction").Session

        for attempt in range(2):
            try:
                with Session(impersonate="chrome", trust_env=False, curl_options=ipv4_curl_options()) as session:
                    runtime = _fetch_runtime(session, deadline, cancel)
                    return _run(node, runtime, int(remote_id), session, deadline, cancel)
            except SenshiRuntimeError as error:
                if attempt or any(word in str(error) for word in ("not permitted", "limit", "cancel", "needs")):
                    raise
                _check(deadline, cancel)
        raise SenshiRuntimeError("Senshi local extraction failed")
    except (SenshiRuntimeError, ProviderDependencyError):
        raise
    except Exception:
        raise SenshiRuntimeError("Senshi local extraction failed") from None
