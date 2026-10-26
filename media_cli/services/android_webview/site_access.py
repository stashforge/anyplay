"""HTTP-first fixed website access; native profile cookies never leave Android."""

from __future__ import annotations

import time
from contextvars import ContextVar
from urllib.parse import urlsplit

from ..browser_access.access import AccessResponse
from ..browser_access.challenge import is_verification_challenge
from ..browser_access.profiles import BrowserAccessCancelled
from .client import CompanionSetupRequired, ProbeClient, ProbeError, site_profile, site_result
from .requirements import companion_dependency_error

_native_agent = ContextVar("android_site_user_agent", default=None)


def site_user_agent(url: str, fallback: str) -> str:
    observed = _native_agent.get()
    return observed[1] if observed is not None and observed[0] == url else fallback


def fetch_site_response(
    url, normal_get, *, deadline, cancel=None, incognito=False, enabled=True, on_status=None, client=None
):
    profile = site_profile(url)
    _native_agent.set(None)

    def check():
        if cancel is not None and cancel.is_set():
            raise BrowserAccessCancelled("Browser access cancelled")
        if time.monotonic() >= deadline:
            raise ProbeError("Site access timed out")

    check()
    try:
        result = normal_get(url)
    except Exception:
        check()
        raise
    check()
    # Even ordinary HTTP must not silently widen the requested website origin.
    site_profile(result.url)
    if urlsplit(result.url).hostname != urlsplit(url).hostname:
        raise ProbeError("Site redirect is not permitted")
    if not is_verification_challenge(result.status, result.headers, result.body):
        return result
    if not enabled:
        raise ProbeError("Android browser access is disabled")
    if incognito:
        raise ProbeError("Protected Android incognito access is not supported")
    client = client if client is not None else ProbeClient()

    def status(message):
        if on_status is not None:
            on_status(message)

    def reveal(job_id, ticket, host):
        check()
        status("Complete verification in the Android window.")
        client.reveal_verification(job_id, ticket, host, deadline=deadline)

    status("Checking site access...")
    try:
        with client.session(profile, deadline=deadline, cancel=cancel) as sid:
            job = client.run_site_get(sid, url, deadline, cancel=cancel, on_interaction=reveal)
            check()
            if isinstance(job, dict) and job.get("status") == "cancelled":
                raise BrowserAccessCancelled("Browser access cancelled")
            value = site_result(job, url)
            result = AccessResponse(value["url"], value["status"], value["body"], value["headers"])
            if is_verification_challenge(result.status, result.headers, result.body):
                raise ProbeError("Site verification did not complete")
        check()
    except CompanionSetupRequired as error:
        check()
        raise companion_dependency_error(error) from None
    except ProbeError:
        check()
        raise
    if 200 <= result.status < 300:
        _native_agent.set((url, value["user_agent"]))
        status("Site access ready. Loading...")
    return result
