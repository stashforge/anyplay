import importlib
import importlib.util
import threading
import time
from contextlib import contextmanager

import pytest

from media_cli.services.browser_access import integration
from media_cli.services.browser_access.access import AccessResponse
from media_cli.services.browser_access.operation import browser_operation
from media_cli.services.browser_access.profiles import BrowserAccessCancelled

URL = "https://kwik.si/e/test"
CHALLENGE = '<title>Just a moment...</title><script src="/cdn-cgi/challenge-platform/a"></script>'


def site_module():
    name = "media_cli.services.android_webview.site_access"
    assert importlib.util.find_spec(name) is not None, "Termux needs an HTTP-first site adapter"
    return importlib.import_module(name)


class NativeClient:
    def __init__(self):
        self.profiles = []
        self.closed = False
        self.reveals = []

    @contextmanager
    def session(self, profile, **options):
        self.profiles.append(profile)
        try:
            yield "owned"
        finally:
            self.closed = True

    def reveal_verification(self, job_id, ticket, host, **options):
        self.reveals.append((job_id, ticket, host))

    def run_site_get(self, sid, url, deadline, cancel=None, on_interaction=None):
        assert sid == "owned" and deadline > time.monotonic()
        on_interaction("job", "a" * 64, "kwik.si")
        return {
            "status": "ok",
            "result": {
                "url": url,
                "status": 200,
                "body": "source='https://cdn.test/video.m3u8'",
                "headers": {},
                "user_agent": "Native WebView UA",
            },
        }


def test_http_success_never_opens_native_profile_even_when_private():
    module = site_module()
    client = NativeClient()
    response = module.fetch_site_response(
        URL,
        lambda url: AccessResponse(url, 200, "ordinary"),
        deadline=time.monotonic() + 2,
        incognito=True,
        client=client,
    )
    assert response.body == "ordinary" and not client.profiles


def test_only_recognized_challenge_uses_profile_and_preserves_native_ua():
    module = site_module()
    client, messages = NativeClient(), []
    response = module.fetch_site_response(
        URL,
        lambda url: AccessResponse(url, 403, CHALLENGE),
        deadline=time.monotonic() + 2,
        client=client,
        on_status=messages.append,
    )
    assert response.status == 200 and client.profiles == ["kwik"] and client.closed
    assert client.reveals == [("job", "a" * 64, "kwik.si")]
    assert len(messages) == 3 and "Checking" in messages[0] and "verification" in messages[1].lower()
    assert messages[-1] == "Site access ready. Loading..."
    assert module.site_user_agent(URL, "fallback") == "Native WebView UA"
    assert module.site_user_agent("https://kwik.cx/e/other", "fallback") == "fallback"
    module.fetch_site_response(
        URL, lambda url: AccessResponse(url, 200, "ordinary"), deadline=time.monotonic() + 2, client=client
    )
    assert module.site_user_agent(URL, "fallback") == "fallback"


@pytest.mark.parametrize("private,enabled", [(True, True), (False, False)])
def test_challenge_cannot_open_saved_profile_when_private_or_disabled(private, enabled):
    module = site_module()
    client = NativeClient()
    with pytest.raises(RuntimeError):
        module.fetch_site_response(
            URL,
            lambda url: AccessResponse(url, 403, CHALLENGE),
            deadline=time.monotonic() + 2,
            incognito=private,
            enabled=enabled,
            client=client,
        )
    assert not client.profiles


def test_ordinary_forbidden_is_not_a_browser_challenge():
    module = site_module()
    client = NativeClient()
    response = module.fetch_site_response(
        URL, lambda url: AccessResponse(url, 403, "Forbidden"), deadline=time.monotonic() + 2, client=client
    )
    assert response.status == 403 and not client.profiles


def test_cancel_after_http_never_opens_browser():
    module = site_module()
    cancel, client = threading.Event(), NativeClient()

    def normal(url):
        cancel.set()
        return AccessResponse(url, 403, CHALLENGE)

    with pytest.raises(BrowserAccessCancelled):
        module.fetch_site_response(URL, normal, deadline=time.monotonic() + 2, cancel=cancel, client=client)
    assert not client.profiles


def test_cancel_during_http_exception_translates_to_browser_cancellation():
    cancel, client = threading.Event(), NativeClient()

    def normal(url):
        cancel.set()
        raise OSError("interrupted transport")

    with pytest.raises(BrowserAccessCancelled):
        site_module().fetch_site_response(URL, normal, deadline=time.monotonic() + 2, cancel=cancel, client=client)
    assert not client.profiles


@pytest.mark.parametrize("terminal", [True, False])
def test_native_cancellation_translates_and_closes_owned_session(terminal):
    from media_cli.services.android_webview.client import ProbeError

    cancel, client = threading.Event(), NativeClient()

    def cancelled(*args, **kwargs):
        if terminal:
            return {"status": "cancelled"}
        cancel.set()
        raise ProbeError("interrupted native request")

    client.run_site_get = cancelled
    with pytest.raises(BrowserAccessCancelled):
        site_module().fetch_site_response(
            URL,
            lambda url: AccessResponse(url, 403, CHALLENGE),
            deadline=time.monotonic() + 2,
            cancel=cancel,
            client=client,
        )
    assert client.closed


def test_normal_response_default_keeps_curl_redirect_behavior(monkeypatch):
    from types import SimpleNamespace

    from curl_cffi import requests

    seen = []

    def get(url, **options):
        seen.append(options)
        return SimpleNamespace(url=url, status_code=200, text="ordinary", headers={})

    monkeypatch.setattr(requests, "get", get)
    assert integration.normal_response(URL).body == "ordinary"
    assert "allow_redirects" not in seen[0]


def test_normal_response_default_keeps_urllib_urlopen(monkeypatch):
    import io
    import urllib.response
    from email.message import Message

    from curl_cffi import requests

    monkeypatch.setattr(requests, "get", lambda *a, **kw: (_ for _ in ()).throw(OSError("synthetic")))
    seen = []

    def urlopen(request, **options):
        seen.append(request.full_url)
        return urllib.response.addinfourl(io.BytesIO(b"ordinary"), Message(), URL, 200)

    monkeypatch.setattr(integration.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(integration.urllib.request, "build_opener", lambda *a: pytest.fail("default changed"))
    assert integration.normal_response(URL).body == "ordinary"
    assert seen == [URL]


@pytest.fixture
def termux(monkeypatch):
    monkeypatch.setattr(integration, "is_termux", lambda: True, raising=False)
    monkeypatch.setattr(integration, "linux_desktop", lambda: False)
    monkeypatch.setattr(integration, "_shutdown", threading.Event())
    for key in ("ANYPLAY_BROWSER_ACCESS", "ANYPLAY_BROWSER_COOKIES"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(integration, "_service", lambda *a, **kw: pytest.fail("Linux browser used on Termux"))
    from media_cli.providers import animepahe

    monkeypatch.setattr(animepahe, "_find_browser_cookies", lambda *a: pytest.fail("Firefox cookies read"))
    monkeypatch.setattr(animepahe, "_detect_firefox_user_agent", lambda: pytest.fail("Firefox inspected"))
    monkeypatch.setattr(animepahe, "_resolve_browser_response", lambda *a, **kw: pytest.fail("Playwright used"))


def test_termux_animepahe_and_kwik_get_use_shared_site_adapter(termux, monkeypatch):
    module = site_module()
    client = NativeClient()
    monkeypatch.setattr(module, "ProbeClient", lambda: client)
    monkeypatch.setattr(integration, "normal_response", lambda url, **kw: AccessResponse(url, 403, CHALLENGE))
    from media_cli.extractors.kwik import KwikExtractor
    from media_cli.providers.animepahe import AnimePaheProvider

    assert "source=" in AnimePaheProvider()._fetch_page("https://animepahe.pw/api")
    rows = KwikExtractor().extract(URL)
    assert rows[0]["headers"]["User-Agent"] == "Native WebView UA"
    assert client.profiles == ["animepahe", "kwik"]
    with browser_operation(incognito=True):
        assert integration.browser_task_options("animepahe")[0] == 120


def test_termux_disabled_challenge_never_uses_native_or_legacy_browser(termux, monkeypatch):
    module = site_module()
    monkeypatch.setenv("ANYPLAY_BROWSER_ACCESS", "off")
    monkeypatch.setattr(module, "ProbeClient", lambda: pytest.fail("native profile opened"))
    monkeypatch.setattr(integration, "normal_response", lambda url, **kw: AccessResponse(url, 403, CHALLENGE))
    with pytest.raises(RuntimeError):
        integration.fetch_text("animepahe", ["https://animepahe.pw"], "https://animepahe.pw/")


def test_termux_http_does_not_follow_redirects(termux, monkeypatch):
    import urllib.error
    from types import SimpleNamespace

    from curl_cffi import requests

    seen = []

    def get(url, **options):
        seen.append(options)
        return SimpleNamespace(url=url, status_code=302, text="", headers={"Location": "https://evil.test/"})

    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(urllib.error.HTTPError) as failure:
        integration.fetch_text("animepahe", ["https://animepahe.pw"], "https://animepahe.pw/")
    assert failure.value.code == 302
    assert seen[0].get("allow_redirects") is False


def test_native_challenge_body_is_not_success():
    module = site_module()
    client = NativeClient()

    def still_challenged(sid, url, deadline, **options):
        return {
            "status": "ok",
            "result": {
                "url": url,
                "status": 200,
                "body": CHALLENGE,
                "headers": {},
                "user_agent": "Native UA",
            },
        }

    client.run_site_get = still_challenged
    with pytest.raises(RuntimeError, match="verification"):
        module.fetch_site_response(
            URL, lambda url: AccessResponse(url, 403, CHALLENGE), deadline=time.monotonic() + 2, client=client
        )
    assert client.closed


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_termux_urllib_redirect_preserves_error_without_following_or_native_fallback(termux, monkeypatch, status):
    import io
    import urllib.error
    import urllib.request
    import urllib.response
    from email.message import Message

    from curl_cffi import requests

    module = site_module()
    monkeypatch.setattr(module, "ProbeClient", lambda: pytest.fail("ordinary redirect opened a profile"))
    monkeypatch.setattr(requests, "get", lambda *a, **kw: (_ for _ in ()).throw(OSError("synthetic failure")))
    requested = []

    class Transport(urllib.request.HTTPSHandler):
        def https_open(self, request):
            requested.append(request.full_url)
            assert request.full_url == URL, "HTTP-first followed the redirect"
            headers = Message()
            headers["Location"] = "https://unapproved.invalid/private"
            headers["Content-Type"] = "text/plain; charset=utf-8"
            response = urllib.response.addinfourl(io.BytesIO(b"redirect body"), headers, URL, status)
            response.msg = "Redirect"
            return response

    build_opener = urllib.request.build_opener
    monkeypatch.setattr(
        integration.urllib.request, "build_opener", lambda *handlers: build_opener(*handlers, Transport())
    )
    with pytest.raises(urllib.error.HTTPError) as failure:
        integration.fetch_text("animepahe", ["https://kwik.si"], URL)
    assert requested == [URL]
    assert failure.value.code == status
    assert failure.value.headers["Location"] == "https://unapproved.invalid/private"
    assert failure.value.read() == b"redirect body"


@pytest.mark.parametrize("status", [403, 500])
def test_native_http_error_does_not_report_verification_completed(status):
    module = site_module()
    client, messages = NativeClient(), []

    def rejected(sid, url, deadline, **options):
        return {
            "status": "ok",
            "result": {
                "url": url,
                "status": status,
                "body": "Unavailable",
                "headers": {},
                "user_agent": "Native UA",
            },
        }

    client.run_site_get = rejected
    response = module.fetch_site_response(
        URL,
        lambda url: AccessResponse(url, 403, CHALLENGE),
        deadline=time.monotonic() + 2,
        client=client,
        on_status=messages.append,
    )
    assert response.status == status and client.closed
    assert messages == ["Checking site access..."]
    assert module.site_user_agent(URL, "fallback") == "fallback"
