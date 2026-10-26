import base64
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from curl_cffi import CurlOpt

from media_cli.extractors import senshi_runtime as runtime
from media_cli.extractors import senshi_webview as webview
from media_cli.services.android_webview.client import ProbeError


@pytest.fixture
def bridge(monkeypatch):
    sessions = []

    class Session:
        def __init__(self, **kw):
            assert kw == {"impersonate": "chrome", "trust_env": False,
                          "curl_options": {CurlOpt.IPRESOLVE: 1, CurlOpt.PROXY: ""}}
            sessions.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def request(self, method, url, **kw):
            assert url == "https://s.vidcloud.se/exchange"
            assert kw["allow_redirects"] is False
            assert kw["headers"]["User-Agent"] == runtime.USER_AGENT
            kw["content_callback"](b"synthetic-response")
            return SimpleNamespace(status_code=200, headers={})

    import curl_cffi.requests

    monkeypatch.setattr(curl_cffi.requests, "Session", Session)
    monkeypatch.setattr(runtime, "_fetch_runtime", lambda *a: "window.__oct={open:async()=>[]};")

    class Client:
        def __init__(self):
            self.closed = 0
            self.calls = []
            self.results = [{"status": "ok", "result": [{"source": "synthetic"}]}]

        @contextmanager
        def session(self, name, *, deadline, cancel=None):
            assert name == "senshi"
            self.deadline = deadline
            try:
                yield "owned"
            finally:
                self.closed += 1

        def run_mediated(self, sid, script, transport, deadline, cancel=None):
            assert sid == "owned" and deadline == self.deadline
            answer = transport(
                {"id": 1, "url": "https://s.vidcloud.se/exchange", "method": "GET", "headers": {}, "body": None}
            )
            assert base64.b64decode(answer["body"]) == b"synthetic-response"
            self.calls.append(deadline)
            return self.results.pop(0)

    return Client(), sessions


def test_webview_reuses_policy_and_closes_owned_session(bridge):
    client, sessions = bridge
    deadline = time.monotonic() + 2
    assert webview.resolve(123, deadline=deadline, client=client) == [{"source": "synthetic"}]
    assert client.calls == [deadline] and client.closed == 1 and len(sessions) == 1


def test_terminal_runtime_retry_uses_fresh_http_session_same_deadline(bridge):
    client, sessions = bridge
    client.results.insert(0, {"status": "failed"})
    deadline = time.monotonic() + 2
    assert webview.resolve(123, deadline=deadline, client=client)
    assert client.calls == [deadline, deadline]
    assert len(sessions) == 2 and sessions[0] is not sessions[1]


def test_worker_loss_retry_never_reuses_the_invalidated_session(bridge):
    client, sessions = bridge
    leases = []

    @contextmanager
    def lease(*args, **kwargs):
        sid = "lease" + str(len(leases) + 1)
        leases.append(sid)
        yield sid

    def job(sid, *args):
        if len(sessions) == 1:
            return {"status": "failed"}
        assert sid == "lease2", "worker loss invalidated lease1"
        return {"status": "ok", "result": [{"source": "synthetic"}]}

    client.session = lease
    client.run_mediated = job
    assert webview.resolve(123, deadline=time.monotonic() + 2, client=client)
    assert leases == ["lease1", "lease2"]


def test_transport_failure_does_not_replay_and_still_closes(bridge):
    client, sessions = bridge

    def failed(*a):
        raise ProbeError("Mediated request failed")

    client.run_mediated = failed
    with pytest.raises(runtime.SenshiRuntimeError, match="Mediated request failed"):
        webview.resolve(123, deadline=time.monotonic() + 2, client=client)
    assert len(sessions) == 1 and client.closed == 1


def test_cancel_after_job_wins_over_success_and_cannot_cache(bridge):
    client, _ = bridge
    cancel = threading.Event()
    original = client.run_mediated

    def done(*a):
        result = original(*a)
        cancel.set()
        return result

    client.run_mediated = done
    with pytest.raises(runtime.SenshiRuntimeError, match="cancelled"):
        webview.resolve(123, deadline=time.monotonic() + 2, cancel=cancel, client=client)
    assert client.closed == 1


def test_cancelled_native_job_does_not_retry(bridge):
    client, sessions = bridge
    client.results = [{"status": "cancelled"}]
    with pytest.raises(runtime.SenshiRuntimeError):
        webview.resolve(123, deadline=time.monotonic() + 2, client=client)
    assert len(sessions) == 1 and client.closed == 1


def test_unexpected_android_errors_are_redacted_at_public_boundary(monkeypatch):
    monkeypatch.setattr(runtime, "is_termux", lambda: True)

    def failed(*a, **kw):
        raise ValueError("https://s.vidcloud.se/private-signed-value")

    monkeypatch.setattr(runtime, "_resolve_webview", failed)
    with pytest.raises(runtime.SenshiRuntimeError) as error:
        runtime.resolve_vidcloud(123)
    assert str(error.value) == "Senshi local extraction failed"
