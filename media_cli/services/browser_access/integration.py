"""Shared text transport for opted-in Linux providers; normal HTTP stays first."""

from __future__ import annotations

import atexit
import io
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from email.message import Message
from functools import partial
from pathlib import Path
from threading import Lock
from urllib.parse import urlsplit

from ...core.processes import is_termux
from .access import AccessResponse, BrowserAccess
from .challenge import is_verification_challenge
from .clearance import Clearance, ClearanceStore, request_with_clearance
from .diagnostics import record
from .http_pool import ClearanceHttpPool
from .operation import current_operation, shutdown_event
from .policy import BrowserPolicy
from .process import _RequestCancellation
from .profiles import BrowserAccessCancelled
from .verification import VerificationState

RECOVERY_PROVIDERS = frozenset({"animepahe", "animexin"})
_services: dict[tuple[Path, bool], tuple[BrowserAccess, tempfile.TemporaryDirectory | None]] = {}
_services_lock = Lock()
_http_pools: dict[tuple[Path, bool], ClearanceHttpPool] = {}
_shutdown = shutdown_event
# Explicit image capabilities, not wildcard subdomain credential sharing.
_IMAGE_ROUTES = {"https://i.animepahe.pw": ("animepahe", "https://animepahe.pw", "/uploads/posters/")}


def close_browser_sessions(*, shutdown: bool = False) -> None:
    """CLI cleanup calls this explicitly because its final exit bypasses atexit."""
    with _services_lock:
        if shutdown:
            _shutdown.set()
        sessions = list(_services.values())
        _services.clear()
        pools = list(_http_pools.values())
        _http_pools.clear()
    error: Exception | None = None
    for pool in pools:
        try:
            pool.close()
        except Exception as failure:
            error = failure
    for service, temporary in sessions:
        try:
            service.close()
        except Exception as failure:
            error = failure
        else:
            if temporary is not None:
                temporary.cleanup()
    if error is not None:
        raise error


atexit.register(close_browser_sessions, shutdown=True)


def _service(root: Path, private: bool, *, create: bool = False) -> BrowserAccess | None:
    with _services_lock:
        if _shutdown.is_set():
            raise BrowserAccessCancelled("Browser access is shutting down")
        key = root, private
        if key not in _services and create:
            temporary = tempfile.TemporaryDirectory(prefix="anyplay-private-browser-") if private else None
            actual_root = Path(temporary.name) / "browser" if temporary is not None else root
            if not private:
                root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _services[key] = BrowserAccess(actual_root, keep_sessions=True, reuse_clearance=True), temporary
        entry = _services.get(key)
        return entry[0] if entry is not None else None


def _http_pool(root: Path, private: bool) -> ClearanceHttpPool:
    with _services_lock:
        if _shutdown.is_set():
            raise BrowserAccessCancelled("Browser access is shutting down")
        key = root, private
        pool = _http_pools.get(key)
        if pool is None:
            pool = ClearanceHttpPool()
            _http_pools[key] = pool
        return pool


def fetch_cached_image(url: str, *, max_bytes: int = 8 * 1024 * 1024) -> bytes | None:
    """Retry an approved cover using existing clearance only; never verify for art."""
    from ...context import FLAGS
    from ...media.urls import is_supported_image

    if (not linux_desktop() or _shutdown.is_set() or type(max_bytes) is not int or not 1 <= max_bytes <= 8388608
            or any(os.environ.get(key, "").strip().casefold() in {"0", "false", "off", "no"}
                   for key in ("ANYPLAY_BROWSER_ACCESS", "ANYPLAY_BROWSER_COOKIES"))):
        return None
    operation = current_operation()
    if operation.cancel is not None and operation.cancel.is_set():
        return None
    try:
        from .policy import _origin
        origin = _origin(url)
        route = _IMAGE_ROUTES.get(origin)
        path = urlsplit(url).path
        if (route is None or not path.startswith(route[2])
                or re.fullmatch(r"[A-Za-z0-9_./-]+", path) is None
                or any(segment in {".", ".."} for segment in path.split("/"))):
            return None
        provider, source, _ = route
        policy = BrowserPolicy(provider, [source])
        source_url = source + "/"
        root = _profile_root()
        private = FLAGS.incognito_mode if operation.incognito is None else operation.incognito
        service = _service(root, private)
        snapshot = service.cached_clearance(policy, source_url, incognito=private) if service is not None else None
        if snapshot is None and not private:
            snapshot = ClearanceStore(root).load(policy, source_url)
        if snapshot is None:
            return None
        # Validation rejects host-only cookies and incompatible domains, paths or
        # partitions. A successful main-site check does not approve arbitrary hosts.
        scoped = Clearance.from_export(BrowserPolicy(provider, [origin]), url, {
            "cookies": list(snapshot.cookies), "user_agent": snapshot.user_agent,
            "partition_key": snapshot.partition_key,
        })
        if scoped is None:
            return None
        result = _http_pool(root, private).get(
            url, scoped, headers={"Referer": source_url}, timeout=10,
            cancel=_RequestCancellation(_shutdown, operation.cancel),
            request=partial(request_with_clearance, binary=True, max_bytes=max_bytes),
        )
        if (200 <= result.status < 300 and result.data is not None
                and len(result.data) <= max_bytes and is_supported_image(result.data)):
            return result.data
    except (OSError, ImportError, RuntimeError, ValueError):
        pass
    return None


def browser_task_options(provider_id: str) -> tuple[float, str]:
    """Keep browser jobs bounded, without sharing a caller's cancellation token."""
    if not ((provider_id in {"animepahe", "kwik"} and is_termux())
            or (provider_id in RECOVERY_PROVIDERS and linux_desktop())):
        return 30, ""
    from ...context import FLAGS
    operation = current_operation()
    private = FLAGS.incognito_mode if operation.incognito is None else operation.incognito
    cancel = operation.cancel
    identity = ":private" if private else ":saved"
    return 120, identity + (f":operation-{id(cancel)}" if cancel is not None else "")


def linux_desktop() -> bool:
    return (sys.platform == "linux" and not os.environ.get("TERMUX_VERSION")
            and not os.environ.get("ANDROID_ROOT")
            and not os.environ.get("PREFIX", "").startswith("/data/data/com.termux"))


def _profile_root() -> Path:
    from ...constants import APP_NAME
    return Path(os.environ.get("XDG_STATE_HOME") or "~/.local/state").expanduser() / str(APP_NAME) / "browser-access"


def normal_response(url: str, *, headers: dict[str, str] | None = None, timeout: float = 15,
                    allow_redirects: bool = True) -> AccessResponse:
    from ..http import UA
    request_headers = {"User-Agent": UA, **(headers or {})}
    try:
        from curl_cffi import requests
    except ImportError:
        requests = None
    if requests is not None:
        try:
            options = {} if allow_redirects else {"allow_redirects": False}
            curl_response = requests.get(url, headers=request_headers, timeout=timeout, impersonate="chrome", **options)
        except Exception:
            pass
        else:
            return AccessResponse(str(curl_response.url), curl_response.status_code, curl_response.text,
                                  dict(curl_response.headers))
    request = urllib.request.Request(url, headers=request_headers, method="GET")
    try:
        if allow_redirects:
            response = urllib.request.urlopen(request, timeout=timeout)
        else:
            from ..android_webview.client import NoRedirect
            response = urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        charset = response.headers.get_content_charset() or "utf-8"
        return AccessResponse(response.geturl(), response.status, response.read().decode(charset, errors="replace"),
                              dict(response.headers))


def fetch_text(provider_id: str, origins: list[str], url: str, *, headers: dict[str, str] | None = None,
               timeout: float = 15) -> str:
    policy = BrowserPolicy(provider_id, origins)
    policy.validate_url(url)
    if _shutdown.is_set():
        raise BrowserAccessCancelled("Browser access is shutting down")
    operation = current_operation()
    if operation.cancel is not None and operation.cancel.is_set():
        raise BrowserAccessCancelled("Browser access cancelled")
    disabled = any(os.environ.get(key, "").strip().casefold() in {"0", "false", "off", "no"}
                   for key in ("ANYPLAY_BROWSER_ACCESS", "ANYPLAY_BROWSER_COOKIES"))
    enabled = linux_desktop() and not disabled
    def normal(target: str) -> AccessResponse:
        result = normal_response(target, headers=headers, timeout=timeout)
        record(policy, target, "normal_http", status=result.status)
        if operation.cancel is not None and operation.cancel.is_set():
            raise BrowserAccessCancelled("Browser access cancelled")
        return result
    def replay(target: str, clearance: Clearance) -> AccessResponse:
        pool = _http_pool(root, incognito)
        result = pool.get(target, clearance, headers=headers, timeout=timeout,
                          cancel=_RequestCancellation(_shutdown, operation.cancel), request=request_with_clearance)
        if _shutdown.is_set() or (operation.cancel is not None and operation.cancel.is_set()):
            raise BrowserAccessCancelled("Browser access cancelled")
        return result
    if is_termux() and provider_id in {"animepahe", "kwik"}:
        from ...context import FLAGS
        from ..android_webview.site_access import fetch_site_response
        private = FLAGS.incognito_mode if operation.incognito is None else operation.incognito
        deadline = time.monotonic() + 120
        result = fetch_site_response(url, lambda target: normal_response(
            target, headers=headers, timeout=min(timeout, max(0.001, deadline - time.monotonic())),
            allow_redirects=False),
            deadline=deadline, cancel=_RequestCancellation(_shutdown, operation.cancel),
            incognito=private, enabled=not disabled, on_status=operation.status)
    elif enabled:
        from ...context import FLAGS
        incognito = FLAGS.incognito_mode if operation.incognito is None else operation.incognito
        messages = {
            VerificationState.QUIET: "Checking site access...",
            VerificationState.INTERACTION: "Check verification in the popup.",
            VerificationState.VERIFIED: "Verification complete. Loading...",
            VerificationState.CANCELLED: "Verification cancelled.",
            VerificationState.TIMED_OUT: "Verification timed out.",
        }
        def status(state: VerificationState) -> None:
            if operation.status is not None:
                operation.status(messages[state])
        root = _profile_root()
        service = _service(root, incognito)
        if service is None:
            saved = None if incognito else ClearanceStore(root).load(policy, url)
            result = None
            if saved is not None:
                try:
                    result = replay(url, saved)
                    record(policy, url, "replay_http", status=result.status, private=False)
                except BrowserAccessCancelled:
                    raise
                except (OSError, ImportError, RuntimeError):
                    record(policy, url, "replay_error", reason="transport_error", private=False)
                    pass
                if result is None or is_verification_challenge(result.status, result.headers, result.body):
                    ClearanceStore(root).invalidate(policy, url)
                elif (result.clearance is not None and result.clearance != saved
                      and result.clearance.valid_for(policy, url)):
                    cancellation = _RequestCancellation(_shutdown, operation.cancel)
                    ClearanceStore(root).save(policy, url, result.clearance, cancel=cancellation)
                    if cancellation.is_set():
                        raise BrowserAccessCancelled("Browser access cancelled")
            if result is None:
                result = normal(url)
            if is_verification_challenge(result.status, result.headers, result.body):
                service = _service(root, incognito, create=True)
                assert service is not None
                original = result
                result = service.get(policy, url, lambda _: original, incognito=incognito,
                                     cancel=operation.cancel, on_state=status, clearance_get=replay,
                                     clearance_rejected=saved is not None)
        else:
            result = service.get(policy, url, normal, incognito=incognito, cancel=operation.cancel,
                                 on_state=status, clearance_get=replay)
    else:
        result = normal(url)
    if not 200 <= result.status < 300:
        response_headers = Message()
        for key, value in result.headers.items():
            response_headers[key] = value
        raise urllib.error.HTTPError(result.url, result.status, f"Site returned HTTP {result.status}",
                                     response_headers, io.BytesIO(result.body.encode("utf-8")))
    return result.body
