"""Authenticated, bounded Android companion transport (standard library only)."""

from __future__ import annotations

import argparse
import fcntl
import getpass
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_SCRIPT = 512 * 1024
MAX_RESPONSE = 1024 * 1024 + 16384
_SESSION_LOCK = threading.Lock()
_SITE_HOSTS = {
    "animepahe.pw": "animepahe", "animepahe.org": "animepahe",
    "animepahe.com": "animepahe", "animepahe.ru": "animepahe",
    "kwik.cx": "kwik", "kwik.si": "kwik",
}


def site_profile(url):
    """Resolve only fixed website origins, independently of server validation."""
    if not isinstance(url, str) or not url or len(url) > 8192:
        raise ProbeError("Invalid website URL")
    decoded = unquote(url)
    if ("#" in url or any(ord(c) <= 32 or 127 <= ord(c) <= 159 for c in url)
            or any(ord(c) < 32 or 127 <= ord(c) <= 159 or c == "\\" for c in decoded)):
        raise ProbeError("Invalid website URL")
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.hostname not in _SITE_HOSTS
                or parts.port not in (None, 443) or parts.username is not None or parts.password is not None):
            raise ValueError
    except ValueError:
        raise ProbeError("Website destination not permitted") from None
    return _SITE_HOSTS[parts.hostname]


def site_result(job, url):
    """Validate the terminal envelope and exact-target native payload together."""
    site_profile(url)
    if not isinstance(job, dict) or job.get("status") != "ok":
        raise ProbeError("Native site request did not complete")
    value = job.get("result")
    ProbeClient._validate_site_result(value, urlsplit(url).hostname)
    if value["url"] != url:
        raise ProbeError("Native site result target does not match request")
    return value


def _remaining(deadline, cancel=None):
    if cancel is not None and cancel.is_set():
        raise ProbeError("Companion operation cancelled")
    left = deadline - time.monotonic()
    if left <= 0:
        raise ProbeError("Companion operation timed out")
    return left


class ProbeError(RuntimeError):
    """Errors deliberately exclude credentials and remote response bodies."""


class CompanionUnavailable(ProbeError):
    """Connection refused: a wake may be attempted, unlike an authentication failure."""


class CompanionSetupRequired(ProbeError):
    """Missing pairing or unsupported capabilities need explicit user setup."""


_PAIRING_REQUIRED = (
    "Anyplay WebView pairing is required. Install the companion APK if needed, "
    "then run 'anyplay companion pair' to pair explicitly."
)


@contextmanager
def startup_deadline(seconds=15):
    manual = "Service startup unavailable or timed out; open Anyplay WebView manually and retry"
    if threading.current_thread() is not threading.main_thread() or any(signal.getitimer(signal.ITIMER_REAL)):
        raise ProbeError(manual)
    previous = signal.getsignal(signal.SIGALRM)

    def expired(_signum, _frame):
        raise ProbeError(manual)

    signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DeadlineStream:
    """Check the absolute deadline on each underlying read, including headers."""

    def __init__(self, source, sock, deadline):
        self.source, self.sock, self.deadline = source, sock, deadline

    def read1(self, size=-1):
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise ProbeError("Companion operation timed out")
        self.sock.settimeout(left)
        return self.source.read1(size)

    def read(self, size=-1):
        chunks = bytearray()
        while size < 0 or len(chunks) < size:
            piece = self.read1(16384 if size < 0 else min(16384, size - len(chunks)))
            if not piece:
                break
            chunks.extend(piece)
        return bytes(chunks)

    def readline(self, limit=-1):
        line = bytearray()
        while limit < 0 or len(line) < limit:
            char = self.read1(1)
            if not char:
                break
            line.extend(char)
            if char == b"\n":
                break
        return bytes(line)

    def close(self):
        self.source.close()

    def __getattr__(self, name):
        return getattr(self.source, name)


class DeadlineHTTPHandler(HTTPHandler):
    def __init__(self, deadline):
        super().__init__()
        self.deadline = deadline

    def http_open(self, request):
        deadline = self.deadline

        class Response(http.client.HTTPResponse):
            def __init__(self, sock, *args, **kwargs):
                super().__init__(sock, *args, **kwargs)
                self.fp = DeadlineStream(self.fp, sock, deadline)

        class Connection(http.client.HTTPConnection):
            response_class = Response

        return self.do_open(Connection, request)


def open_companion():
    if sys.platform != "android" and "com.termux" not in sys.prefix:
        return False
    try:
        result = subprocess.run(
            ["am", "start", "-n", "dev.anyplay.webview/.LaunchActivity"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0 or "Error" in result.stdout or "Error" in result.stderr:
            raise ProbeError("Could not open companion; open Anyplay WebView manually")
        return True
    except (OSError, subprocess.TimeoutExpired):
        raise ProbeError("Could not open companion; open Anyplay WebView manually") from None


class ProbeClient:
    def __init__(self, base_url="http://127.0.0.1:18765", token_path=None):
        try:
            parts = urlsplit(base_url)
            valid = (
                parts.scheme == "http"
                and parts.hostname == "127.0.0.1"
                and parts.username is None
                and parts.password is None
                and parts.path == ""
                and not parts.query
                and not parts.fragment
                and parts.port is not None
                and 1 <= parts.port <= 65535
                and base_url == f"http://127.0.0.1:{parts.port}"
            )
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise ProbeError("Only an exact local HTTP origin is permitted")
        self.base_url = base_url
        self.token_path = Path(token_path or Path.home() / ".local/state/anyplay-webview/token").absolute()
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def _private_dir(self, create=False, exclusive=False, deadline=None):
        parent = self.token_path.parent
        if any(p.is_symlink() for p in (self.token_path, parent, *parent.parents)):
            raise ProbeError("Credential paths must not contain symbolic links")
        fd = None
        try:
            if create:
                parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ProbeError("Credential directory must be private (0700)")
            lock = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            deadline = min(time.monotonic() + 5, deadline) if deadline is not None else time.monotonic() + 5
            while True:
                if time.monotonic() >= deadline:
                    raise ProbeError("Credential storage is busy; retry shortly")
                try:
                    fcntl.flock(fd, lock | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ProbeError("Credential storage is busy; retry shortly") from None
                    time.sleep(min(0.02, max(0, deadline - time.monotonic())))
            return fd
        except ProbeError:
            if fd is not None:
                os.close(fd)
            raise
        except FileNotFoundError:
            if fd is not None:
                os.close(fd)
            if not create:
                raise CompanionSetupRequired(_PAIRING_REQUIRED) from None
            raise ProbeError("Private credential directory is unavailable") from None
        except OSError:
            if fd is not None:
                os.close(fd)
            raise ProbeError("Private credential directory is unavailable") from None

    def _load_token(self, deadline=None):
        directory = self._private_dir(deadline=deadline)
        try:
            fd = os.open(self.token_path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
            with os.fdopen(fd, "r", encoding="ascii") as source:
                info = os.fstat(source.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_nlink != 1
                ):
                    raise ProbeError("Credential file must be private (0600)")
                token = source.read(66).strip()
                if not re.fullmatch(r"[0-9a-f]{64}", token):
                    raise ProbeError("Invalid stored credential; pair explicitly")
                return token
        except FileNotFoundError:
            raise CompanionSetupRequired(_PAIRING_REQUIRED) from None
        except (OSError, UnicodeError):
            raise ProbeError("No readable credential; pair explicitly first") from None
        finally:
            os.close(directory)

    def _store_token(self, token):
        directory = self._private_dir(create=True, exclusive=True)
        created = None
        saved = False
        try:
            fd = os.open(
                self.token_path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
            )
            with os.fdopen(fd, "w", encoding="ascii") as destination:
                created = os.fstat(destination.fileno())
                destination.write(token + "\n")
                destination.flush()
                os.fsync(destination.fileno())
            os.fsync(directory)
            saved = True
        except OSError:
            raise ProbeError("Could not store credential safely; existing pairing was not replaced") from None
        finally:
            if created is not None and not saved:
                try:
                    current = os.stat(self.token_path.name, dir_fd=directory, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                        os.unlink(self.token_path.name, dir_fd=directory)
                except OSError:
                    pass
            os.close(directory)

    def _request(
        self, method, path, body=None, authenticated=True, timeout=5, deadline=None, response_limit=MAX_RESPONSE
    ):
        if deadline is not None:
            deadline = min(deadline, time.monotonic() + timeout)

        def remaining():
            left = timeout if deadline is None else min(timeout, deadline - time.monotonic())
            if left <= 0:
                raise ProbeError("Companion operation timed out")
            return left

        remaining()
        headers = {"Accept": "application/json"}
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        token = None
        server_id = nonce = timestamp = None
        if authenticated:
            token = self._load_token(deadline=deadline)
            client_nonce = secrets.token_hex(32)
            proof = self._request(
                "POST",
                "/v1/prove",
                {"client_nonce": client_nonce},
                authenticated=False,
                timeout=remaining(),
                deadline=deadline,
            )
            server_id = proof.get("server_id")
            supplied = proof.get("proof")
            if (
                not isinstance(server_id, str)
                or not re.fullmatch(r"[0-9a-f]{64}", server_id)
                or not isinstance(supplied, str)
                or not re.fullmatch(r"[0-9a-f]{64}", supplied)
                or not hmac.compare_digest(
                    self._mac(token, "server-proof:v1\n" + client_nonce + "\n" + server_id), supplied
                )
            ):
                raise ProbeError("Companion server authentication failed")
            nonce = secrets.token_hex(32)
            timestamp = str(int(time.time() * 1000))
            digest = hashlib.sha256(data or b"").hexdigest()
            signature = self._mac(
                token,
                "request:v1\n"
                + server_id
                + "\n"
                + nonce
                + "\n"
                + timestamp
                + "\n"
                + method
                + "\n"
                + path
                + "\n"
                + digest,
            )
            headers.update(
                {
                    "Authorization": "Anyplay-HMAC " + nonce + ":" + signature,
                    "X-Anyplay-Server": server_id,
                    "X-Anyplay-Time": timestamp,
                    "X-Anyplay-Content-SHA256": digest,
                }
            )
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            opener = (
                self.opener
                if deadline is None
                else build_opener(ProxyHandler({}), NoRedirect(), DeadlineHTTPHandler(deadline))
            )
            with opener.open(request, timeout=remaining()) as response:
                if response.status != 200 or response.headers.get_content_type() != "application/json":
                    raise ProbeError("Unexpected companion response")
                limit = response_limit if authenticated else 4096
                chunks = bytearray()
                while True:
                    remaining()
                    chunk = response.read1(min(16384, limit + 1 - len(chunks)))
                    if not chunk:
                        break
                    chunks.extend(chunk)
                    if len(chunks) > limit:
                        raise ProbeError("Companion response is too large")
                raw = bytes(chunks)
                remaining()
                if len(raw) > limit:
                    raise ProbeError("Companion response is too large")
                if authenticated:
                    expected = self._mac(
                        token,
                        "response:v1\n"
                        + server_id
                        + "\n"
                        + nonce
                        + "\n"
                        + timestamp
                        + "\n200\n"
                        + hashlib.sha256(raw).hexdigest(),
                    )
                    received = response.headers.get("X-Anyplay-Response", "")
                    if not re.fullmatch(r"[0-9a-f]{64}", received) or not hmac.compare_digest(expected, received):
                        raise ProbeError("Companion response authentication failed")
            result = json.loads(raw.decode("utf-8"))
        except HTTPError as error:
            error.close()
            if error.code in (401, 403):
                raise ProbeError("Companion authentication failed; no automatic re-pairing") from None
            raise ProbeError(f"Companion rejected request (HTTP {error.code})") from None
        except URLError as error:
            if isinstance(error.reason, ConnectionRefusedError):
                raise CompanionUnavailable("Local companion is stopped") from None
            raise ProbeError("Local companion unavailable or returned invalid data") from None
        except (OSError, UnicodeError, ValueError, RecursionError):
            raise ProbeError("Local companion unavailable or returned invalid data") from None
        if (
            not isinstance(result, dict)
            or result.get("app") != "anyplay-webview"
            or type(result.get("protocol")) is not int
            or result["protocol"] != 1
            or result.get("ok") is not True
        ):
            raise ProbeError("Incompatible or unsuccessful companion response")
        return result

    @staticmethod
    def _mac(token, message):
        return hmac.new(token.encode("ascii"), message.encode("utf-8"), hashlib.sha256).hexdigest()

    def pair(self, code):
        directory = self._private_dir(create=True)
        os.close(directory)
        if self.token_path.exists() or self.token_path.is_symlink():
            raise ProbeError("Existing pairing must be revoked and removed explicitly")
        if not isinstance(code, str) or not re.fullmatch(r"[0-9]{12}", code):
            raise ProbeError("Pairing code must contain 12 digits")
        response = self._request("POST", "/v1/pair", {"code": code}, authenticated=False)
        token = response.get("token")
        if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{64}", token):
            raise ProbeError("Companion returned an invalid pairing credential")
        try:
            self._store_token(token)
        except ProbeError:
            raise ProbeError(
                "Companion paired, but credential saving failed. Revoke pairing in the app before retrying."
            ) from None
        return {"paired": True}

    def has_pairing(self):
        """Validate saved pairing without returning credentials or creating storage."""
        try:
            self.token_path.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            raise ProbeError("Saved pairing is unavailable; existing data was not changed") from None
        self._load_token()
        return True

    def health(self, timeout=5):
        return self._request("GET", "/v1/health", timeout=timeout, deadline=time.monotonic() + timeout)

    def start(self):
        with startup_deadline():
            return self._start()

    def ensure_started(self, deadline, cancel=None):
        """Worker-thread entry point: no process alarms, Activities or auto-pairing."""
        _remaining(deadline, cancel)
        try:
            health = self._request(
                "GET", "/v1/health", timeout=min(0.5, _remaining(deadline, cancel)), deadline=deadline
            )
        except CompanionUnavailable:
            token = self._load_token(deadline=deadline)
            self._wake(token, min(5, _remaining(deadline, cancel)))
            health = None
            deadline = min(deadline, time.monotonic() + 8)
        while True:
            _remaining(deadline, cancel)
            if health is not None and health.get("worker") != "stopping":
                return health
            if health is not None:
                time.sleep(min(0.1, _remaining(deadline, cancel)))
            try:
                health = self._request(
                    "GET", "/v1/health", timeout=min(0.5, _remaining(deadline, cancel)), deadline=deadline
                )
            except CompanionUnavailable:
                health = None
                time.sleep(min(0.1, _remaining(deadline, cancel)))

    @contextmanager
    def session(self, profile, *, deadline, cancel=None):
        """Serialize local callers; never close a session this lease did not create."""
        owned = None
        while True:
            if _SESSION_LOCK.acquire(timeout=min(0.05, _remaining(deadline, cancel))):
                break
        try:
            _remaining(deadline, cancel)
            health = self.ensure_started(deadline, cancel)
            capability = "site_get" if profile in ("animepahe", "kwik") else "mediated_fetch"
            if health.get(capability) is not True:
                raise CompanionSetupRequired(
                    "Companion update required for " + capability + ". Update the Anyplay WebView APK, then retry."
                )
            _remaining(deadline, cancel)
            owned = self.create_session(profile, deadline=deadline)["session_id"]
            yield self._identifier(owned)
        finally:
            try:
                if owned is not None:
                    self._request(
                        "POST",
                        "/v1/sessions/" + self._identifier(owned) + "/close",
                        {},
                        timeout=0.5,
                        deadline=time.monotonic() + 0.5,
                    )
            except ProbeError:
                pass
            finally:
                _SESSION_LOCK.release()

    def _start(self):
        try:
            return self.health(timeout=0.5)
        except CompanionUnavailable:
            pass
        token = self._load_token()
        manual = "Could not start companion service; open Anyplay WebView manually and retry"
        self._wake(token)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                return self.health(timeout=min(0.5, remaining / 2))
            except CompanionUnavailable:
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        raise ProbeError(manual)

    def _wake(self, token, timeout=5):
        manual = "Could not start companion service; open Anyplay WebView manually and retry"
        if sys.platform != "android" and "com.termux" not in sys.prefix:
            raise ProbeError(manual)
        timestamp = str(int(time.time() * 1000))
        proof = self._mac(token, "wake:v1\n" + timestamp)
        try:
            result = subprocess.run(
                [
                    "am",
                    "broadcast",
                    "--receiver-foreground",
                    "-n",
                    "dev.anyplay.webview/.WakeReceiver",
                    "-a",
                    "dev.anyplay.webview.WAKE",
                    "--es",
                    "timestamp",
                    timestamp,
                    "--es",
                    "proof",
                    proof,
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            # Some Termux am implementations map Android RESULT_OK (-1) to exit 1.
            completed = re.search(r"(?m)^Broadcast completed: result=-1\s*$", result.stdout)
            if (result.returncode != 0 and not completed) or "Error" in result.stdout or "Error" in result.stderr:
                raise ProbeError(manual)
        except FileNotFoundError:
            raise CompanionSetupRequired(
                "Companion startup requires 'am'. Install it with: pkg install termux-am, then retry."
            ) from None
        except (OSError, subprocess.TimeoutExpired):
            raise ProbeError(manual) from None

    @staticmethod
    def _identifier(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
            raise ProbeError("Invalid session or job identifier")
        return value

    def create_session(self, profile="probe", deadline=None):
        if profile not in ("probe", "probe_a", "probe_b", "senshi", "animepahe", "kwik"):
            raise ProbeError("Unknown offline profile")
        return self._request(
            "POST", "/v1/sessions", {} if profile == "probe" else {"profile": profile}, deadline=deadline
        )

    def close_session(self, session_id):
        return self._request("POST", "/v1/sessions/" + self._identifier(session_id) + "/close", {})

    def submit(self, session_id, script, timeout_ms=30000):
        self._identifier(session_id)
        if (
            not isinstance(script, str)
            or len(script.encode("utf-8")) > MAX_SCRIPT
            or type(timeout_ms) is not int
            or not 1 <= timeout_ms <= 120000
        ):
            raise ProbeError("Script or deadline exceeds companion limits")
        return self._request("POST", "/v1/jobs", {"session_id": session_id, "script": script, "timeout_ms": timeout_ms})

    def poll(self, job_id):
        return self._request("GET", "/v1/jobs/" + self._identifier(job_id))

    def cancel(self, job_id):
        return self._request("POST", "/v1/jobs/" + self._identifier(job_id) + "/cancel", {})

    def shutdown(self):
        return self._request("POST", "/v1/shutdown", {})

    def run(self, session_id, script, timeout_ms=30000):
        response = self.submit(session_id, script, timeout_ms)
        job_id = response["job"]["id"]
        deadline = time.monotonic() + timeout_ms / 1000 + 5
        while time.monotonic() < deadline:
            job = self.poll(job_id)["job"]
            if job["status"] not in ("queued", "running"):
                return job
            time.sleep(0.1)
        self.cancel(job_id)
        raise ProbeError("Timed out waiting for companion job")

    def reveal_verification(self, job_id, ticket, host, *, deadline):
        """Reveal an existing native job; never put a URL or pairing token in an intent."""
        self._identifier(job_id)
        if not isinstance(ticket, str) or not re.fullmatch(r"[0-9a-f]{64}", ticket) or host not in _SITE_HOSTS:
            raise ProbeError("Invalid verification ticket")
        try:
            result = subprocess.run(
                ["am", "start", "-n", "dev.anyplay.webview/.worker.VerificationActivity",
                 "--es", "job_id", job_id, "--es", "ticket", ticket],
                capture_output=True, text=True, timeout=min(5, _remaining(deadline)), check=False,
            )
            if ((result.returncode != 0 and "Starting: Intent" not in result.stdout)
                    or "Error" in result.stdout or "Error" in result.stderr):
                raise ProbeError("Could not show site verification")
        except (OSError, subprocess.TimeoutExpired):
            raise ProbeError("Could not show site verification") from None

    def run_site_get(self, session_id, url, deadline, cancel=None, on_interaction=None):
        """Return a terminal job dict; successful payloads live under result."""
        self._identifier(session_id)
        site_profile(url)
        host = urlsplit(url).hostname
        job_id = None
        terminal = False
        shown = set()
        try:
            _remaining(deadline, cancel)
            health = self._request("GET", "/v1/health", deadline=deadline)
            _remaining(deadline, cancel)
            if health.get("site_get") is not True:
                raise CompanionSetupRequired(
                    "Companion update required for site access. Update the Anyplay WebView APK, then retry."
                )
            response = self._request("POST", "/v1/jobs", {
                "session_id": session_id, "script": json.dumps({"url": url}, separators=(",", ":")),
                "timeout_ms": max(1, min(120000, int(_remaining(deadline, cancel) * 1000))),
            }, deadline=deadline)
            job_id = self._identifier(response["job"]["id"])
            while True:
                _remaining(deadline, cancel)
                job = self._request("GET", "/v1/jobs/" + job_id, deadline=deadline)["job"]
                _remaining(deadline, cancel)
                if job.get("status") not in ("queued", "running"):
                    if job.get("status") not in ("ok", "failed", "cancelled", "timeout", "unsupported"):
                        raise ProbeError("Invalid native site job")
                    if job.get("status") == "ok":
                        site_result(job, url)
                    _remaining(deadline, cancel)
                    terminal = True
                    return job
                verification = job.get("verification")
                if verification is not None:
                    if (job.get("status") != "running" or not isinstance(verification, dict)
                            or verification.get("host") != host
                            or not isinstance(verification.get("ticket"), str)
                            or not re.fullmatch(r"[0-9a-f]{64}", verification["ticket"])):
                        raise ProbeError("Invalid verification ticket")
                    ticket = verification["ticket"]
                    if ticket not in shown:
                        shown.add(ticket)
                        if on_interaction is None:
                            self.reveal_verification(job_id, ticket, host, deadline=deadline)
                        else:
                            try:
                                on_interaction(job_id, ticket, host)
                            except Exception:
                                raise ProbeError("Could not show site verification") from None
                time.sleep(min(0.05, _remaining(deadline, cancel)))
        finally:
            if job_id is not None and not terminal:
                try:
                    self._request("POST", "/v1/jobs/" + job_id + "/cancel", {}, timeout=0.5,
                                  deadline=time.monotonic() + 0.5)
                except Exception:
                    pass

    @staticmethod
    def _validate_site_result(value, host):
        try:
            if not isinstance(value, dict) or set(value) != {"url", "status", "body", "headers", "user_agent"}:
                raise ValueError
            site_profile(value["url"])
            if urlsplit(value["url"]).hostname != host:
                raise ValueError
            if type(value["status"]) is not int or not 100 <= value["status"] <= 599 or 300 <= value["status"] < 400:
                raise ValueError
            if not isinstance(value["body"], str) or len(value["body"].encode("utf-8")) > 262144:
                raise ValueError
            headers = value["headers"]
            if not isinstance(headers, dict) or len(headers) > 64 or len(json.dumps(headers).encode()) > 32768:
                raise ValueError
            for key, item in headers.items():
                if (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,128}", key)
                        or not isinstance(item, str) or any(ord(c) < 32 or ord(c) == 127 for c in item)):
                    raise ValueError
            ua = value["user_agent"]
            if not isinstance(ua, str) or not 1 <= len(ua) <= 4096 or any(ord(c) < 32 or ord(c) > 126 for c in ua):
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            raise ProbeError("Invalid native site response") from None

    def run_mediated(self, session_id, script, transport, deadline, cancel=None):
        """One bounded job; the trusted caller owns destination and HTTP policy."""
        self._identifier(session_id)
        job_id = None
        terminal = False

        def remaining_ms():
            if cancel is not None and cancel.is_set():
                raise ProbeError("Companion operation cancelled")
            left = deadline - time.monotonic()
            if left < 0.001:
                raise ProbeError("Companion operation timed out")
            return min(120000, int(left * 1000))

        try:
            remaining_ms()
            if not isinstance(script, str) or len(script.encode("utf-8")) > 786432 or not callable(transport):
                raise ProbeError("Invalid mediated job")
            health = self._request("GET", "/v1/health", deadline=deadline)
            if health.get("mediated_fetch") is not True:
                raise CompanionSetupRequired(
                    "Companion update required for mediated fetch. Update the Anyplay WebView APK, then retry."
                )
            response = self._request(
                "POST",
                "/v1/jobs",
                {"session_id": session_id, "script": script, "mediated": True, "timeout_ms": remaining_ms()},
                deadline=deadline,
            )
            job_id = self._identifier(response["job"]["id"])
            seen = set()
            while True:
                remaining_ms()
                job = self._request("GET", "/v1/jobs/" + job_id, deadline=deadline, response_limit=2097152 + 16384)[
                    "job"
                ]
                if job.get("status") not in ("queued", "running"):
                    terminal = True
                    return job
                request = job.get("request")
                if request is not None:
                    if (
                        not isinstance(request, dict)
                        or type(request.get("id")) is not int
                        or not 1 <= request["id"] <= 8
                        or request["id"] in seen
                        or len(json.dumps(request).encode()) > 524288
                    ):
                        raise ProbeError("Invalid mediated request")
                    seen.add(request["id"])
                    try:
                        answer = transport(request)
                        if (
                            not isinstance(answer, dict)
                            or type(answer.get("id")) is not int
                            or answer["id"] != request["id"]
                            or len(json.dumps(answer).encode()) > 2097152
                        ):
                            raise ValueError()
                    except Exception:
                        raise ProbeError("Mediated request failed") from None
                    remaining_ms()
                    self._request("POST", "/v1/jobs/" + job_id + "/reply", answer, deadline=deadline)
                else:
                    time.sleep(min(0.05, remaining_ms() / 1000))
        finally:
            if job_id is not None and not terminal:
                try:
                    self._request(
                        "POST", "/v1/jobs/" + job_id + "/cancel", {}, timeout=0.5, deadline=time.monotonic() + 0.5
                    )
                except Exception:
                    pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="Anyplay WebView foundation diagnostic")
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("command", choices=("pair", "start", "health", "smoke", "shutdown"))
    args = parser.parse_args(argv)
    client = ProbeClient(token_path=args.token_file)
    try:
        if args.command == "pair":
            open_companion()
            print("Tap Pair in Anyplay WebView, then return here to enter its code (input is hidden).", file=sys.stderr)
            result = client.pair(getpass.getpass("Pairing code shown in companion: ").strip())
        elif args.command == "health":
            result = client.health()
        elif args.command == "start":
            result = client.start()
        elif args.command == "shutdown":
            result = client.shutdown()
        else:
            session_id = client.create_session()["session_id"]
            try:
                values = [client.run(session_id, script) for script in ("2+2", "Promise.resolve(2+2)")]
                result = {
                    "passed": all(v.get("status") == "ok" and v.get("result") == 4 for v in values),
                    "results": values,
                }
            finally:
                client.close_session(session_id)
            if not result["passed"]:
                print(json.dumps(result))
                return 1
        print(json.dumps(result))
        return 0
    except ProbeError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
