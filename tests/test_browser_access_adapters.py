"""Shared transport integration, provider namespaces and CLI operation scope."""

import threading
import urllib.error
from types import SimpleNamespace

import pytest

from media_cli.services.browser_access import integration
from media_cli.services.browser_access.access import AccessResponse
from media_cli.services.browser_access.operation import browser_operation, current_operation
from media_cli.services.browser_access.profiles import BrowserAccessCancelled
from media_cli.services.browser_access.verification import VerificationState

CHALLENGE = '<title>Just a moment...</title><script src="/cdn-cgi/challenge-platform/a"></script>'


@pytest.fixture
def transport(tmp_path, monkeypatch):
    integration.close_browser_sessions()
    monkeypatch.setattr(integration, "_shutdown", threading.Event())
    calls = []
    root = tmp_path / "state" / "browser"
    monkeypatch.setattr(integration, "_profile_root", lambda: root)
    monkeypatch.setattr(integration, "linux_desktop", lambda: True)
    for key in ("ANYPLAY_BROWSER_COOKIES", "ANYPLAY_BROWSER_ACCESS"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(integration, "normal_response", lambda url, **kw: AccessResponse(url, 403, CHALLENGE))

    class Service:
        def __init__(self, root, **kwargs):
            self.root = root
        def get(self, policy, url, normal, **kwargs):
            assert normal(url).status == 403
            calls.append((self.root, policy, url, kwargs))
            if kwargs.get("on_state"):
                kwargs["on_state"](VerificationState.INTERACTION)
            return AccessResponse(url, 200, "recovered")
        def close(self):
            pass
    monkeypatch.setattr(integration, "BrowserAccess", Service)
    yield SimpleNamespace(calls=calls, root=root)
    integration.close_browser_sessions()


def test_normal_fast_path_creates_no_profile(transport, monkeypatch):
    monkeypatch.setattr(integration, "normal_response", lambda url, **kw: AccessResponse(url, 200, "normal"))
    assert integration.fetch_text("animexin", ["https://site.test"], "https://site.test/") == "normal"
    assert transport.calls == []
    assert not transport.root.parent.exists()


def save_clearance(root):
    import time

    from media_cli.services.browser_access.clearance import Clearance, ClearanceStore
    from media_cli.services.browser_access.policy import BrowserPolicy
    root.parent.mkdir(parents=True, exist_ok=True)
    policy = BrowserPolicy("animexin", ["https://site.test"])
    reply = {"user_agent": "Test browser", "partition_key": None,
             "cookies": [{"name": "cf_clearance", "value": "test-secret", "domain": "site.test",
                          "hostOnly": True, "path": "/", "secure": True, "session": False,
                          "expirationDate": time.time() + 600}]}
    clearance = Clearance.from_export(policy, "https://site.test/", reply)
    assert clearance is not None
    ClearanceStore(root).save(policy, "https://site.test/", clearance)


def test_saved_clearance_http_precedes_browser_creation(transport, monkeypatch):
    save_clearance(transport.root)
    monkeypatch.setattr(integration, "normal_response", lambda *a, **k: pytest.fail("cookie-free request"))
    replay_calls = []
    def replay(url, clearance, **kwargs):
        replay_calls.append((clearance.user_agent, kwargs))
        return AccessResponse(url, 200, "saved-http")
    monkeypatch.setattr(integration, "request_with_clearance", replay)
    assert integration.fetch_text("animexin", ["https://site.test"], "https://site.test/") == "saved-http"
    assert transport.calls == []
    assert replay_calls[0][0] == "Test browser"
    assert integration._services == {}


def test_rejected_saved_clearance_uses_browser_fallback(transport, monkeypatch):
    save_clearance(transport.root)
    monkeypatch.setattr(integration, "request_with_clearance", lambda url, clearance, **kw:
                        AccessResponse(url, 403, CHALLENGE))
    monkeypatch.setattr(integration, "normal_response", lambda *a, **k: pytest.fail("redundant request"))
    assert integration.fetch_text("animexin", ["https://site.test"], "https://site.test/") == "recovered"
    assert len(transport.calls) == 1


def test_saved_clearance_retries_connection_before_browser_or_invalidation(transport, monkeypatch):
    from media_cli.services.browser_access.clearance import ClearanceStore
    save_clearance(transport.root)
    clients = []
    def replay(url, clearance, **kwargs):
        clients.append(kwargs["session"])
        return AccessResponse(url, 403, CHALLENGE) if len(clients) == 1 else AccessResponse(url, 200, "retry-http")
    monkeypatch.setattr(integration, "request_with_clearance", replay)
    monkeypatch.setattr(ClearanceStore, "invalidate", lambda *a: pytest.fail("premature invalidation"))
    assert integration.fetch_text("animexin", ["https://site.test"], "https://site.test/") == "retry-http"
    assert integration.fetch_text("animexin", ["https://site.test"], "https://site.test/") == "retry-http"
    assert clients[0] is not clients[1] and clients[1] is clients[2]
    assert not transport.calls


def test_http_only_clients_are_closed_at_cli_cleanup(transport, monkeypatch):
    save_clearance(transport.root)
    seen = []
    def replay(url, clearance, **kwargs):
        seen.append(kwargs["session"])
        return AccessResponse(url, 200, "http")
    monkeypatch.setattr(integration, "request_with_clearance", replay)
    integration.fetch_text("animexin", ["https://site.test"], "https://site.test/")
    assert not integration._services and integration._http_pools
    integration.close_browser_sessions(shutdown=True)
    assert not integration._http_pools and seen[0]._closed


def test_rotated_saved_cookie_is_kept_without_creating_browser(transport, monkeypatch):
    from media_cli.services.browser_access.clearance import Clearance, ClearanceStore
    from media_cli.services.browser_access.policy import BrowserPolicy
    save_clearance(transport.root)
    policy = BrowserPolicy("animexin", ["https://site.test"])
    original = ClearanceStore(transport.root).load(policy, "https://site.test/")
    payload = {"user_agent":original.user_agent, "cookies":list(original.cookies), "partition_key":None}
    payload["cookies"][0]["value"] = "rotated-secret"
    rotated = Clearance.from_export(policy, "https://site.test/", payload)
    monkeypatch.setattr(integration, "request_with_clearance", lambda url, clearance, **kw:
                        AccessResponse(url, 200, "http", clearance=rotated))
    integration.fetch_text("animexin", ["https://site.test"], "https://site.test/")
    assert ClearanceStore(transport.root).load(policy, "https://site.test/").cookies[0]["value"] == "rotated-secret"
    assert not transport.calls


def test_private_mode_does_not_read_saved_clearance(transport, monkeypatch):
    monkeypatch.setattr(integration.ClearanceStore, "load", lambda *a: pytest.fail("saved cookie read"))
    with browser_operation(incognito=True):
        assert integration.fetch_text("animexin", ["https://site.test"], "https://site.test/") == "recovered"


def test_disabled_access_does_not_read_saved_clearance(transport, monkeypatch):
    monkeypatch.setenv("ANYPLAY_BROWSER_COOKIES", "off")
    monkeypatch.setattr(integration.ClearanceStore, "load", lambda *a: pytest.fail("saved cookie read"))
    monkeypatch.setattr(integration, "normal_response", lambda url, **kw: AccessResponse(url, 200, "normal"))
    assert integration.fetch_text("animexin", ["https://site.test"], "https://site.test/") == "normal"


def test_challenge_uses_same_provider_policy_and_operation(transport):
    cancel, messages = threading.Event(), []
    with browser_operation(cancel=cancel, status=messages.append, incognito=False):
        assert integration.fetch_text("animexin", ["https://site.test"], "https://site.test/page") == "recovered"
    _, policy, _, options = transport.calls[0]
    assert policy.provider_id == "animexin"
    assert policy.origins == {"https://site.test"}
    assert options["cancel"] is cancel
    assert "popup" in messages[0]


def test_incognito_does_not_create_or_read_saved_root(transport):
    with browser_operation(incognito=True):
        integration.fetch_text("animexin", ["https://site.test"], "https://site.test/")
    temporary_root, _, _, options = transport.calls[0]
    assert options["incognito"] is True
    assert temporary_root != transport.root
    assert temporary_root.parent.exists()
    integration.close_browser_sessions()
    assert not temporary_root.exists()
    assert not transport.root.parent.exists()


def test_incognito_requests_share_one_private_root_until_cli_cleanup(transport):
    with browser_operation(incognito=True):
        integration.fetch_text("animepahe", ["https://site.test"], "https://site.test/search")
        integration.fetch_text("animepahe", ["https://site.test"], "https://site.test/title")
    assert transport.calls[0][0] == transport.calls[1][0]
    assert not transport.root.parent.exists()


def test_shutdown_prevents_background_work_reopening_browser(transport, monkeypatch):
    integration.close_browser_sessions(shutdown=True)
    monkeypatch.setattr(integration, "normal_response", lambda *a, **k: pytest.fail("request after CLI shutdown"))
    with pytest.raises(BrowserAccessCancelled):
        integration.fetch_text("animepahe", ["https://site.test"], "https://site.test/")


def test_disabled_browser_preserves_http_error_without_state(transport, monkeypatch):
    monkeypatch.setenv("ANYPLAY_BROWSER_ACCESS", "off")
    with pytest.raises(urllib.error.HTTPError) as error:
        integration.fetch_text("animexin", ["https://site.test"], "https://site.test/")
    assert error.value.code == 403
    assert transport.calls == []


def test_external_host_cannot_be_auto_approved(transport):
    with pytest.raises(ValueError):
        integration.fetch_text("animexin", ["https://site.test"], "https://evil.test/")
    assert transport.calls == []


def test_pre_cancel_sends_no_request(transport, monkeypatch):
    monkeypatch.setattr(integration, "normal_response", lambda *a, **k: pytest.fail("request sent"))
    cancel = threading.Event()
    cancel.set()
    with browser_operation(cancel=cancel), pytest.raises(BrowserAccessCancelled):
        integration.fetch_text("animexin", ["https://site.test"], "https://site.test/")


def test_operation_scope_restores_and_combines_cancellation():
    before = current_operation()
    outer, inner = threading.Event(), threading.Event()
    with browser_operation(cancel=outer, incognito=True):
        with browser_operation(cancel=inner):
            assert current_operation().incognito is True
            outer.set()
            assert current_operation().cancel.is_set()
        assert current_operation().cancel is outer
    assert current_operation() is before


def test_executor_propagates_and_resets_operation_context():
    from media_cli.core.workers import DaemonExecutor
    event = threading.Event()
    with DaemonExecutor(max_workers=1) as executor:
        with browser_operation(cancel=event, incognito=True):
            observed = executor.submit(current_operation).result(timeout=2)
        assert observed.cancel is event
        assert observed.incognito is True
        assert executor.submit(current_operation).result(timeout=2).cancel is None


def test_animepahe_desktop_does_not_inspect_personal_firefox(transport, monkeypatch):
    from media_cli.providers import animepahe
    monkeypatch.setattr(animepahe, "_find_browser_cookies", lambda *a: pytest.fail("personal cookies accessed"))
    monkeypatch.setattr(animepahe, "_detect_firefox_user_agent", lambda: pytest.fail("Firefox inspected"))
    provider = animepahe.AnimePaheProvider()
    assert provider._fetch_page("https://animepahe.pw/api") == "recovered"
    assert transport.calls[0][1].provider_id == "animepahe"


def test_animexin_default_html_uses_shared_service_but_injected_fetch_is_preserved(transport):
    from media_cli.providers.animexin import AnimeXinProvider
    assert AnimeXinProvider()._fetch("https://animexin.dev/title/") == "recovered"
    assert transport.calls[0][1].provider_id == "animexin"
    def custom(url):
        return "custom"
    assert AnimeXinProvider(fetch=custom)._fetch is custom


def test_animexin_cancel_does_not_fall_back_to_another_request(monkeypatch):
    from media_cli.providers.animexin import AnimeXinProvider
    def cancel(_):
        raise BrowserAccessCancelled("closed")
    provider = AnimeXinProvider(ajax_fetch=cancel, fetch=lambda _: pytest.fail("request after cancel"))
    with pytest.raises(BrowserAccessCancelled):
        provider.search("test")


def test_kwik_uses_animepahe_namespace_without_personal_cookies(transport, monkeypatch):
    from media_cli.extractors.kwik import KwikExtractor
    from media_cli.providers import animepahe
    monkeypatch.setattr(animepahe, "_find_browser_cookies", lambda *a: pytest.fail("personal cookies accessed"))
    monkeypatch.setattr(animepahe, "_detect_firefox_user_agent", lambda: pytest.fail("Firefox inspected"))
    assert KwikExtractor().extract("https://kwik.si/e/test") == []
    assert transport.calls[0][1].provider_id == "animepahe"
    assert "https://kwik.si" in transport.calls[0][1].origins


def test_preparation_propagates_status_and_abandonment():
    from media_cli.ui.transition import PreparationCancelled, run_preparation
    started, released, cancelled = threading.Event(), threading.Event(), threading.Event()
    observed = []
    def work():
        operation = current_operation()
        observed.append(operation)
        operation.status("Checking verification")
        started.set()
        while not operation.cancel.is_set():
            released.wait(0.01)
        cancelled.set()
    with pytest.raises(PreparationCancelled):
        run_preparation(work, tick=lambda: None, cancelled=started.is_set, status_cb=observed.append)
    assert cancelled.wait(2)
    assert "Checking verification" in observed


def test_background_search_carries_operation_and_discards_cancelled_results(monkeypatch):
    from media_cli.app import search_coordinator as search
    from media_cli.ui.footer import FooterStatus
    entered, finished, release = threading.Event(), threading.Event(), threading.Event()
    observed = []
    class Status:
        def read(self, active, *, message=None):
            return FooterStatus(message or "Searching") if active else None
    def fetch(*args, **kwargs):
        operation = current_operation()
        observed.append(operation)
        entered.set()
        if operation.status:
            operation.status("Complete verification in the popup.")
        while operation.cancel is not None and not operation.cancel.is_set() and not release.is_set():
            release.wait(0.01)
        finished.set()
        return [{"_id": "late", "name": "Late result"}]
    monkeypatch.setattr(search, "load_config", lambda: {})
    monkeypatch.setattr(search, "_provider_search_cache", {})
    monkeypatch.setattr(search, "_make_background_status", lambda *a, **k: Status())
    monkeypatch.setattr(search, "search_anime", fetch)
    live, results, loading, error = search.make_provider_oneshot_search("test", "sub", "animexin")
    try:
        assert entered.wait(2)
        assert observed[0].cancel is not None
        assert "popup" in loading().message
        live.cancel()
        assert finished.wait(2)
        assert results() == []
        assert not search._provider_search_cache
    finally:
        release.set()


@pytest.mark.parametrize("fail", [False, True])
def test_search_view_always_cancels_source_on_exit(monkeypatch, fail):
    from media_cli.app import search_results_controller as controller
    cancelled = threading.Event()
    def live():
        pass
    live.cancel = cancelled.set
    monkeypatch.setattr(controller, "SearchResultsController", lambda **kw: SimpleNamespace(
        session=SimpleNamespace(current=SimpleNamespace(id="search:test")), updates=lambda: ()))
    def run(*args, **kwargs):
        if fail:
            raise KeyboardInterrupt
        return controller.SearchResultsResult("back")
    monkeypatch.setattr(controller, "run_view_session", run)
    if fail:
        with pytest.raises(KeyboardInterrupt):
            controller.run_search_results(None, None, live_fn=live, query="test", ttype="sub")
    else:
        controller.run_search_results(None, None, live_fn=live, query="test", ttype="sub")
    assert cancelled.is_set()


def test_catalog_browser_jobs_have_separate_operation_keys_and_verification_budget(transport):
    first, second = threading.Event(), threading.Event()
    with browser_operation(cancel=first):
        wait, first_key = integration.browser_task_options("animexin")
        assert wait >= 110
    with browser_operation(cancel=second):
        _, second_key = integration.browser_task_options("animexin")
        assert integration.browser_task_options("anikoto") == (30, "")
    assert first_key and second_key and first_key != second_key


def test_catalog_jobs_separate_private_and_saved_modes_without_cancel_token(transport):
    with browser_operation(incognito=False):
        _, saved = integration.browser_task_options("animexin")
    with browser_operation(incognito=True):
        _, private = integration.browser_task_options("animexin")
    assert saved != private


def test_animepahe_keeps_domain_fallback_for_ordinary_http_errors(transport, monkeypatch):
    from media_cli.providers.animepahe import AnimePaheProvider
    calls = []
    def normal(url, **kwargs):
        calls.append(url)
        if "animepahe.pw" in url:
            return AccessResponse(url, 500, "Temporary server error")
        return AccessResponse(url, 200, '{"data":[{"session":"one","title":"Example","episodes":12}]}')
    monkeypatch.setattr(integration, "normal_response", normal)
    provider = AnimePaheProvider()
    assert provider.search("test")
    assert len(calls) == 2
    assert calls[1].startswith("https://animepahe.org/")


def test_animepahe_does_not_retry_other_domains_after_verification_timeout(transport, monkeypatch):
    from media_cli.providers.animepahe import AnimePaheProvider
    from media_cli.services.browser_access.bridge import BrowserRequestTimeout
    calls = []
    def timeout(*args, **kwargs):
        calls.append(args[2])
        raise BrowserRequestTimeout("verification expired")
    monkeypatch.setattr(integration, "fetch_text", timeout)
    with pytest.raises(BrowserRequestTimeout):
        AnimePaheProvider().search("test")
    assert len(calls) == 1


def test_cancellation_during_enrichment_does_not_publish_or_cache(monkeypatch):
    import media_cli.app_core as app_core
    from media_cli.app import search_coordinator as search
    pending = []
    class Thread:
        def __init__(self, target, **kwargs):
            self.target = target
        def start(self):
            pending.append(self)
        def join(self):
            self.target()
    monkeypatch.setattr(search.threading, "Thread", Thread)
    monkeypatch.setattr(search, "load_config", lambda: {})
    monkeypatch.setattr(search, "_provider_search_cache", {})
    monkeypatch.setattr(search, "search_anime", lambda *a, **k: [{"_id": "one", "name": "Example"}])
    monkeypatch.setattr(app_core, "batch_prepare_shows_display_state", lambda *a: None)
    live, results, _, _ = search.make_provider_oneshot_search("test", "sub", "animexin")
    def enrich(shows, other):
        live.cancel()
        return shows, []
    monkeypatch.setattr("media_cli.core.enrichment.enrich_provider_results_fast", enrich)
    pending[0].target()
    assert results() == []
    assert not search._provider_search_cache
