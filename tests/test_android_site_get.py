import json
import threading
import time
from types import SimpleNamespace

import pytest

from media_cli.services.android_webview.client import ProbeClient, ProbeError, site_result

URL = "https://animepahe.pw/api?m=search&q=test"
TICKET = "a" * 64


def result(**changes):
    return {"url": URL, "status": 200, "body": "page", "headers": {}, "user_agent": "Native UA", **changes}


class SiteClient(ProbeClient):
    def __init__(self, polls=None):
        self.calls = []
        self.polls = list(polls or [{"status": "ok", "result": result()}])

    def _request(self, method, path, body=None, **options):
        self.calls.append((method, path, body, options))
        if path == "/v1/health":
            return {"site_get": True, "worker": "ready"}
        if path == "/v1/jobs":
            return {"job": {"id": "job-1"}}
        if path.endswith("/cancel"):
            return {}
        return {"job": self.polls.pop(0)}


def run(client, url=URL, **options):
    method = getattr(client, "run_site_get", None)
    assert callable(method), "Website jobs need the bounded site-get client"
    return method("session-1", url, time.monotonic() + 3, **options)


def test_site_get_submits_json_not_javascript_and_keeps_deadline():
    client = SiteClient()
    assert run(client) == {"status": "ok", "result": result()}
    submit = next(call for call in client.calls if call[1] == "/v1/jobs")
    assert json.loads(submit[2]["script"]) == {"url": URL}
    assert "mediated" not in submit[2]
    assert 0 < submit[2]["timeout_ms"] <= 3000
    assert all(call[3].get("deadline") for call in client.calls)
    assert not any(call[1].endswith("/cancel") for call in client.calls)


@pytest.mark.parametrize(
    "url",
    [
        "http://animepahe.pw/",
        "https://animepahe.pw.evil.test/",
        "https://i.animepahe.pw/",
        "https://user:pass@animepahe.pw/",
        "https://animepahe.pw:444/",
        "https://kwik.cx/#x",
        "https://kwik.cx/%0a",
        "https://kwik.cx/\\evil",
        "https://127.0.0.1/",
    ],
)
def test_site_get_rejects_unsafe_destination_before_any_request(url):
    client = SiteClient()
    with pytest.raises(ProbeError):
        run(client, url)
    assert not client.calls


@pytest.mark.parametrize(
    "url",
    [
        "https://animepahe.org/",
        "https://animepahe.com/",
        "https://animepahe.ru/",
        "https://kwik.cx/e/test",
        "https://kwik.si:443/e/test",
    ],
)
def test_fixed_website_hosts_are_accepted(url):
    assert run(SiteClient([{"status": "ok", "result": result(url=url)}]), url)["result"]["url"] == url


def test_repeated_verification_ticket_reveals_once_and_only_while_running():
    verification = {"ticket": TICKET, "host": "animepahe.pw"}
    client = SiteClient(
        [
            {"status": "running", "verification": verification},
            {"status": "running", "verification": verification},
            {"status": "ok", "result": result(), "verification": verification},
        ]
    )
    revealed = []
    assert run(client, on_interaction=lambda *args: revealed.append(args))["result"] == result()
    assert revealed == [("job-1", TICKET, "animepahe.pw")]


@pytest.mark.parametrize(
    "verification",
    [
        {"ticket": "not-a-ticket", "host": "animepahe.pw"},
        {"ticket": TICKET, "host": "kwik.cx"},
        {"ticket": TICKET.upper(), "host": "animepahe.pw"},
    ],
)
def test_invalid_reveal_never_launches_and_cancels(verification):
    client = SiteClient([{"status": "running", "verification": verification}])
    with pytest.raises(ProbeError):
        run(client, on_interaction=lambda *args: pytest.fail("invalid reveal"))
    assert client.calls[-1][1] == "/v1/jobs/job-1/cancel"


@pytest.mark.parametrize(
    "changes",
    [
        {"url": "https://animepahe.org/"},
        {"status": True},
        {"body": "x" * 262145},
        {"body": "\u00e9" * 131073},
        {"headers": {"X-Test": 1}},
        {"user_agent": "UA\r\nCookie: private"},
    ],
)
def test_native_result_validation_cancels_invalid_job(changes):
    client = SiteClient([{"status": "ok", "result": result(**changes)}])
    with pytest.raises(ProbeError):
        run(client)
    assert client.calls[-1][1].endswith("/cancel")


@pytest.mark.parametrize(
    "target",
    [
        "https://animepahe.pw/other?m=search&q=test",
        "https://animepahe.pw/api?m=search&q=other",
        "https://animepahe.pw/api?q=test&m=search",
        "https://animepahe.pw/api?m=search&q=%74est",
        "https://animepahe.pw:443/api?m=search&q=test",
    ],
)
def test_native_result_requires_exact_requested_target(target):
    job = {"status": "ok", "result": result(url=target)}
    with pytest.raises(ProbeError):
        site_result(job, URL)
    client = SiteClient([job])
    with pytest.raises(ProbeError):
        run(client)
    assert client.calls[-1][1].endswith("/cancel")


def test_callback_error_and_cancellation_always_cancel_owned_job():
    client = SiteClient([{"status": "running", "verification": {"ticket": TICKET, "host": "animepahe.pw"}}])

    def fail(*args):
        raise ValueError("must not escape")

    with pytest.raises(ProbeError):
        run(client, on_interaction=fail)
    assert client.calls[-1][1].endswith("/cancel")
    cancelled = threading.Event()
    cancelled.set()
    client = SiteClient()
    with pytest.raises(ProbeError, match="cancelled"):
        run(client, cancel=cancelled)
    assert not client.calls


def test_default_reveal_uses_fixed_activity_and_ticket_only(monkeypatch):
    from media_cli.services.android_webview import client as module

    client = SiteClient(
        [
            {"status": "running", "verification": {"ticket": TICKET, "host": "animepahe.pw"}},
            {"status": "ok", "result": result()},
        ]
    )
    launches = []

    def launch(args, **options):
        launches.append((args, options))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", launch)
    assert run(client)["result"] == result()
    assert launches[0][0] == [
        "am",
        "start",
        "-n",
        "dev.anyplay.webview/.worker.VerificationActivity",
        "--es",
        "job_id",
        "job-1",
        "--es",
        "ticket",
        TICKET,
    ]
    assert launches[0][1]["capture_output"] is True
    assert 0 < launches[0][1]["timeout"] <= 3


def test_site_lease_requires_site_capability_not_mediated_fetch():
    client = SiteClient()
    client.ensure_started = lambda *a, **kw: {"site_get": True}
    client.create_session = lambda profile, **kw: {"session_id": "owned"}
    with client.session("animepahe", deadline=time.monotonic() + 2) as sid:
        assert sid == "owned"
    assert client.calls[-1][1] == "/v1/sessions/owned/close"


def test_site_get_poll_failure_cancels_and_preexpired_never_dispatches():
    client = SiteClient()
    original = client._request

    def broken(method, path, body=None, **options):
        if path == "/v1/jobs/job-1":
            raise ProbeError("Local companion unavailable")
        return original(method, path, body, **options)

    client._request = broken
    with pytest.raises(ProbeError):
        run(client)
    assert client.calls[-1][1].endswith("/cancel")
    client = SiteClient()
    with pytest.raises(ProbeError, match="timed out"):
        client.run_site_get("s", URL, time.monotonic() - 1)
    assert not client.calls


@pytest.mark.parametrize("status", ["failed", "cancelled", "timeout", "unsupported"])
def test_site_get_returns_terminal_failure_without_replay(status):
    client = SiteClient([{"status": status}])
    assert run(client) == {"status": status}
    assert len([call for call in client.calls if call[1] == "/v1/jobs"]) == 1
    assert not any(call[1].endswith("/cancel") for call in client.calls)
