"""Transport diagnostics expose decisions, never browser identity or secrets."""

import logging
import time

from media_cli.services.browser_access.clearance import Clearance, ClearanceStore
from media_cli.services.browser_access.policy import BrowserPolicy

POLICY = BrowserPolicy("test", ["https://site.test"])
URL = "https://site.test/api?token=private-query"


def snapshot():
    return {"user_agent": "private-browser-UA", "partition_key": None,
            "cookies": [{"name": "cf_clearance", "value": "private-cookie-value", "domain": "site.test",
                         "hostOnly": True, "path": "/", "secure": True, "session": False,
                         "expirationDate": time.time() + 600}]}


def test_diagnostic_fields_cannot_accept_arbitrary_secret_text(caplog):
    from media_cli.services.browser_access.diagnostics import record
    caplog.set_level(logging.DEBUG)
    record(POLICY, URL, "replay_http", status=403)
    record(POLICY, URL, "private-cookie-value", reason="private-browser-UA")
    record(POLICY, URL, "cookie_export", reason="private-cookie-value")
    record(POLICY, "https://user:password@evil.test/", "replay_http", status=200)
    assert "origin=https://site.test" in caplog.text
    assert "event=replay_http" in caplog.text and "status=403" in caplog.text
    for value in ("private-query", "private-cookie-value", "private-browser-UA", "password", "/api"):
        assert value not in caplog.text


def test_cache_diagnostics_distinguish_missing_hit_and_invalid_without_values(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    store = ClearanceStore(tmp_path / "browser")
    assert store.load(POLICY, URL) is None
    assert "event=cache_miss" in caplog.text and "reason=missing" in caplog.text
    clearance = Clearance.from_export(POLICY, URL, snapshot())
    store.save(POLICY, URL, clearance)
    assert "event=cache_saved" in caplog.text
    assert store.load(POLICY, URL) is not None
    assert "event=cache_hit" in caplog.text
    path = next(store.root.rglob("*.json"))
    path.write_text("{bad")
    assert store.load(POLICY, URL) is None
    assert "reason=invalid_or_expired" in caplog.text
    for value in ("private-cookie-value", "private-query", "private-browser-UA"):
        assert value not in caplog.text
