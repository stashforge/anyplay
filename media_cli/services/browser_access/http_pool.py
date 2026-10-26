"""Serialized, origin-bound clearance connections with one challenge retry."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Event, Lock
from typing import TYPE_CHECKING

from .challenge import is_verification_challenge
from .clearance import Clearance, ClearanceRequestError, request_with_clearance
from .diagnostics import record
from .policy import BrowserPolicy
from .process import _RequestCancellation
from .profiles import BrowserAccessCancelled

if TYPE_CHECKING:
    from curl_cffi.requests import Session

    from .access import AccessResponse


@dataclass(repr=False)
class _Client:
    lock: Lock = field(default_factory=Lock)
    session: Session | None = None
    identity: tuple | None = None

    def close(self) -> None:
        session, self.session = self.session, None
        if session is not None:
            session.close()


class ClearanceHttpPool:
    """One instance per state-root/private-mode scope; never writes to disk."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._closed = Event()
        self._clients: dict[tuple[str, str], _Client] = {}

    def get(self, url: str, clearance: Clearance, *, headers: dict[str, str] | None = None,
            timeout: float = 15, cancel: Event | None = None,
            request: Callable[..., AccessResponse] = request_with_clearance) -> AccessResponse:
        policy = BrowserPolicy(clearance.provider_id, [clearance.origin])
        if (not clearance.valid_for(policy, url) or isinstance(timeout, bool)
                or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0):
            raise ClearanceRequestError("Clearance HTTP request failed")
        deadline = time.monotonic() + timeout
        cancellation = _RequestCancellation(self._closed, cancel)

        def remaining() -> float:
            if cancellation.is_set():
                raise BrowserAccessCancelled("Browser access cancelled")
            budget = deadline - time.monotonic()
            # curl converts seconds to integer milliseconds; zero means unbounded.
            if budget < .001:
                raise ClearanceRequestError("Clearance HTTP request timed out")
            return budget

        with self._lock:
            remaining()
            client = self._clients.setdefault((clearance.provider_id, clearance.origin), _Client())
        while not client.lock.acquire(timeout=min(.05, remaining())):
            pass
        try:
            remaining()
            identity = clearance.user_agent, clearance._partition_key
            if client.identity != identity:
                client.close()
                client.identity = identity
            for attempt in range(2):
                remaining()
                if client.session is None:
                    from curl_cffi.requests import Session
                    # The same easy handle must survive worker-thread changes.
                    client.session = Session(impersonate="chrome", trust_env=False,
                                             use_thread_local_curl=False)
                    record(policy, url, "http_client_created")
                else:
                    record(policy, url, "http_client_reused")
                result = request(url, clearance, headers=headers, timeout=remaining(),
                                 session=client.session, cancel=cancellation)
                remaining()
                challenged = is_verification_challenge(result.status, result.headers, result.body)
                if not challenged:
                    return result
                client.close()
                if attempt == 0:
                    record(policy, url, "fresh_connection_retry", reason="verification_challenge",
                           status=result.status)
                else:
                    return result
            raise AssertionError("Unreachable clearance retry")
        except BaseException:
            client.close()
            raise
        finally:
            # Publish an idle handle atomically with respect to close() marking
            # the pool closed, so neither owner can miss the final cleanup.
            with self._lock:
                try:
                    if self._closed.is_set():
                        client.close()
                finally:
                    client.lock.release()

    def close(self) -> None:
        with self._lock:
            self._closed.set()
            clients = list(self._clients.values())
            self._clients.clear()
        failure: Exception | None = None
        for client in clients:
            # Active curl handles must only be closed by their request worker.
            # Shutdown must not wait for a server that has not sent a body yet.
            if not client.lock.acquire(blocking=False):
                continue
            try:
                client.close()
            except Exception as error:
                failure = error
            finally:
                client.lock.release()
        if failure is not None:
            raise failure
