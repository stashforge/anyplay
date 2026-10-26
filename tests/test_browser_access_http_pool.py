"""Connection reuse tests use synthetic clearance and no browser or network."""

import dataclasses
import threading
import time
from types import SimpleNamespace

import pytest

from media_cli.services.browser_access.access import AccessResponse
from media_cli.services.browser_access.clearance import Clearance, ClearanceRequestError
from media_cli.services.browser_access.http_pool import ClearanceHttpPool
from media_cli.services.browser_access.policy import BrowserPolicy
from media_cli.services.browser_access.profiles import BrowserAccessCancelled

URL = "https://site.test/api"
CHALLENGE = '<title>Just a moment...</title><script src="/cdn-cgi/challenge-platform/a"></script>'


def snapshot(provider="animexin", origin="https://site.test"):
    return Clearance.from_export(BrowserPolicy(provider, [origin]), origin + "/api", {
        "user_agent": "Synthetic Chrome", "partition_key": None,
        "cookies": [{"name": "cf_clearance", "value": "synthetic-secret", "domain": origin[8:],
                     "path": "/", "secure": True, "hostOnly": True,
                     "expirationDate": time.time() + 600}],
    })


@pytest.fixture
def clients(monkeypatch):
    import curl_cffi.requests
    created = []
    def make(**kwargs):
        client = SimpleNamespace(closed=False, options=kwargs)
        client.close = lambda: setattr(client, "closed", True)
        created.append(client)
        return client
    monkeypatch.setattr(curl_cffi.requests, "Session", make)
    return created


def test_reuses_one_non_thread_local_client_across_workers(clients):
    pool, seen = ClearanceHttpPool(), []
    def request(url, clearance, **kwargs):
        seen.append(kwargs["session"])
        return AccessResponse(url, 200, "ok")
    for _ in range(2):
        worker = threading.Thread(target=lambda: pool.get(URL, snapshot(), request=request))
        worker.start()
        worker.join(2)
        assert not worker.is_alive()
    assert len(clients) == 1 and seen == [clients[0], clients[0]]
    assert clients[0].options["use_thread_local_curl"] is False
    pool.close()
    assert clients[0].closed


def test_challenge_retries_once_then_keeps_new_client(clients):
    pool, seen = ClearanceHttpPool(), []
    item = snapshot()
    def request(url, clearance, **kwargs):
        assert clearance is item
        seen.append(kwargs["session"])
        return AccessResponse(url, 403, CHALLENGE) if len(seen) == 1 else AccessResponse(url, 200, "ok")
    assert pool.get(URL, item, request=request).status == 200
    assert clients[0].closed and not clients[1].closed
    pool.get(URL, item, request=request)
    assert seen == [clients[0], clients[1], clients[1]]
    pool.close()


@pytest.mark.parametrize("status,body,count", [(403, CHALLENGE, 2), (403, "Forbidden", 1),
                                               (500, "error", 1), (200, "ok", 1)])
def test_only_recognized_challenge_gets_one_retry(clients, status, body, count):
    pool, seen = ClearanceHttpPool(), []
    def request(url, clearance, **kwargs):
        seen.append(kwargs["session"])
        return AccessResponse(url, status, body)
    assert pool.get(URL, snapshot(), request=request).status == status
    assert len(seen) == count
    pool.close()


def test_provider_origin_and_identity_are_isolated(clients):
    pool, seen = ClearanceHttpPool(), []
    def request(url, clearance, **kwargs):
        seen.append(kwargs["session"])
        return AccessResponse(url, 200, "ok")
    item = snapshot()
    pool.get(URL, item, request=request)
    pool.get(URL, snapshot("animepahe"), request=request)
    pool.get("https://other.test/api", snapshot(origin="https://other.test"), request=request)
    pool.get(URL, dataclasses.replace(item, user_agent="Another Chrome"), request=request)
    assert len(clients) == 4 and clients[0].closed
    pool.close()
    assert all(client.closed for client in clients)


def test_timeout_is_shared_by_both_attempts(clients):
    pool, budgets = ClearanceHttpPool(), []
    def request(url, clearance, **kwargs):
        budgets.append(kwargs["timeout"])
        time.sleep(.03)
        return AccessResponse(url, 403, CHALLENGE)
    pool.get(URL, snapshot(), timeout=.5, request=request)
    assert 0 < budgets[1] < budgets[0] <= .5
    pool.close()


def test_cancelled_request_does_not_retry_or_poison_next_operation(clients):
    pool, cancel = ClearanceHttpPool(), threading.Event()
    def request(url, clearance, **kwargs):
        cancel.set()
        return AccessResponse(url, 403, CHALLENGE)
    with pytest.raises(BrowserAccessCancelled):
        pool.get(URL, snapshot(), cancel=cancel, request=request)
    assert len(clients) == 1
    assert pool.get(URL, snapshot(), request=lambda url, *a, **kw: AccessResponse(url, 200, "ok")).status == 200
    pool.close()


def test_closed_pool_never_reopens(clients):
    pool = ClearanceHttpPool()
    pool.close()
    with pytest.raises(BrowserAccessCancelled):
        pool.get(URL, snapshot())
    assert clients == []


def test_invalid_scope_never_creates_client(clients):
    pool = ClearanceHttpPool()
    with pytest.raises(ClearanceRequestError):
        pool.get("https://evil.test/api", snapshot())
    assert clients == []


def test_waiting_worker_can_cancel_without_touching_active_client(clients):
    pool, entered, release, cancel = ClearanceHttpPool(), threading.Event(), threading.Event(), threading.Event()
    errors = []
    def request(url, clearance, **kwargs):
        entered.set()
        assert release.wait(2)
        return AccessResponse(url, 200, "ok")
    first = threading.Thread(target=lambda: pool.get(URL, snapshot(), request=request))
    first.start()
    assert entered.wait(2)
    def wait():
        try:
            pool.get(URL, snapshot(), cancel=cancel, request=request)
        except BrowserAccessCancelled:
            errors.append("cancelled")
    second = threading.Thread(target=wait)
    second.start()
    cancel.set()
    second.join(1)
    assert errors == ["cancelled"] and not clients[0].closed
    release.set()
    first.join(2)
    pool.close()


def test_submillisecond_budget_never_becomes_unbounded_curl_timeout(clients):
    pool = ClearanceHttpPool()
    with pytest.raises(ClearanceRequestError):
        pool.get(URL, snapshot(), timeout=.0001)
    assert not clients


def test_partition_change_replaces_client_but_cookie_rotation_does_not(clients):
    pool, seen = ClearanceHttpPool(), []
    item = snapshot()
    def request(url, clearance, **kwargs):
        seen.append(kwargs["session"])
        return AccessResponse(url, 200, "ok")
    pool.get(URL, item, request=request)
    rotated = dataclasses.replace(item, _cookies=(dataclasses.replace(item._cookies[0], value="rotated"),))
    pool.get(URL, rotated, request=request)
    partition = ("https://site.test", False)
    partitioned = dataclasses.replace(item, _partition_key=partition,
        _cookies=(dataclasses.replace(item._cookies[0], partition=partition),))
    pool.get(URL, partitioned, request=request)
    assert seen == [clients[0], clients[0], clients[1]] and clients[0].closed
    pool.close()


def test_shutdown_does_not_wait_for_a_stalled_request(clients):
    pool, entered, release, closed = ClearanceHttpPool(), threading.Event(), threading.Event(), threading.Event()
    errors = []
    def request(url, clearance, **kwargs):
        entered.set()
        assert release.wait(2)
        return AccessResponse(url, 200, "ok")
    def run():
        try:
            pool.get(URL, snapshot(), request=request)
        except BrowserAccessCancelled:
            errors.append("cancelled")
    def close():
        pool.close()
        closed.set()
    worker = threading.Thread(target=run)
    worker.start()
    assert entered.wait(2)
    closer = threading.Thread(target=close)
    closer.start()
    try:
        assert closed.wait(.3)
        assert not clients[0].closed  # Never close a curl handle mid-transfer.
    finally:
        release.set()
        worker.join(2)
        closer.join(2)
    assert errors == ["cancelled"] and clients[0].closed
