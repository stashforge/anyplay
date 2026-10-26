"""Protected covers can reuse authorized clearance but never start a browser."""

import threading
import time
from types import SimpleNamespace

import pytest

from media_cli.services.browser_access import integration
from media_cli.services.browser_access.access import AccessResponse
from media_cli.services.browser_access.clearance import Clearance, ClearanceStore
from media_cli.services.browser_access.policy import BrowserPolicy

SOURCE = "https://animepahe.pw"
URL = "https://i.animepahe.pw/uploads/posters/test.webp"
IMAGE = b"RIFFxxxxWEBPbinary\xff\xfe"


@pytest.fixture
def image_access(tmp_path, monkeypatch):
    integration.close_browser_sessions()
    monkeypatch.setattr(integration, "_shutdown", threading.Event())
    root = tmp_path / "browser"
    monkeypatch.setattr(integration, "_profile_root", lambda: root)
    monkeypatch.setattr(integration, "linux_desktop", lambda: True)
    monkeypatch.setattr(integration, "BrowserAccess", lambda *a, **kw: pytest.fail("Image opened a browser"))
    monkeypatch.setattr(integration, "current_operation", lambda: SimpleNamespace(incognito=False, cancel=None))
    for key in ("ANYPLAY_BROWSER_ACCESS", "ANYPLAY_BROWSER_COOKIES"):
        monkeypatch.delenv(key, raising=False)
    yield root
    integration.close_browser_sessions()


def save(root, *, host_only=False):
    root.parent.mkdir(parents=True, exist_ok=True)
    item = Clearance.from_export(BrowserPolicy("animepahe", [SOURCE]), SOURCE + "/", {
        "user_agent": "Synthetic Chrome", "partition_key": None,
        "cookies": [{"name": "cf_clearance", "value": "synthetic-secret",
                     "domain": "animepahe.pw" if host_only else ".animepahe.pw",
                     "path": "/", "secure": True, "hostOnly": host_only,
                     "expirationDate": time.time() + 600}],
    })
    ClearanceStore(root).save(BrowserPolicy("animepahe", [SOURCE]), SOURCE + "/", item)


def test_image_uses_existing_domain_scoped_clearance_and_shared_retry(image_access, monkeypatch):
    save(image_access)
    seen = []
    def request(url, item, **kwargs):
        seen.append(kwargs["session"])
        assert item.origin == "https://i.animepahe.pw"
        assert item.cookies[0]["domain"] == ".animepahe.pw"
        assert kwargs["binary"] is True and kwargs["max_bytes"] == 128
        if len(seen) == 1:
            return AccessResponse(url, 403, "", {"cf-mitigated": "challenge"})
        return AccessResponse(url, 200, "", data=IMAGE)
    monkeypatch.setattr(integration, "request_with_clearance", request)
    assert integration.fetch_cached_image(URL, max_bytes=128) == IMAGE
    assert len(seen) == 2 and seen[0] is not seen[1]
    assert not integration._services


@pytest.mark.parametrize("kind", ["missing", "host-only", "foreign-host", "foreign-path", "private", "disabled"])
def test_image_never_broadens_cookie_scope_or_starts_verification(image_access, monkeypatch, kind):
    if kind != "missing":
        save(image_access, host_only=kind == "host-only")
    url = URL
    if kind == "foreign-host":
        url = "https://evil.test/uploads/posters/test.webp"
    if kind == "foreign-path":
        url = "https://i.animepahe.pw/account"
    if kind == "private":
        monkeypatch.setattr(integration, "current_operation", lambda: SimpleNamespace(incognito=True, cancel=None))
        monkeypatch.setattr(ClearanceStore, "load", lambda *a: pytest.fail("private read saved credentials"))
    if kind == "disabled":
        monkeypatch.setenv("ANYPLAY_BROWSER_COOKIES", "off")
    monkeypatch.setattr(integration, "request_with_clearance", lambda *a, **kw: pytest.fail("Unauthorized image request"))
    assert integration.fetch_cached_image(url) is None
    assert not integration._http_pools and not integration._services
    if kind == "missing":
        assert not image_access.exists()


def test_image_rejection_does_not_invalidate_main_site_clearance(image_access, monkeypatch):
    save(image_access)
    monkeypatch.setattr(integration, "request_with_clearance", lambda url, *a, **kw:
                        AccessResponse(url, 403, "", {"cf-mitigated": "challenge"}))
    assert integration.fetch_cached_image(URL) is None
    assert ClearanceStore(image_access).load(BrowserPolicy("animepahe", [SOURCE]), SOURCE + "/") is not None


def test_success_html_is_not_returned_as_an_image(image_access, monkeypatch):
    save(image_access)
    monkeypatch.setattr(integration, "request_with_clearance", lambda url, *a, **kw:
                        AccessResponse(url, 200, "", data=b"<html>not a cover</html>"))
    assert integration.fetch_cached_image(URL) is None


def test_private_image_uses_only_its_existing_memory_snapshot(image_access, monkeypatch):
    from media_cli.services.browser_access.access import BrowserAccess
    save(image_access)
    item = ClearanceStore(image_access).load(BrowserPolicy("animepahe", [SOURCE]), SOURCE + "/")
    service = BrowserAccess(image_access, keep_sessions=True, reuse_clearance=True)
    service._clearances[("animepahe", True, SOURCE)] = item
    monkeypatch.setattr(integration, "_service", lambda *a, **kw: service)
    monkeypatch.setattr(integration, "current_operation", lambda: SimpleNamespace(incognito=True, cancel=None))
    monkeypatch.setattr(ClearanceStore, "load", lambda *a: pytest.fail("Private image read saved credentials"))
    monkeypatch.setattr(ClearanceStore, "save", lambda *a, **kw: pytest.fail("Private image saved credentials"))
    monkeypatch.setattr(integration, "request_with_clearance", lambda url, *a, **kw:
                        AccessResponse(url, 200, "", data=IMAGE))
    assert integration.fetch_cached_image(URL) == IMAGE
    assert list(integration._http_pools) == [(image_access, True)]
    service.close()


def test_cancelled_image_makes_no_request(image_access, monkeypatch):
    save(image_access)
    cancel = threading.Event()
    cancel.set()
    monkeypatch.setattr(integration, "current_operation", lambda: SimpleNamespace(incognito=False, cancel=cancel))
    monkeypatch.setattr(integration, "request_with_clearance", lambda *a, **kw: pytest.fail("Cancelled request sent"))
    assert integration.fetch_cached_image(URL) is None
    assert not integration._http_pools


@pytest.mark.parametrize("suffix", ["../account", "../../account", "./test.webp", "%2e%2e/account",
                                    "%252e%252e/account", "test%2f..%2faccount"])
def test_image_route_rejects_traversal_before_reading_clearance(image_access, monkeypatch, suffix):
    save(image_access)
    monkeypatch.setattr(ClearanceStore, "load", lambda *a: pytest.fail("Traversal read credentials"))
    monkeypatch.setattr(integration, "request_with_clearance", lambda *a, **kw: pytest.fail("Traversal requested"))
    assert integration.fetch_cached_image("https://i.animepahe.pw/uploads/posters/" + suffix) is None
    assert not integration._http_pools


def test_image_redirect_does_not_start_another_request(image_access, monkeypatch):
    save(image_access)
    calls = []
    def redirect(url, *a, **kw):
        calls.append(url)
        return AccessResponse(url, 302, "", {"Location": "https://evil.test/image.webp"}, data=IMAGE)
    monkeypatch.setattr(integration, "request_with_clearance", redirect)
    assert integration.fetch_cached_image(URL) is None
    assert calls == [URL]
