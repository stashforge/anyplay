import threading
import time
from types import SimpleNamespace

import pytest

from media_cli.extractors import senshi_runtime as runtime
from media_cli.services.android_webview import client as transport


def test_termux_dispatch_precedes_node_or_linux_dependencies(monkeypatch):
    expected = [{"source": "synthetic"}]
    monkeypatch.setattr(runtime, "is_termux", lambda: True, raising=False)
    monkeypatch.setattr(runtime, "_resolve_webview", lambda remote, **kw: expected, raising=False)
    monkeypatch.setattr("shutil.which", lambda _: pytest.fail("Termux must not inspect Node"))
    assert runtime.resolve_vidcloud(123) is expected


def test_linux_still_uses_the_existing_worker(monkeypatch):
    monkeypatch.setattr(runtime, "is_termux", lambda: False, raising=False)
    monkeypatch.setattr(
        runtime, "_resolve_webview", lambda *a, **kw: pytest.fail("Linux must not use the companion"), raising=False
    )
    monkeypatch.setattr("shutil.which", lambda _: "/synthetic/node")
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout="v26.0.0"))
    monkeypatch.setattr(runtime, "_fetch_runtime", lambda *a: "unused")
    monkeypatch.setattr(runtime, "_run", lambda *a: [{"source": "linux"}])
    assert runtime.resolve_vidcloud(123) == [{"source": "linux"}]


def test_worker_thread_start_uses_signed_wake_without_activity_or_signals(monkeypatch):
    c = transport.ProbeClient()
    calls = []

    def request(method, path, **kw):
        calls.append((path, kw))
        if len(calls) == 1:
            raise transport.CompanionUnavailable("stopped")
        return {"mediated_fetch": True}

    c._request = request
    c._load_token = lambda **kw: "a" * 64
    monkeypatch.setattr(transport.sys, "platform", "android")
    launches = []

    def launch(args, **kw):
        launches.append(args)
        return SimpleNamespace(returncode=1, stdout="Broadcast completed: result=-1\n", stderr="")

    monkeypatch.setattr(transport.subprocess, "run", launch)
    outcomes = []

    def work():
        try:
            outcomes.append(c.ensure_started(time.monotonic() + 3))
        except Exception as error:
            outcomes.append(error)

    thread = threading.Thread(target=work)
    thread.start()
    thread.join(4)
    assert not thread.is_alive()
    assert outcomes == [{"mediated_fetch": True}]
    assert len(launches) == 1 and launches[0][1] == "broadcast"
    assert "LaunchActivity" not in str(launches)
    assert all(kw.get("deadline") is not None for _, kw in calls)


def test_session_closes_only_its_own_id_and_releases_after_error():
    c = transport.ProbeClient()
    c.ensure_started = lambda *a, **kw: {"mediated_fetch": True}
    c.create_session = lambda *a, **kw: {"session_id": "owned"}
    calls = []
    c._request = lambda method, path, body=None, **kw: calls.append(path) or {}
    with pytest.raises(ValueError):
        with c.session("senshi", deadline=time.monotonic() + 2):
            raise ValueError("synthetic")
    assert calls == ["/v1/sessions/owned/close"]
    with c.session("senshi", deadline=time.monotonic() + 2) as sid:
        assert sid == "owned"


def test_cancelled_or_unpaired_session_never_closes_another_session():
    c = transport.ProbeClient()
    cancelled = threading.Event()
    cancelled.set()
    c.ensure_started = lambda *a, **kw: pytest.fail("pre-cancel must not wake")
    with pytest.raises(transport.ProbeError, match="cancelled"):
        with c.session("senshi", deadline=time.monotonic() + 1, cancel=cancelled):
            pytest.fail("must not enter")
    c.ensure_started = lambda *a, **kw: {"mediated_fetch": True}
    c.create_session = lambda *a, **kw: (_ for _ in ()).throw(transport.ProbeError("existing session"))
    c._request = lambda *a, **kw: pytest.fail("must not close an unowned session")
    with pytest.raises(transport.ProbeError):
        with c.session("senshi", deadline=time.monotonic() + 1):
            pytest.fail("must not enter")


def test_start_waits_for_teardown_without_waking_again(monkeypatch):
    c = transport.ProbeClient()
    responses = iter([{"mediated_fetch": True, "worker": "stopping"}, {"mediated_fetch": True, "worker": "idle"}])
    c._request = lambda *a, **kw: next(responses)
    monkeypatch.setattr(c, "_wake", lambda *a: pytest.fail("existing service must not wake"))
    assert c.ensure_started(time.monotonic() + 1)["worker"] == "idle"
