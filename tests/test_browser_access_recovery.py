"""Shared challenge-only recovery flow and deterministic lifecycle coverage."""

import contextlib
import threading
from types import SimpleNamespace

import pytest

from media_cli.services.browser_access import access
from media_cli.services.browser_access.access import AccessResponse, BrowserAccess
from media_cli.services.browser_access.bridge import BrowserRequestTimeout
from media_cli.services.browser_access.challenge import is_verification_challenge
from media_cli.services.browser_access.policy import BrowserPolicy, BrowserPolicyError
from media_cli.services.browser_access.process import BrowserProcessError, BrowserWindowNotReady
from media_cli.services.browser_access.profiles import BrowserAccessCancelled
from media_cli.services.browser_access.verification import VerificationState

URL = "https://site.test/api"
CHALLENGE = '<html><title>Just a moment...</title><script src="/cdn-cgi/challenge-platform/a"></script></html>'


@pytest.mark.parametrize("status,headers,body,expected", [
    (403, {}, CHALLENGE, True), (200, {}, CHALLENGE, True), (503, {}, CHALLENGE, True),
    (403, {"CF-Mitigated": "challenge"}, "", True),
    (200, {"cf-mitigated": "challenge"}, "", True),
    (403, {}, "Forbidden", False), (429, {}, "Too many requests", False),
    (500, {}, "Internal server error", False), (503, {}, "Maintenance", False),
    (200, {}, '<a href="/cdn-cgi/challenge-platform/">Cloudflare help</a>', False),
    (200, {}, '{"title":"Just a moment","info":"cf-chl-"}', False),
    (404, {}, CHALLENGE, False),
])
def test_conservative_challenge_detection(status, headers, body, expected):
    assert is_verification_challenge(status, headers, body) is expected


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    clock = SimpleNamespace(now=0.0)
    events, script, windows, calls, documents, window_outcomes, exports = [], [], [], [], [], [], []
    owner = SimpleNamespace(closed=threading.Event())
    def advance(seconds):
        clock.now += seconds
    monkeypatch.setattr(access, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(access, "_pause", lambda delay, cancel: advance(delay))

    class Bridge:
        def __enter__(self):
            events.append("bridge_open")
            return self
        def __exit__(self, *args):
            events.append("bridge_close")
        def extension_config(self):
            return {"endpoint": "unused", "token": "unused"}
        def request(self, command, **kwargs):
            calls.append((clock.now, command, kwargs))
            if command["operation"] == "clearance":
                if exports:
                    value = exports.pop(0)
                    return value() if callable(value) else value
                return {"error": "browser_operation_failed"}
            if command["operation"] == "document":
                if documents:
                    advance(min(1, kwargs["timeout"]))
                    return documents.pop(0)
                return {"url": command["url"], "status": 200, "body": "<html><title>Site</title></html>"}
            advance(min(1, kwargs["timeout"]))
            if script:
                value = script.pop(0)
                if callable(value):
                    return value()
                if isinstance(value, Exception):
                    raise value
                return value
            return {"url": URL, "status": 403, "body": CHALLENGE}

    @contextlib.contextmanager
    def package(profile, policy, config, bootstrap):
        events.append(("helper_open", profile.provider_id, profile.persistent, bootstrap))
        try:
            yield profile.path / "helper"
        finally:
            events.append("helper_close")

    class Owner:
        def __init__(self, *args):
            self.closed = owner.closed
        def __enter__(self):
            events.append("owner_open")
            return self
        def __exit__(self, *args):
            events.append("owner_close")
        def set_window_state(self, state, **kwargs):
            windows.append((clock.now, state))
            if window_outcomes:
                outcome = window_outcomes.pop(0)
                if callable(outcome):
                    outcome()
                else:
                    raise outcome

    monkeypatch.setattr(access, "BrowserBridge", Bridge)
    monkeypatch.setattr(access, "packaged_extension", package)
    monkeypatch.setattr(access, "OwnedChromium", Owner)
    service = BrowserAccess(tmp_path / "browser", quiet_seconds=2, total_seconds=6)
    return SimpleNamespace(service=service, policy=BrowserPolicy("test", ["https://site.test"]),
                           events=events, script=script, windows=windows, calls=calls, owner=owner,
                           clock=clock, root=tmp_path / "browser", documents=documents,
                           window_outcomes=window_outcomes, exports=exports)


def normal_challenge(url):
    return AccessResponse(url, 403, CHALLENGE)


def clearance_reply():
    import time
    return {"user_agent": "Probe browser", "partition_key": {"topLevelSite": "https://site.test",
            "hasCrossSiteAncestor": False}, "cookies": [{"name": "cf_clearance", "value": "test-secret",
            "domain": "site.test", "hostOnly": True, "path": "/", "secure": True,
            "session": False, "expirationDate": time.time() + 600,
            "partitionKey": {"topLevelSite": "https://site.test", "hasCrossSiteAncestor": False}}]}


def test_clearance_handoff_closes_browser_and_reuses_saved_http_after_restart(runtime):
    runtime.service.keep_sessions = True
    runtime.service.reuse_clearance = True
    runtime.script.append({"url": URL, "status": 200, "body": "browser"})
    runtime.exports.append(clearance_reply())
    replay_calls = []
    def replay(url, clearance):
        replay_calls.append(clearance.user_agent)
        return AccessResponse(url, 200, "http")
    assert runtime.service.get(runtime.policy, URL, normal_challenge, clearance_get=replay).body == "http"
    assert runtime.events.count("owner_close") == 1
    assert runtime.service.get(runtime.policy, URL, lambda _: pytest.fail("normal after clearance"),
                               clearance_get=replay).body == "http"
    runtime.service.close()
    restarted = BrowserAccess(runtime.root, keep_sessions=True, reuse_clearance=True)
    try:
        assert restarted.get(runtime.policy, URL, lambda _: pytest.fail("HTTP without saved cookie"),
                             clearance_get=replay).body == "http"
        assert runtime.events.count("owner_open") == 1
        assert replay_calls == ["Probe browser"] * 3
    finally:
        restarted.close()


def test_failed_handoff_keeps_working_browser_without_repeated_export(runtime):
    runtime.service.keep_sessions = runtime.service.reuse_clearance = True
    runtime.exports.append(clearance_reply())
    runtime.script.extend([{"url": URL, "status": 200, "body": "first"},
                           {"url": URL, "status": 200, "body": "second"}])
    replay_calls = []
    def replay(url, clearance):
        replay_calls.append(url)
        return normal_challenge(url)
    try:
        assert runtime.service.get(runtime.policy, URL, normal_challenge, clearance_get=replay).body == "first"
        assert "owner_close" not in runtime.events
        assert runtime.service.get(runtime.policy, URL, normal_challenge, clearance_get=replay).body == "second"
        assert replay_calls == [URL]
    finally:
        runtime.service.close()


@pytest.mark.parametrize("exported,expected", [({"cookies": [], "partition_key": None,
    "user_agent": "private-UA"}, "no_clearance"), (None, "http_error")])
def test_handoff_diagnostic_reports_why_browser_is_retained(runtime, caplog, exported, expected):
    import logging
    caplog.set_level(logging.DEBUG)
    runtime.service.keep_sessions = runtime.service.reuse_clearance = True
    runtime.exports.append(clearance_reply() if exported is None else exported)
    runtime.script.append({"url": URL, "status": 200, "body": "private-browser-body"})
    try:
        result = runtime.service.get(runtime.policy, URL, normal_challenge,
            clearance_get=lambda url, clearance: normal_challenge(url))
        assert result.body == "private-browser-body"
        assert "event=browser_retained" in caplog.text and "reason=" + expected in caplog.text
        if exported is None:
            assert "event=replay_http" in caplog.text and "status=403" in caplog.text
        assert "test-secret" not in caplog.text and "private-UA" not in caplog.text
        assert "private-browser-body" not in caplog.text
    finally:
        runtime.service.close()


def test_rejected_saved_clearance_reopens_browser_and_renews(runtime):
    runtime.service.keep_sessions = runtime.service.reuse_clearance = True
    runtime.exports.extend([clearance_reply(), clearance_reply()])
    runtime.script.extend([{"url": URL, "status": 200, "body": "first"},
                           {"url": URL, "status": 200, "body": "renewed"}])
    responses = [AccessResponse(URL, 200, "first-http"), normal_challenge(URL),
                 AccessResponse(URL, 200, "renewed-http")]
    try:
        def replay(url, clearance):
            return responses.pop(0)
        assert runtime.service.get(runtime.policy, URL, normal_challenge, clearance_get=replay).body == "first-http"
        assert runtime.service.get(runtime.policy, URL, lambda _: pytest.fail("redundant cookie-free request"),
                                   clearance_get=replay).body == "renewed"
        assert runtime.events.count("owner_open") == 2
        assert runtime.events.count("owner_close") == 1
        runtime.script.append({"url": URL, "status": 200, "body": "stable-browser"})
        assert runtime.service.get(runtime.policy, URL, normal_challenge, clearance_get=replay).body == "stable-browser"
        assert runtime.events.count("owner_open") == 2
    finally:
        runtime.service.close()


def test_rejected_restart_cookie_keeps_browser_even_if_new_http_probe_passes(runtime):
    runtime.service.keep_sessions = runtime.service.reuse_clearance = True
    runtime.exports.append(clearance_reply())
    runtime.script.append({"url": URL, "status": 200, "body": "browser"})
    try:
        response = runtime.service.get(runtime.policy, URL, normal_challenge,
            clearance_get=lambda url, clearance: AccessResponse(url, 200, "http"), clearance_rejected=True)
        assert response.body == "browser"
        assert "owner_close" not in runtime.events
    finally:
        runtime.service.close()


def test_private_clearance_never_persists_and_dies_with_service(runtime):
    runtime.service.keep_sessions = runtime.service.reuse_clearance = True
    runtime.exports.append(clearance_reply())
    runtime.script.append({"url": URL, "status": 200, "body": "browser"})
    try:
        def replay(url, clearance):
            return AccessResponse(url, 200, "http")
        runtime.service.get(runtime.policy, URL, normal_challenge, clearance_get=replay, incognito=True)
        assert runtime.service.get(runtime.policy, URL, normal_challenge, clearance_get=replay,
                                   incognito=True).body == "http"
        assert not list(runtime.root.rglob("*.json"))
        assert not (runtime.root / "profiles").exists()
    finally:
        runtime.service.close()
    assert not runtime.service._clearances


def test_cancelled_handoff_does_not_save_cookie_or_return_late_success(runtime):
    runtime.service.keep_sessions = runtime.service.reuse_clearance = True
    cancel = threading.Event()
    runtime.exports.append(clearance_reply())
    runtime.script.append({"url": URL, "status": 200, "body": "browser"})
    def replay(url, clearance):
        cancel.set()
        return AccessResponse(url, 200, "late")
    try:
        with pytest.raises(BrowserAccessCancelled):
            runtime.service.get(runtime.policy, URL, normal_challenge, clearance_get=replay, cancel=cancel)
        assert not list(runtime.root.rglob("*.json"))
        assert "owner_close" in runtime.events
    finally:
        runtime.service.close()


@pytest.mark.parametrize("outcome", [{"error": "browser_operation_failed"}, {"cookies": []}])
def test_unavailable_clearance_export_preserves_browser(runtime, outcome):
    runtime.service.keep_sessions = runtime.service.reuse_clearance = True
    runtime.exports.append(outcome)
    runtime.script.append({"url": URL, "status": 200, "body": "browser"})
    try:
        result = runtime.service.get(runtime.policy, URL, normal_challenge,
            clearance_get=lambda *args: pytest.fail("replay without cookie"))
        assert result.body == "browser"
        assert "owner_close" not in runtime.events
    finally:
        runtime.service.close()


def test_network_failure_during_handoff_preserves_browser(runtime):
    runtime.service.keep_sessions = runtime.service.reuse_clearance = True
    runtime.exports.append(clearance_reply())
    runtime.script.append({"url": URL, "status": 200, "body": "browser"})
    def replay(*args):
        raise OSError("HTTP unavailable")
    try:
        assert runtime.service.get(runtime.policy, URL, normal_challenge, clearance_get=replay).body == "browser"
        assert "owner_close" not in runtime.events
    finally:
        runtime.service.close()


def test_optional_handoff_timeout_keeps_successful_browser_response(runtime):
    runtime.service.keep_sessions = runtime.service.reuse_clearance = True
    runtime.exports.append(clearance_reply())
    runtime.script.append({"url": URL, "status": 200, "body": "browser"})
    def slow_replay(*args):
        runtime.clock.now += 10
        raise OSError("HTTP timeout")
    try:
        assert runtime.service.get(runtime.policy, URL, normal_challenge, clearance_get=slow_replay).body == "browser"
        assert "owner_close" not in runtime.events
    finally:
        runtime.service.close()


def test_cancel_during_clearance_save_cannot_return_success(runtime, monkeypatch):
    from media_cli.services.browser_access.clearance import ClearanceStore
    runtime.service.keep_sessions = runtime.service.reuse_clearance = True
    cancel = threading.Event()
    runtime.exports.append(clearance_reply())
    runtime.script.append({"url": URL, "status": 200, "body": "browser"})
    def cancel_save(*args, **kwargs):
        cancel.set()
    monkeypatch.setattr(ClearanceStore, "save", cancel_save)
    try:
        with pytest.raises(BrowserAccessCancelled):
            runtime.service.get(runtime.policy, URL, normal_challenge, cancel=cancel,
                clearance_get=lambda url, clearance: AccessResponse(url, 200, "http"))
        assert not runtime.service._clearances
    finally:
        runtime.service.close()


def test_another_provider_cannot_reuse_in_memory_clearance(runtime):
    runtime.service.keep_sessions = runtime.service.reuse_clearance = True
    runtime.exports.append(clearance_reply())
    runtime.script.append({"url": URL, "status": 200, "body": "browser"})
    try:
        runtime.service.get(runtime.policy, URL, normal_challenge,
                            clearance_get=lambda url, clearance: AccessResponse(url, 200, "http"))
        other = BrowserPolicy("other", ["https://site.test"])
        assert runtime.service.get(other, URL, lambda url: AccessResponse(url, 200, "ordinary"),
                                   clearance_get=lambda *args: pytest.fail("cross-provider cookie")).body == "ordinary"
    finally:
        runtime.service.close()


@pytest.mark.parametrize("rotate_during", ["handoff", "saved"])
def test_rotated_http_clearance_is_persisted(runtime, rotate_during):
    from media_cli.services.browser_access.clearance import Clearance
    runtime.service.keep_sessions = runtime.service.reuse_clearance = True
    runtime.exports.append(clearance_reply())
    runtime.script.append({"url": URL, "status": 200, "body": "browser"})
    payload = clearance_reply()
    payload["cookies"][0]["value"] = "rotated-secret"
    updated = Clearance.from_export(runtime.policy, URL, payload)
    seen = []
    def replay(url, clearance):
        seen.append(clearance.cookies[0]["value"])
        rotate = len(seen) == (1 if rotate_during == "handoff" else 2)
        return AccessResponse(url, 200, "http", clearance=updated if rotate else None)
    runtime.service.get(runtime.policy, URL, normal_challenge, clearance_get=replay)
    runtime.service.get(runtime.policy, URL, normal_challenge, clearance_get=replay)
    runtime.service.close()
    restarted = BrowserAccess(runtime.root, keep_sessions=True, reuse_clearance=True)
    try:
        restarted.get(runtime.policy, URL, normal_challenge, clearance_get=replay)
        assert seen[-1] == "rotated-secret"
        if rotate_during == "handoff":
            assert seen[1] == "rotated-secret"
    finally:
        restarted.close()


def test_retained_session_reuses_verified_browser_without_normal_request(runtime):
    runtime.service.keep_sessions = True
    runtime.script.extend([{"url": URL, "status": 200, "body": "first"},
                           {"url": URL, "status": 200, "body": "second"}])
    try:
        assert runtime.service.get(runtime.policy, URL, normal_challenge).body == "first"
        states = []
        result = runtime.service.get(runtime.policy, URL, lambda _: pytest.fail("fresh HTTP session"),
                                     on_state=states.append)
        assert result.body == "second"
        assert states == []
        assert runtime.events.count("owner_open") == 1
        assert "owner_close" not in runtime.events
    finally:
        runtime.service.close()
    assert runtime.events[-3:] == ["owner_close", "helper_close", "bridge_close"]


def test_automatic_verification_gets_time_to_finish_without_revealing_popup(runtime):
    service = BrowserAccess(runtime.root, keep_sessions=True, total_seconds=30)
    states = []
    runtime.documents.extend([{"url": URL, "status": 200, "body": CHALLENGE}] * 10)
    runtime.script.append({"url": URL, "status": 200, "body": "ok"})
    try:
        assert service.get(runtime.policy, URL, normal_challenge, on_state=states.append).body == "ok"
        assert VerificationState.INTERACTION not in states
        assert [state for _, state in runtime.windows] == ["minimized"]
    finally:
        service.close()


def test_retained_private_profile_lives_until_service_shutdown(runtime):
    runtime.service.keep_sessions = True
    runtime.script.append({"url": URL, "status": 200, "body": "ok"})
    try:
        runtime.service.get(runtime.policy, URL, normal_challenge, incognito=True)
        assert list((runtime.root / "incognito").iterdir())
        assert not (runtime.root / "profiles").exists()
    finally:
        runtime.service.close()
    assert not list((runtime.root / "incognito").iterdir())


def test_retained_request_cancellation_closes_its_browser(runtime):
    runtime.service.keep_sessions = True
    runtime.script.append({"url": URL, "status": 200, "body": "ok"})
    cancel = threading.Event()
    try:
        runtime.service.get(runtime.policy, URL, normal_challenge)
        def cancelled():
            cancel.set()
            return {"url": URL, "status": 200, "body": "late"}
        runtime.script.append(cancelled)
        with pytest.raises(BrowserAccessCancelled):
            runtime.service.get(runtime.policy, URL, normal_challenge, cancel=cancel)
        assert "owner_close" in runtime.events
    finally:
        runtime.service.close()


def test_later_operation_does_not_inherit_completed_callers_cancellation(runtime):
    runtime.service.keep_sessions = True
    previous = threading.Event()
    runtime.script.extend([{"url": URL, "status": 200, "body": "first"},
                           {"url": URL, "status": 200, "body": "second"}])
    try:
        runtime.service.get(runtime.policy, URL, normal_challenge, cancel=previous)
        previous.set()
        assert runtime.service.get(runtime.policy, URL, normal_challenge).body == "second"
        assert runtime.events.count("owner_open") == 1
    finally:
        runtime.service.close()


def test_origin_change_preserves_provider_private_profile(runtime):
    runtime.service.keep_sessions = True
    other_url = "https://other.test/api"
    other_policy = BrowserPolicy("test", ["https://other.test"])
    runtime.script.extend([{"url": URL, "status": 200, "body": "first"},
                           {"url": other_url, "status": 200, "body": "second"}])
    try:
        runtime.service.get(runtime.policy, URL, normal_challenge, incognito=True)
        profile = next((runtime.root / "incognito").iterdir())
        marker = profile / "origin-storage"
        marker.write_text("browser state")
        assert runtime.service.get(other_policy, other_url, normal_challenge, incognito=True).body == "second"
        assert marker.read_text() == "browser state"
        assert list((runtime.root / "incognito").iterdir()) == [profile]
        assert runtime.events.count("owner_open") == 2
    finally:
        runtime.service.close()
    assert not profile.exists()


def test_service_close_prevents_later_requests(runtime):
    runtime.service.keep_sessions = True
    runtime.service.close()
    with pytest.raises(BrowserAccessCancelled):
        runtime.service.get(runtime.policy, URL, lambda _: pytest.fail("request after shutdown"))


def test_expired_clearance_loads_verification_target_and_hides_recovered_popup(runtime):
    runtime.service.keep_sessions = True
    runtime.script.append({"url": URL, "status": 200, "body": "first"})
    try:
        runtime.service.get(runtime.policy, URL, normal_challenge)
        runtime.script.extend([{"url": URL, "status": 403, "body": CHALLENGE},
                               {"url": URL, "status": 200, "body": "renewed"}])
        runtime.documents.extend([{"url": URL, "status": 200, "body": CHALLENGE}] * 2)
        assert runtime.service.get(runtime.policy, URL, normal_challenge).body == "renewed"
        assert runtime.events.count("owner_open") == 2, "Renewed challenge must reach a visible page"
        assert [state for _, state in runtime.windows] == ["minimized", "normal", "minimized"]
    finally:
        runtime.service.close()


def test_shutdown_attempts_every_provider_even_if_one_cleanup_fails(runtime):
    runtime.service.keep_sessions = True
    runtime.script.extend([{"url": URL, "status": 200, "body": "first"},
                           {"url": URL, "status": 200, "body": "other"}])
    runtime.service.get(runtime.policy, URL, normal_challenge)
    runtime.service.get(BrowserPolicy("other", ["https://site.test"]), URL, normal_challenge)
    def failure():
        raise RuntimeError("cleanup failed")
    first = runtime.service._sessions[("test", False)]
    first.browser_stack.callback(failure)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        runtime.service.close()
    assert runtime.events.count("owner_close") == 2


def test_normal_fast_path_has_no_profile_browser_or_callbacks(runtime):
    result = AccessResponse(URL, 200, "ok")
    states = []
    assert runtime.service.get(runtime.policy, URL, lambda _: result, on_state=states.append) is result
    assert runtime.events == states == []
    assert not runtime.root.exists()


def test_normal_errors_do_not_open_browser(runtime):
    for status in (403, 429, 500, 503):
        result = AccessResponse(URL, status, "ordinary error")
        assert runtime.service.get(runtime.policy, URL, lambda _, result=result: result) is result
    def fail(_):
        raise OSError("network unavailable")
    with pytest.raises(OSError):
        runtime.service.get(runtime.policy, URL, fail)
    assert runtime.events == []


def test_quiet_success_and_reverse_order_cleanup(runtime):
    runtime.script.append({"url": URL, "status": 200, "body": "ok"})
    states = []
    result = runtime.service.get(runtime.policy, URL, normal_challenge, on_state=states.append)
    assert result.body == "ok"
    assert states == [VerificationState.QUIET, VerificationState.VERIFIED]
    assert runtime.windows == []
    assert runtime.events[-3:] == ["owner_close", "helper_close", "bridge_close"]
    assert (runtime.root / "profiles" / "test").exists()


def test_reveals_once_after_quiet_grace(runtime):
    runtime.script.extend([{"url": URL, "status": 403, "body": CHALLENGE}] * 3
                          + [{"url": URL, "status": 200, "body": "ok"}])
    states = []
    runtime.service.get(runtime.policy, URL, normal_challenge, on_state=states.append)
    assert len(runtime.windows) == 1
    assert runtime.windows[0][0] >= 2
    assert runtime.windows[0][1] == "normal"
    assert states == [VerificationState.QUIET, VerificationState.INTERACTION, VerificationState.VERIFIED]


def test_total_timeout_is_bounded_and_cleans_up(runtime):
    states = []
    with pytest.raises(BrowserRequestTimeout):
        runtime.service.get(runtime.policy, URL, normal_challenge, on_state=states.append)
    assert runtime.clock.now <= 6
    assert states[-1] == VerificationState.TIMED_OUT
    assert runtime.events[-3:] == ["owner_close", "helper_close", "bridge_close"]
    assert all(now + kw["timeout"] <= 6 for now, _, kw in runtime.calls)


@pytest.mark.parametrize("close_browser", [True, False])
def test_late_success_loses_to_close_or_cancel(runtime, close_browser):
    cancel = threading.Event()
    def close_then_reply():
        (runtime.owner.closed if close_browser else cancel).set()
        return {"url": URL, "status": 200, "body": "late"}
    runtime.script.append(close_then_reply)
    states = []
    with pytest.raises(BrowserAccessCancelled):
        runtime.service.get(runtime.policy, URL, normal_challenge, cancel=cancel, on_state=states.append)
    assert states[-1] == VerificationState.CANCELLED
    assert VerificationState.VERIFIED not in states


def test_pre_cancel_does_not_even_call_normal_getter(runtime):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(BrowserAccessCancelled):
        runtime.service.get(runtime.policy, URL, lambda _: pytest.fail("request called"), cancel=cancel)
    assert not runtime.root.exists()


@pytest.mark.parametrize("reply,error", [
    ({"error": "browser_closed"}, BrowserAccessCancelled),
    ({"error": "unknown"}, BrowserProcessError),
    ({"url": "https://other.test/", "status": 200, "body": "x"}, BrowserPolicyError),
])
def test_invalid_reply_or_closed_browser_fails_cleanly(runtime, reply, error):
    runtime.script.append(reply)
    with pytest.raises(error):
        runtime.service.get(runtime.policy, URL, normal_challenge)
    assert runtime.events[-3:] == ["owner_close", "helper_close", "bridge_close"]


def test_reload_errors_and_request_timeouts_can_recover(runtime):
    runtime.script.extend([{"error": "browser_operation_failed"}, BrowserRequestTimeout("transient"),
                           {"url": URL, "status": 200, "body": "ok"}])
    assert runtime.service.get(runtime.policy, URL, normal_challenge).body == "ok"


def test_incognito_uses_disposable_profile_without_reading_saved_state(runtime):
    runtime.script.append({"url": URL, "status": 200, "body": "ok"})
    runtime.service.get(runtime.policy, URL, normal_challenge, incognito=True)
    helper = next(event for event in runtime.events if isinstance(event, tuple))
    assert helper[1:3] == ("test", False)
    assert not (runtime.root / "profiles").exists()
    assert list((runtime.root / "incognito").iterdir()) == []


def test_same_origin_bootstrap_only(runtime):
    runtime.script.append({"url": URL, "status": 200, "body": "ok"})
    runtime.service.get(runtime.policy, URL, normal_challenge, bootstrap_url="https://site.test/")
    assert ("helper_open", "test", True, "https://site.test/") in runtime.events
    with pytest.raises(BrowserPolicyError):
        runtime.service.get(BrowserPolicy("test", ["https://site.test", "https://other.test"]), URL,
                            normal_challenge, bootstrap_url="https://other.test/")


def test_state_callback_error_still_cleans_up(runtime):
    def fail(state):
        raise RuntimeError("UI cancelled")
    with pytest.raises(RuntimeError, match="UI cancelled"):
        runtime.service.get(runtime.policy, URL, normal_challenge, on_state=fail)
    assert runtime.events[-3:] == ["owner_close", "helper_close", "bridge_close"]


def test_response_repr_does_not_expose_urls_headers_or_body():
    response = AccessResponse("https://site.test/?token=secret", 200, "private body", {"Set-Cookie": "private"})
    assert "secret" not in repr(response)
    assert "private" not in repr(response)


def test_waits_for_document_verification_before_requesting_api(runtime):
    runtime.documents.extend([{"url": URL, "status": 200, "body": CHALLENGE}] * 3)
    runtime.script.append({"url": URL, "status": 200, "body": "ok"})
    assert runtime.service.get(runtime.policy, URL, normal_challenge).body == "ok"
    assert sum(command["operation"] == "fetch" for _, command, _ in runtime.calls) == 1
    assert sum(command["operation"] == "document" for _, command, _ in runtime.calls) == 4


def test_plain_rate_limit_after_verification_is_not_reported_as_verified(runtime):
    runtime.script.append({"url": URL, "status": 429, "body": "Rate limited"})
    states = []
    result = runtime.service.get(runtime.policy, URL, normal_challenge, on_state=states.append)
    assert result.status == 429
    assert VerificationState.VERIFIED not in states
    assert sum(command["operation"] == "fetch" for _, command, _ in runtime.calls) == 1


def test_reply_cannot_switch_to_another_approved_origin(runtime):
    policy = BrowserPolicy("test", ["https://site.test", "https://other.test"])
    runtime.script.append({"url": "https://other.test/", "status": 200, "body": "wrong origin"})
    with pytest.raises(BrowserPolicyError):
        runtime.service.get(policy, URL, normal_challenge)


def test_transient_reveal_error_can_retry_within_deadline(runtime):
    runtime.script.extend([{"url": URL, "status": 403, "body": CHALLENGE}] * 3
                          + [{"url": URL, "status": 200, "body": "ok"}])
    runtime.window_outcomes.append(BrowserWindowNotReady("reload"))
    states = []
    assert runtime.service.get(runtime.policy, URL, normal_challenge, on_state=states.append).body == "ok"
    assert len(runtime.windows) == 2
    assert states.count(VerificationState.INTERACTION) == 1


def test_cancel_during_reveal_reports_terminal_state(runtime):
    runtime.script.extend([{"url": URL, "status": 403, "body": CHALLENGE}] * 3)
    def cancel_window():
        raise BrowserAccessCancelled("closed during reveal")
    runtime.window_outcomes.append(cancel_window)
    states = []
    with pytest.raises(BrowserAccessCancelled):
        runtime.service.get(runtime.policy, URL, normal_challenge, on_state=states.append)
    assert states[-1] == VerificationState.CANCELLED
