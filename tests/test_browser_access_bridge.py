"""Authenticated, bounded, single-request browser bridge contracts."""

import http.client
import json
import socket
import threading
import time

import pytest

from media_cli.services.browser_access import bridge as bridge_module
from media_cli.services.browser_access.bridge import (
    BrowserBridge,
    BrowserBridgeClosed,
    BrowserRequestTimeout,
)
from media_cli.services.browser_access.profiles import BrowserAccessCancelled


@pytest.fixture
def bridge():
    instance = BrowserBridge(max_message_bytes=1024)
    try:
        instance.__enter__()
    except PermissionError:
        pytest.skip("Local sockets require host execution in this environment")
    try:
        yield instance
    finally:
        instance.close()


def call(bridge, method, path, body=None, *, token=None, host=None, raw=False):
    config = bridge.extension_config()
    port = int(config["endpoint"].rsplit(":", 1)[1])
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    headers = {"Authorization": "Bearer " + (config["token"] if token is None else token)}
    if host is not None:
        headers["Host"] = host
    if body is not None:
        body = body if raw else json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    try:
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def start_request(bridge, *, timeout=2, cancel=None, payload=None):
    outcomes = []

    def run():
        try:
            outcomes.append(bridge.request(payload or {"operation": "fetch", "url": "https://provider.test/api"},
                                           timeout=timeout, cancel=cancel))
        except Exception as exc:
            outcomes.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    return worker, outcomes


def claim(bridge):
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        status, body = call(bridge, "GET", "/command")
        if status == 200:
            return json.loads(body)
        assert status == 204
        time.sleep(0.005)
    pytest.fail("request was not published")


def test_validated_reply_reaches_only_its_request(bridge):
    worker, outcomes = start_request(bridge)
    command = claim(bridge)
    assert command["payload"]["operation"] == "fetch"
    status, _ = call(bridge, "POST", "/result", {"id": command["id"], "result": {"status": 200, "body": "ok"}})
    assert status == 204
    worker.join(2)
    assert outcomes == [{"status": 200, "body": "ok"}]
    assert call(bridge, "POST", "/result", {"id": command["id"], "result": {"replayed": True}})[0] == 409


@pytest.mark.parametrize("method,path", [("GET", "/command"), ("POST", "/result")])
def test_wrong_secret_cannot_read_commands_or_submit_results(bridge, method, path):
    assert call(bridge, method, path, token="wrong")[0] == 403


def test_non_ascii_bad_secret_is_rejected_cleanly(bridge):
    assert call(bridge, "GET", "/command", token="caf\u00e9")[0] == 403


def test_one_bridge_secret_cannot_control_another(bridge):
    with BrowserBridge() as other:
        token = bridge.extension_config()["token"]
        assert call(other, "GET", "/command", token=token)[0] == 403


def test_shutdown_interrupts_an_incomplete_authenticated_upload(bridge):
    config = bridge.extension_config()
    connection = http.client.HTTPConnection("127.0.0.1", int(config["endpoint"].rsplit(":", 1)[1]), timeout=2)
    try:
        connection.putrequest("POST", "/result")
        connection.putheader("Authorization", "Bearer " + config["token"])
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", "100")
        connection.endheaders()
        connection.send(b"{")
        started = time.monotonic()
        bridge.close()
        assert time.monotonic() - started < 1
    finally:
        connection.close()


def test_concurrent_close_waits_for_cleanup_to_finish(bridge, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    second_done = threading.Event()
    shutdown = bridge._server.shutdown

    def delayed_shutdown():
        entered.set()
        assert release.wait(2)
        shutdown()

    monkeypatch.setattr(bridge._server, "shutdown", delayed_shutdown)
    first = threading.Thread(target=bridge.close)
    second = threading.Thread(target=lambda: (bridge.close(), second_done.set()))
    first.start()
    try:
        assert entered.wait(1)
        second.start()
        assert not second_done.wait(0.05)
    finally:
        release.set()
        first.join(2)
        if second.ident is not None:
            second.join(2)
    assert second_done.is_set()


def test_hostname_rebinding_and_unknown_routes_rejected(bridge):
    assert call(bridge, "GET", "/command", host="evil.example")[0] == 403
    assert call(bridge, "GET", "/command?token=not-accepted")[0] == 404


def test_command_can_only_be_claimed_once(bridge):
    cancel = threading.Event()
    worker, outcomes = start_request(bridge, cancel=cancel)
    claim(bridge)
    assert call(bridge, "GET", "/command")[0] == 204
    cancel.set()
    worker.join(2)
    assert isinstance(outcomes[0], BrowserAccessCancelled)


def test_timeout_and_late_reply_cannot_overwrite_next_request(bridge):
    worker, outcomes = start_request(bridge, timeout=0.1)
    old = claim(bridge)
    worker.join(2)
    assert isinstance(outcomes[0], BrowserRequestTimeout)
    worker, outcomes = start_request(bridge)
    new = claim(bridge)
    assert old["id"] != new["id"]
    assert call(bridge, "POST", "/result", {"id": old["id"], "result": {"bad": True}})[0] == 409
    assert call(bridge, "POST", "/result", {"id": new["id"], "result": {"ok": True}})[0] == 204
    worker.join(2)
    assert outcomes == [{"ok": True}]


def test_cancelled_waiting_request_does_not_replace_active_command(bridge):
    owner_cancel = threading.Event()
    owner, owner_outcomes = start_request(bridge, cancel=owner_cancel)
    command = claim(bridge)
    cancelled = threading.Event()
    waiter, waiter_outcomes = start_request(bridge, cancel=cancelled)
    cancelled.set()
    waiter.join(2)
    assert isinstance(waiter_outcomes[0], BrowserAccessCancelled)
    assert call(bridge, "POST", "/result", {"id": command["id"], "result": {"owner": True}})[0] == 204
    owner.join(2)
    assert owner_outcomes == [{"owner": True}]


def test_queued_requests_keep_independent_payloads_and_results(bridge):
    first, first_outcomes = start_request(bridge, payload={"operation": "first"})
    first_command = claim(bridge)
    second, second_outcomes = start_request(bridge, payload={"operation": "second"})
    assert call(bridge, "GET", "/command")[0] == 204
    call(bridge, "POST", "/result", {"id": first_command["id"], "result": {"first": True}})
    first.join(2)
    second_command = claim(bridge)
    assert second_command["payload"] == {"operation": "second"}
    assert first_command["id"] != second_command["id"]
    call(bridge, "POST", "/result", {"id": second_command["id"], "result": {"second": True}})
    second.join(2)
    assert first_outcomes == [{"first": True}]
    assert second_outcomes == [{"second": True}]


@pytest.mark.parametrize("body,status", [
    (b"x" * 1025, 413), (b"not-json", 400), (b"[]", 400),
    (b'{"id":"test","result":NaN}', 400),
])
def test_invalid_or_oversize_reply_rejected(bridge, body, status):
    assert call(bridge, "POST", "/result", body, raw=True)[0] == status


def test_shutdown_wakes_waiter_and_removes_pending_result(bridge):
    worker, outcomes = start_request(bridge)
    claim(bridge)
    bridge.close()
    worker.join(2)
    assert not worker.is_alive()
    assert isinstance(outcomes[0], BrowserBridgeClosed)
    bridge.close()


def test_cancel_before_request_publishes_nothing(bridge):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(BrowserAccessCancelled):
        bridge.request({"operation": "fetch"}, cancel=cancel)
    assert call(bridge, "GET", "/command")[0] == 204


def test_pending_payload_is_snapshotted_and_oversize_payload_rejected(bridge):
    with pytest.raises(ValueError):
        bridge.request({"body": "x" * 2048})
    payload = {"operation": "fetch", "options": {"mode": "original"}}
    worker, outcomes = start_request(bridge, payload=payload)
    command = claim(bridge)
    payload["options"]["mode"] = "changed"
    assert command["payload"]["options"]["mode"] == "original"
    call(bridge, "POST", "/result", {"id": command["id"], "result": {}})
    worker.join(2)
    assert outcomes == [{}]


def test_configuration_is_separate_per_bridge_and_redacted(bridge):
    with BrowserBridge() as other:
        assert bridge.extension_config()["token"] != other.extension_config()["token"]
        assert bridge.extension_config()["endpoint"] != other.extension_config()["endpoint"]
    config = bridge.extension_config()
    assert config["token"] not in repr(bridge)
    assert config["endpoint"] not in repr(bridge)


def test_closed_bridge_cannot_be_restarted_or_used(bridge):
    bridge.close()
    with pytest.raises(BrowserBridgeClosed):
        bridge.request({"operation": "fetch"})
    with pytest.raises(BrowserBridgeClosed):
        bridge.__enter__()


@pytest.mark.parametrize("timeout", [-1, 0, True, float("inf"), float("nan")])
def test_bad_deadline_rejected(bridge, timeout):
    with pytest.raises(ValueError):
        bridge.request({"operation": "fetch"}, timeout=timeout)


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_slow_trickle_cannot_monopolize_the_http_worker(bridge, monkeypatch, phase):
    monkeypatch.setattr(bridge_module, "_CONNECTION_SECONDS", 0.12, raising=False)
    config = bridge.extension_config()
    port = int(config["endpoint"].rsplit(":", 1)[1])
    closed = False
    with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
        connection.settimeout(0.01)
        if phase == "headers":
            connection.sendall(b"GET /command HTTP/1.1\r\n")
        else:
            headers = (f"POST /result HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                       f"Authorization: Bearer {config['token']}\r\nContent-Type: application/json\r\n"
                       "Content-Length: 1000\r\n\r\n")
            connection.sendall(headers.encode("ascii"))
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            try:
                connection.sendall(b" ")
                if connection.recv(1) == b"":
                    closed = True
                    break
            except TimeoutError:
                pass
            except (BrokenPipeError, ConnectionResetError):
                closed = True
                break
            time.sleep(0.02)
    assert closed, "a trickling connection exceeded its total lifetime"
    assert call(bridge, "GET", "/command")[0] == 204


def test_cancellation_between_wait_check_and_reply_observation_wins(bridge, monkeypatch):
    cancel = threading.Event()
    check = bridge._check_wait

    def cancel_before_observing(deadline, cancellation):
        interval = check(deadline, cancellation)
        if bridge._pending is not None and bridge._pending.reply is not None:
            cancel.set()
        return interval

    monkeypatch.setattr(bridge, "_check_wait", cancel_before_observing)
    worker, outcomes = start_request(bridge, cancel=cancel)
    command = claim(bridge)
    call(bridge, "POST", "/result", {"id": command["id"], "result": {"success": True}})
    worker.join(2)
    assert isinstance(outcomes[0], BrowserAccessCancelled)
