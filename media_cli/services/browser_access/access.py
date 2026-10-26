"""Normal requests first; scoped quiet-first browser recovery on challenges only."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from .bridge import BrowserBridge, BrowserRequestTimeout
from .challenge import is_verification_challenge
from .diagnostics import record
from .extension import packaged_extension
from .policy import BrowserPolicy, BrowserPolicyError
from .process import BrowserProcessError, BrowserWindowNotReady, OwnedChromium, _RequestCancellation
from .profiles import BrowserAccessCancelled, BrowserProfile, BrowserProfiles
from .verification import VerificationGate, VerificationState

if TYPE_CHECKING:
    from .clearance import Clearance


@dataclass(frozen=True)
class AccessResponse:
    url: str = field(repr=False)
    status: int
    body: str = field(repr=False)
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    clearance: Clearance | None = field(default=None, repr=False)
    data: bytes | None = field(default=None, repr=False)


def _pause(seconds: float, cancel: Event) -> None:
    deadline = time.monotonic() + seconds
    while not cancel.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.05, remaining))


@dataclass(repr=False)
class _Session:
    lock: Lock = field(default_factory=Lock)
    profile_stack: ExitStack = field(default_factory=ExitStack)
    browser_stack: ExitStack = field(default_factory=ExitStack)
    profile: BrowserProfile | None = None
    policy: BrowserPolicy | None = None
    bootstrap: str = ""
    bridge: BrowserBridge | None = None
    owner: OwnedChromium | None = None

    def close(self) -> None:
        try:
            self.browser_stack.close()
        finally:
            self.profile_stack.close()
            self.profile = None
            self.policy = None
            self.bridge = None
            self.owner = None


class BrowserAccess:
    """Lazy shared service; adapters retain their existing normal HTTP behavior.

    The caller supplies a trusted profile root whose parent exists. A normal_get
    adapter must return HTTP errors as responses; network exceptions propagate.
    Clearance reuse is opt-in; browser-native requests remain the fallback.
    """

    def __init__(self, root: Path, *, quiet_seconds: float = 15, total_seconds: float = 90,
                 keep_sessions: bool = False, reuse_clearance: bool = False):
        VerificationGate(0, quiet_seconds=quiet_seconds, total_seconds=total_seconds)
        self.profiles = BrowserProfiles(root)
        self.quiet_seconds, self.total_seconds = quiet_seconds, total_seconds
        self.keep_sessions = keep_sessions
        self.reuse_clearance = reuse_clearance
        self._clearances: dict[tuple[str, bool, str], Clearance] = {}
        self._browser_only: set[tuple[str, bool, str]] = set()
        self._sessions: dict[tuple[str, bool], _Session] = {}
        self._lock = Lock()
        self._closed = Event()

    def close(self) -> None:
        """Cancel active requests before closing browsers and releasing profiles."""
        self._closed.set()
        with self._lock:
            sessions = list(self._sessions.values())
        error: Exception | None = None
        for session in sessions:
            try:
                with session.lock:
                    session.close()
            except Exception as failure:
                error = failure
        self._clearances.clear()
        self._browser_only.clear()
        if error is not None:
            raise error

    def get(self, policy: BrowserPolicy, url: str, normal_get: Callable[[str], AccessResponse], *,
            bootstrap_url: str | None = None, incognito: bool = False, cancel: Event | None = None,
            on_state: Callable[[VerificationState], None] | None = None,
            clearance_get: Callable[[str, Clearance], AccessResponse] | None = None,
            clearance_rejected: bool = False) -> AccessResponse:
        policy.validate_url(url)
        if self.keep_sessions:
            return self._retained_get(policy, url, normal_get, bootstrap_url, incognito, cancel, on_state,
                                      clearance_get, clearance_rejected)
        if cancel is not None and cancel.is_set():
            raise BrowserAccessCancelled("Browser access cancelled")
        response = normal_get(url)
        if cancel is not None and cancel.is_set():
            raise BrowserAccessCancelled("Browser access cancelled")
        if not is_verification_challenge(response.status, response.headers, response.body):
            return response
        bootstrap = bootstrap_url or url
        policy.validate_url(bootstrap)
        if self._origin(bootstrap) != self._origin(url):
            raise BrowserPolicyError("Browser bootstrap must match the requested origin")
        deadline = time.monotonic() + self.total_seconds
        with self.profiles.lease(policy.provider_id, incognito=incognito, timeout=self.total_seconds, cancel=cancel) as profile:
            self._check_start(deadline, cancel)
            with BrowserBridge() as bridge:
                with packaged_extension(profile, policy, bridge.extension_config(), bootstrap) as extension:
                    self._check_start(deadline, cancel)
                    with OwnedChromium(profile, policy, extension, bootstrap, bridge) as owner:
                        return self._recover(policy, url, bootstrap, bridge, owner, deadline, cancel, on_state)

    def _retained_get(self, policy: BrowserPolicy, url: str, normal_get: Callable[[str], AccessResponse],
                      bootstrap_url: str | None, incognito: bool, cancel: Event | None,
                      on_state: Callable[[VerificationState], None] | None,
                      clearance_get: Callable[[str, Clearance], AccessResponse] | None,
                      clearance_rejected: bool) -> AccessResponse:
        deadline = time.monotonic() + self.total_seconds
        cancellation = _RequestCancellation(self._closed, cancel)
        self._check_start(deadline, cancellation)
        bootstrap = bootstrap_url or url
        policy.validate_url(bootstrap)
        if self._origin(bootstrap) != self._origin(url):
            raise BrowserPolicyError("Browser bootstrap must match the requested origin")
        with self._lock:
            session = self._sessions.setdefault((policy.provider_id, incognito), _Session())
        while not session.lock.acquire(timeout=0.05):
            self._check_start(deadline, cancellation)
        try:
            self._check_start(deadline, cancellation)
            if clearance_rejected:
                self._browser_only.add((policy.provider_id, incognito, self._origin_text(url)))
            replay_response = None
            if session.owner is not None and session.owner.closed.is_set():
                session.close()
            if self.reuse_clearance and clearance_get is not None and session.owner is None:
                replay_response = self._saved_clearance_get(policy, url, incognito, clearance_get,
                                                            deadline, cancellation)
                if replay_response is not None and not is_verification_challenge(
                        replay_response.status, replay_response.headers, replay_response.body):
                    return replay_response
            if (session.owner is not None and session.policy == policy
                    and self._origin(session.bootstrap) == self._origin(url)):
                record(policy, url, "browser_reused", reason="native_session", private=incognito)
                assert session.bridge is not None
                combined = _RequestCancellation(session.owner.closed, cancellation)
                reply = session.bridge.request(policy.command("fetch", url),
                                               timeout=min(12, deadline - time.monotonic()), cancel=combined)
                self._check_start(deadline, combined)
                if reply == {"error": "browser_closed"}:
                    raise BrowserAccessCancelled("Browser verification cancelled")
                if reply != {"error": "browser_operation_failed"}:
                    result = policy.response(reply)
                    if self._origin(result["url"]) != self._origin(url):
                        raise BrowserPolicyError("Browser reply does not match the requested origin")
                    if not is_verification_challenge(result["status"], {}, result["body"]):
                        return AccessResponse(result["url"], result["status"], result["body"])
                # Reload the actual target when clearance expires: a background
                # fetch cannot display an interactive challenge on the old page.
            else:
                response = replay_response if replay_response is not None else normal_get(url)
                self._check_start(deadline, cancellation)
                if not is_verification_challenge(response.status, response.headers, response.body):
                    return response
            # A different approved origin gets a new bound window but retains
            # this provider's native cookie jar, including in incognito mode.
            session.browser_stack.close()
            session.owner = session.bridge = None
            if session.profile is None:
                session.profile = session.profile_stack.enter_context(self.profiles.lease(
                    policy.provider_id, incognito=incognito, timeout=max(0, deadline - time.monotonic()),
                    cancel=cancellation))
            self._check_start(deadline, cancellation)
            session.bridge = session.browser_stack.enter_context(BrowserBridge())
            extension = session.browser_stack.enter_context(packaged_extension(
                session.profile, policy, session.bridge.extension_config(), bootstrap))
            record(policy, url, "browser_start", reason="verification_challenge", private=incognito)
            session.owner = session.browser_stack.enter_context(OwnedChromium(
                session.profile, policy, extension, bootstrap, session.bridge))
            session.policy, session.bootstrap = policy, bootstrap
            recovered = self._recover(policy, url, bootstrap, session.bridge, session.owner,
                                      deadline, cancellation, on_state)
            if 200 <= recovered.status < 400:
                if self.reuse_clearance and clearance_get is not None:
                    handed_off = self._handoff(session, policy, url, incognito, clearance_get,
                                               deadline, cancellation)
                    if handed_off is not None:
                        return handed_off
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    session.owner.set_window_state("minimized", timeout=min(5, remaining), cancel=cancellation)
            return recovered
        except BaseException:
            session.close()
            raise
        finally:
            session.lock.release()

    def cached_clearance(self, policy: BrowserPolicy, url: str, *, incognito: bool = False) -> Clearance | None:
        """Read an immutable memory snapshot without starting or waiting on a browser."""
        policy.validate_url(url)
        item = self._clearances.get((policy.provider_id, incognito, self._origin_text(url)))
        return item if not self._closed.is_set() and item is not None and item.valid_for(policy, url) else None

    def _saved_clearance_get(self, policy: BrowserPolicy, url: str, incognito: bool,
                             get: Callable[[str, Clearance], AccessResponse], deadline: float,
                             cancel: Event) -> AccessResponse | None:
        from .clearance import ClearanceStore
        key = policy.provider_id, incognito, self._origin_text(url)
        store = ClearanceStore(self.profiles.root)
        clearance = self._clearances.get(key)
        if clearance is None and not incognito:
            clearance = store.load(policy, url)
        if clearance is None:
            return None
        response = None
        if clearance.valid_for(policy, url):
            try:
                response = get(url, clearance)
                record(policy, url, "replay_http", status=response.status, private=incognito)
            except BrowserAccessCancelled:
                raise
            except (OSError, ImportError, RuntimeError):
                record(policy, url, "replay_error", reason="transport_error", private=incognito)
                # A broken HTTP path must not replace working browser recovery.
                pass
            self._check_start(deadline, cancel)
        if response is not None and not is_verification_challenge(response.status, response.headers, response.body):
            if (response.clearance is not None and response.clearance != clearance
                    and response.clearance.valid_for(policy, url)):
                clearance = response.clearance
                if not incognito:
                    store.save(policy, url, clearance, cancel=cancel)
                self._check_start(deadline, cancel)
            self._clearances[key] = clearance
            return response
        self._clearances.pop(key, None)
        if response is not None and is_verification_challenge(response.status, response.headers, response.body):
            self._browser_only.add(key)
        if not incognito:
            store.invalidate(policy, url)
        return response

    def _handoff(self, session: _Session, policy: BrowserPolicy, url: str, incognito: bool,
                 get: Callable[[str, Clearance], AccessResponse], deadline: float,
                 cancel: Event) -> AccessResponse | None:
        from .clearance import Clearance, ClearanceStore
        assert session.bridge is not None
        def check_cancelled() -> None:
            if cancel.is_set():
                raise BrowserAccessCancelled("Browser access cancelled")
        check_cancelled()
        if deadline <= time.monotonic():
            record(policy, url, "browser_retained", reason="deadline", private=incognito)
            return None
        failure_reason = "export_failed"
        try:
            exported = session.bridge.request({"operation": "clearance", "url": url},
                                               timeout=min(5, max(0.001, deadline - time.monotonic())), cancel=cancel)
            exported_cookies = exported.get("cookies")
            count = len(exported_cookies) if isinstance(exported_cookies, list) else None
            record(policy, url, "cookie_export", cookies=count,
                   partitioned=isinstance(exported.get("partition_key"), dict), private=incognito)
            check_cancelled()
            if deadline <= time.monotonic():
                record(policy, url, "browser_retained", reason="deadline", private=incognito)
                return None
            clearance = Clearance.from_export(policy, url, exported)
            if clearance is None:
                reason = "export_failed" if "error" in exported else "no_clearance" if count == 0 else "invalid_snapshot"
                record(policy, url, "browser_retained", reason=reason,
                       private=incognito)
                return None
            failure_reason = "transport_error"
            response = get(url, clearance)
            record(policy, url, "replay_http", status=response.status, private=incognito)
        except BrowserAccessCancelled:
            raise
        except (OSError, ImportError, RuntimeError, ValueError):
            record(policy, url, "browser_retained", reason=failure_reason, private=incognito)
            check_cancelled()
            return None
        check_cancelled()
        if deadline <= time.monotonic():
            record(policy, url, "browser_retained", reason="deadline", private=incognito)
            return None
        if not 200 <= response.status < 300 or is_verification_challenge(
                response.status, response.headers, response.body):
            record(policy, url, "browser_retained", reason="http_error", status=response.status, private=incognito)
            return None
        if response.clearance is not None and response.clearance.valid_for(policy, url):
            clearance = response.clearance
        key = policy.provider_id, incognito, self._origin_text(url)
        if key not in self._browser_only:
            session.close()
            record(policy, url, "browser_closed", reason="http_replay_succeeded", private=incognito)
        else:
            record(policy, url, "browser_retained", reason="cached_clearance_rejected", private=incognito)
        check_cancelled()
        if not incognito:
            ClearanceStore(self.profiles.root).save(policy, url, clearance, cancel=cancel)
        else:
            record(policy, url, "cache_save_skipped", reason="private_memory_only", private=True)
        check_cancelled()
        self._clearances[key] = clearance
        return response if key not in self._browser_only else None

    @staticmethod
    def _origin_text(url: str) -> str:
        return "https://" + str(urlsplit(url).hostname)

    @staticmethod
    def _origin(url: str) -> tuple[str | None, int]:
        parts = urlsplit(url)
        return parts.hostname, parts.port or 443

    @staticmethod
    def _check_start(deadline: float, cancel: Event | None) -> None:
        if cancel is not None and cancel.is_set():
            raise BrowserAccessCancelled("Browser access cancelled")
        if time.monotonic() >= deadline:
            raise BrowserRequestTimeout("Browser verification timed out")

    def _recover(self, policy: BrowserPolicy, url: str, bootstrap: str, bridge: BrowserBridge, owner: OwnedChromium,
                 deadline: float, cancel: Event | None,
                 on_state: Callable[[VerificationState], None] | None) -> AccessResponse:
        now = time.monotonic()
        self._check_start(deadline, cancel)
        quiet = min(self.quiet_seconds, deadline - now)
        gate = VerificationGate(now, quiet_seconds=quiet, total_seconds=deadline - now)
        reveal_at = now + quiet
        cancellation = _RequestCancellation(owner.closed, cancel)
        last_state: VerificationState | None = None
        revealed = False

        def observe(*, verified: bool = False, closed: bool = False) -> VerificationState:
            nonlocal last_state
            state = gate.observe(time.monotonic(), verified=verified,
                                 closed=closed or owner.closed.is_set(), cancelled=cancellation.is_set())
            if state != last_state:
                last_state = state
                if on_state is not None:
                    on_state(state)
            if state == VerificationState.CANCELLED:
                raise BrowserAccessCancelled("Browser verification cancelled")
            if state == VerificationState.TIMED_OUT:
                raise BrowserRequestTimeout("Browser verification timed out")
            return state

        def request(command: dict) -> dict:
            nonlocal revealed
            state = observe()
            remaining = deadline - time.monotonic()
            if state == VerificationState.INTERACTION and not revealed:
                owner.set_window_state("normal", timeout=min(5, remaining), cancel=cancellation)
                revealed = True
                observe()
                remaining = deadline - time.monotonic()
            timeout = min(12.0, remaining)
            if state == VerificationState.QUIET:
                timeout = min(timeout, max(0.001, reveal_at - time.monotonic()))
            return bridge.request(command, timeout=timeout, cancel=cancellation)

        def decoded(reply: dict, target: str) -> dict | None:
            if cancellation.is_set() or time.monotonic() >= deadline:
                observe()
            if reply == {"error": "browser_closed"}:
                observe(closed=True)
            if reply == {"error": "browser_operation_failed"}:
                observe()
                return None
            if "error" in reply:
                raise BrowserProcessError("Unsupported browser helper response")
            result = policy.response(reply)
            if self._origin(result["url"]) != self._origin(target):
                raise BrowserPolicyError("Browser reply does not match the requested origin")
            return result

        document_command = policy.command("document", bootstrap)
        fetch_command = policy.command("fetch", url)
        while True:
            try:
                document = decoded(request(document_command), bootstrap)
                # Observe the rendered challenge without repeatedly hitting the
                # provider API while browser verification is still in progress.
                if document is not None and not is_verification_challenge(200, {}, document["body"]):
                    result = decoded(request(fetch_command), url)
                    if result is not None and not is_verification_challenge(result["status"], {}, result["body"]):
                        if 200 <= result["status"] < 400:
                            observe(verified=True)
                        return AccessResponse(result["url"], result["status"], result["body"])
                observe()
            except BrowserAccessCancelled:
                observe(closed=True)
                raise
            except (BrowserRequestTimeout, BrowserWindowNotReady):
                observe()
            _pause(min(0.25, max(0, deadline - time.monotonic())), cancellation)
