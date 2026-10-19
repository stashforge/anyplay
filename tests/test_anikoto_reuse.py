import json
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock
from urllib.error import URLError
from urllib.parse import urlsplit

import pytest

from media_cli import providers
from media_cli.providers.anikoto import AnikotoProvider


@pytest.fixture(autouse=True)
def isolate_provider_sessions(monkeypatch):
    monkeypatch.setattr(providers, "_SESSION_PROVIDERS", OrderedDict(), raising=False)
    monkeypatch.setattr(providers, "disabled_providers_from_config", lambda: set())
    registry = providers._RegistryDict(providers.PROVIDER_REGISTRY)
    registry["anikoto"] = {"name": "Anikoto", "domains": ["https://megaplay.buzz"]}
    monkeypatch.setattr(providers, "PROVIDER_REGISTRY", registry)

    def no_network(*args, **kwargs):
        pytest.fail("Real network access is forbidden")

    monkeypatch.setattr("socket.socket.connect", no_network)


@pytest.fixture
def site(monkeypatch):
    class Site:
        media = {
            "id": 150672,
            "idMal": 52034,
            "title": {"romaji": "OSHI NO KO"},
            "episodes": 1,
            "description": "A description",
            "genres": ["Drama"],
        }
        paths = []
        catalog_error = None
        watch_failures = 0
        translated = False
        source_count = 0
        unexpected = []

        def request(self, req, timeout=None):
            path = urlsplit(req.full_url).path
            self.paths.append(path)
            final_url = req.full_url
            if path == "/filter":
                title = "Site translation" if self.translated else "OSHI NO KO"
                body = (
                    '<div class="inner"><div class="poster" data-tip="562">'
                    f'<a href="/watch/oshi-no-ko/ep-1">{title}</a></div></div>'
                )
            elif path == "/watch/oshi-no-ko/ep-1":
                if self.watch_failures:
                    self.watch_failures -= 1
                    raise URLError("watch page unavailable")
                title = "Site translation" if self.translated else "OSHI NO KO"
                body = f'<main data-id="562"><h1>{title}</h1></main>'
            elif path == "/ajax/episode/list/562":
                if self.catalog_error == "transport":
                    raise URLError("catalog unavailable")
                if self.catalog_error == "json":
                    body = "invalid json"
                elif self.catalog_error == "shape":
                    body = json.dumps({"result": None})
                elif self.catalog_error == "empty":
                    body = json.dumps({"result": "<div>No episodes</div>"})
                else:
                    mal = 999 if self.catalog_error == "mal" else 52034
                    server = ' data-ids="wrong-server"' if mal == 999 else ""
                    body = json.dumps({"result": f'<a data-num="7" data-mal="{mal}"{server}>7</a>'})
                if self.catalog_error == "redirect":
                    final_url = "https://other.example/catalog"
            elif path == "/stream/ani/150672/7/sub":
                body = '<div data-id="12345"></div>'
            elif path == "/stream/getSources":
                self.source_count += 1
                body = json.dumps({"sources": [{"file": f"https://cdn.example/{self.source_count}.mp4"}]})
            else:
                self.unexpected.append(req.full_url)
                raise AssertionError(f"Unexpected request: {req.full_url}")
            return Mock(read=lambda: body.encode(), geturl=lambda: final_url)

    fake = Site()
    monkeypatch.setattr("urllib.request.urlopen", fake.request)
    monkeypatch.setattr(
        "media_cli.core.anilist_fallback.search_anilist_with_fallback",
        lambda query: {"data": {"Page": {"media": [fake.media]}}},
    )

    def unexpected_lookup(*args, **kwargs):
        fake.unexpected.append("AniList lookup after search")
        raise AssertionError("Search metadata should already be available")

    monkeypatch.setattr("media_cli.services.anilist.fetch_one", unexpected_lookup)
    yield fake
    assert fake.unexpected == []


def test_registry_reuses_only_anikoto_for_same_callback_and_domains(site):
    callback = Mock()
    first = providers.get_provider("anikoto", callback)
    assert providers.get_provider("ANIKOTO", callback) is first
    assert providers.get_provider("anikoto", Mock()) is not first
    assert providers.get_provider("miruro", callback) is not providers.get_provider("miruro", callback)

    first.search("OSHI NO KO")
    assert first._get_anime_page("150672")[0] == "562"
    providers.PROVIDER_REGISTRY["anikoto"]["domains"][0] = "https://mirror.example"
    changed = providers.get_provider("anikoto", callback)
    assert changed is not first
    assert changed.base_url == "https://mirror.example"
    assert changed._anime_page_cache == {}
    assert changed._media_cache == {}


def test_unhashable_equal_callbacks_still_have_distinct_sessions():
    class Callback:
        __hash__ = None

        def __eq__(self, other):
            return True

        def __call__(self, *args, **kwargs):
            raise AssertionError("unused")

    callback = Callback()
    first = providers.get_provider("anikoto", callback)
    assert providers.get_provider("anikoto", callback) is first
    assert providers.get_provider("anikoto", Callback()) is not first


def test_registry_session_retention_is_bounded():
    callback = Mock()
    first = providers.get_provider("anikoto", callback)
    assert providers.get_provider("anikoto", callback) is first
    for _ in range(32):
        providers.get_provider("anikoto", Mock())
    assert providers.get_provider("anikoto", callback) is not first


@pytest.mark.parametrize("origin", ["search", "fetch"])
def test_auth_changes_do_not_reuse_personal_metadata(site, monkeypatch, origin):
    site.media = {
        **site.media,
        "mediaListEntry": {"id": 777, "status": "CURRENT", "progress": 8, "notes": "private"},
        "isFavourite": True,
    }
    monkeypatch.setattr("media_cli.services.anilist_auth.stored_anilist_token", lambda config: "old-token")
    callback = Mock()
    provider = providers.get_provider("anikoto", callback)
    if origin == "search":
        provider.search("title")
    else:
        monkeypatch.setattr("media_cli.services.anilist.fetch_one", lambda *args, **kwargs: site.media)
        provider.get_title("150672")
    assert provider._get_anime_page("150672")[0] == "562"

    monkeypatch.setattr("media_cli.services.anilist_auth.stored_anilist_token", lambda config: "new-token")
    reused = providers.get_provider("anikoto", callback)
    show = reused.get_title("150672")
    assert show["_anilist_progress"] == 0
    assert show["_anilist_list"] == ""
    assert "_anilist_entry_id" not in show
    assert "mediaListEntry" not in reused._media_cache["150672"]
    assert "isFavourite" not in reused._media_cache["150672"]
    assert reused._get_anime_page("150672")[0] == "562"
    assert site.paths.count("/filter") == 1


def test_search_metadata_and_identity_survive_catalog_and_play_calls(site):
    callback = Mock()
    providers.get_provider("anikoto", callback).search("OSHI NO KO")
    first = providers.get_provider("anikoto", callback).episode_catalog("150672")
    assert first["ids"] == ["7"]
    assert site.paths.count("/ajax/episode/list/562") == 1
    assert providers.get_provider("anikoto", callback).episode_catalog("150672")["ids"] == ["7"]
    assert site.paths.count("/ajax/episode/list/562") == 2

    for count in (1, 2):
        result = providers.get_provider("anikoto", callback).episode_sources("150672", "7")
        assert result["_provider_sources"][0]["streamUrl"] == f"https://cdn.example/{count}.mp4"
        assert site.paths.count("/ajax/episode/list/562") == 2 + count
    assert site.paths.count("/filter") == 1
    assert site.paths.count("/watch/oshi-no-ko/ep-1") == 1


def test_native_mal_probe_catalog_is_reused_only_within_catalog_call(site):
    site.translated = True
    site.media = {**site.media, "title": {"native": "\u672c\u5f53\u306e\u540d\u524d"}}
    provider = AnikotoProvider()
    provider.search("title")
    assert provider.episode_catalog("150672")["ids"] == ["7"]
    assert site.paths.count("/ajax/episode/list/562") == 1
    assert provider.episode_catalog("150672")["ids"] == ["7"]
    assert site.paths.count("/ajax/episode/list/562") == 2


@pytest.mark.parametrize("method", ["search", "get_title"])
@pytest.mark.parametrize(
    "fields, loaded",
    [
        ({"description": "text", "genres": ["Drama"]}, ("description", "genres")),
        ({"description": "", "genres": []}, ("description", "genres")),
        ({"description": None, "genres": None}, ("description", "genres")),
        ({"description": ""}, ("description",)),
        ({"genres": []}, ("genres",)),
        ({}, ()),
    ],
)
def test_metadata_marker_records_raw_key_presence(site, method, fields, loaded):
    site.media = {"id": 150672, "title": {"romaji": "OSHI NO KO"}, **fields}
    provider = AnikotoProvider()
    show = provider.search("title")[0]
    if method == "get_title":
        show = provider.get_title("150672")
    assert show.get("_metadata_fields_loaded", ()) == loaded
    if method == "search":
        assert show["_metadata_fields_loaded"] == loaded
    elif not loaded:
        assert "_metadata_fields_loaded" not in show


@pytest.mark.parametrize("operation", ["episode_catalog", "episode_sources"])
@pytest.mark.parametrize("failure", ["mal", "transport", "json", "shape", "redirect"])
def test_failed_catalog_invalidates_all_aliases_and_retries_identity(site, operation, failure):
    provider = AnikotoProvider()
    provider.search("title")
    provider._media_cache["alias"] = site.media
    assert provider._get_anime_page("alias")[0] == "562"
    site.catalog_error = failure
    if operation == "episode_catalog":
        assert provider.episode_catalog("150672")["ids"] == ["1"]
    else:
        result = provider.episode_sources("150672", "7")
        assert result["_provider_sources"][0]["streamUrl"] == "https://cdn.example/1.mp4"
    assert provider._anime_page_cache == {}

    site.catalog_error = None
    assert provider.episode_catalog("150672")["ids"] == ["7"]
    assert site.paths.count("/filter") == 2


def test_failed_watch_identity_is_not_cached_and_can_be_retried(site):
    callback = Mock()
    providers.get_provider("anikoto", callback).search("title")
    site.watch_failures = 1
    assert providers.get_provider("anikoto", callback).episode_catalog("150672")["ids"] == ["1"]
    assert providers.get_provider("anikoto", callback).episode_catalog("150672")["ids"] == ["7"]
    assert site.paths.count("/filter") == 2


def test_empty_catalog_without_mal_cannot_retain_a_stale_mapping(site):
    site.media = {key: value for key, value in site.media.items() if key != "idMal"}
    provider = AnikotoProvider()
    provider.search("title")
    assert provider.episode_catalog("150672")["ids"] == ["7"]
    site.catalog_error = "empty"
    assert provider.episode_catalog("150672")["ids"] == ["1"]
    assert provider._anime_page_cache == {}


@pytest.mark.parametrize("setting", ["anikoto_url", "domains", "_request_json"])
def test_mutated_instance_scope_discards_old_identity(site, setting):
    provider = AnikotoProvider()
    provider.search("title")
    assert provider._get_anime_page("150672")[0] == "562"
    new_value = {
        "anikoto_url": "https://new-watch.example",
        "domains": ["https://new-embed.example"],
        "_request_json": Mock(),
    }[setting]
    setattr(provider, setting, new_value)
    provider.search("title")
    assert provider._anime_page_cache == {}


@pytest.mark.parametrize("title_id", [" 150672 ", "alias", "https://anikototv.to/watch/oshi-no-ko/ep-1"])
def test_title_invalidation_clears_aliases_and_refetches_only_selected_title(site, monkeypatch, title_id):
    callback = Mock()
    provider = providers.get_provider("anikoto", callback)
    provider.search("title")
    provider._fetch_media("alias")
    provider._fetch_media("another alias")
    assert provider._get_anime_page("alias")[0] == "562"
    assert provider._get_anime_page("https://anikototv.to/watch/oshi-no-ko/ep-1")[0] == "562"
    unrelated = {"id": 222, "title": {"romaji": "Other show"}}
    provider._remember_media("other alias", unrelated)
    other_mapping = ("999", "https://anikototv.to/watch/other/ep-1")
    provider._anime_page_cache["222"] = other_mapping
    provider._anime_page_cache["other alias"] = other_mapping

    provider.invalidate_title_cache(title_id)

    assert provider._media_cache == {"222": unrelated, "other alias": unrelated}
    assert provider._anime_page_cache == {"222": other_mapping, "other alias": other_mapping}
    provider.invalidate_title_cache("unknown")
    assert provider._media_cache == {"222": unrelated, "other alias": unrelated}
    assert provider._anime_page_cache == {"222": other_mapping, "other alias": other_mapping}

    site.media = {**site.media, "description": "Fresh metadata"}
    lookups = []

    def fetch(*args, **kwargs):
        lookups.append(kwargs["anilist_id"])
        return site.media

    monkeypatch.setattr("media_cli.services.anilist.fetch_one", fetch)
    refreshed = providers.get_provider("anikoto", callback)
    assert refreshed.get_title("150672")["description"] == "Fresh metadata"
    assert lookups == [150672]
    assert refreshed.episode_catalog("150672")["ids"] == ["7"]
    assert site.paths.count("/filter") == 2
    assert site.paths.count("/watch/oshi-no-ko/ep-1") == 3


@pytest.mark.parametrize("operation", ["episode_catalog", "episode_sources"])
def test_refresh_between_identity_and_catalog_cannot_disable_mal_verification(site, monkeypatch, operation):
    provider = AnikotoProvider()
    provider.search("title")
    assert provider._get_anime_page("150672")[0] == "562"
    resolve = provider._get_anime_page

    def invalidate_after_identity(*args, **kwargs):
        result = resolve(*args, **kwargs)
        provider.invalidate_title_cache("150672")
        site.catalog_error = "mal"
        return result

    monkeypatch.setattr(provider, "_get_anime_page", invalidate_after_identity)
    monkeypatch.setattr("media_cli.services.anilist.fetch_one", lambda *args, **kwargs: site.media)
    if operation == "episode_catalog":
        assert provider.episode_catalog("150672")["ids"] == ["1"]
    else:
        result = provider.episode_sources("150672", "7")
        assert result["_provider_sources"][0]["streamUrl"] == "https://cdn.example/1.mp4"
    assert "/ajax/server/list" not in site.paths
    assert provider._anime_page_cache == {}


@pytest.mark.parametrize("operation", ["episode_catalog", "episode_sources"])
def test_numeric_mapping_without_metadata_cannot_be_used_unvalidated(site, monkeypatch, operation):
    provider = AnikotoProvider()
    provider._anime_page_cache["150672"] = ("562", "https://anikototv.to/watch/oshi-no-ko/ep-1")
    monkeypatch.setattr("media_cli.services.anilist.fetch_one", lambda *args, **kwargs: None)
    if operation == "episode_catalog":
        assert provider.episode_catalog("150672")["ids"] == []
    else:
        result = provider.episode_sources("150672", "7")
        assert result["_provider_sources"][0]["streamUrl"] == "https://cdn.example/1.mp4"
    assert "/ajax/episode/list/562" not in site.paths
    assert provider._anime_page_cache == {}


@pytest.fixture
def response_gate():
    entered, release = Event(), Event()

    def pause():
        entered.set()
        assert release.wait(10), "Timed out releasing the old response"

    return entered, release, pause


@pytest.mark.parametrize("operation", ["fetch", "authenticated", "slug", "search"])
@pytest.mark.parametrize("boundary", ["title", "scope"])
def test_old_metadata_response_cannot_overwrite_refreshed_cache(site, monkeypatch, response_gate, operation, boundary):
    entered, release, pause = response_gate
    provider = AnikotoProvider()
    unrelated = {"id": 222, "title": {"romaji": "Other show"}}
    provider._remember_media("222", unrelated)
    old = {**site.media, "description": "Old metadata"}
    fresh = {**site.media, "description": "Fresh metadata"}
    requests = []

    def fetch():
        requests.append("lookup")
        if not entered.is_set():
            pause()
            return old
        return fresh

    monkeypatch.setattr("media_cli.services.anilist.fetch_one", lambda *args, **kwargs: fetch())
    monkeypatch.setattr(
        "media_cli.core.anilist_fallback.search_anilist_with_fallback",
        lambda query: {"data": {"Page": {"media": [fetch()]}}},
    )
    monkeypatch.setattr(
        "media_cli.services.anilist_auth.stored_anilist_token",
        lambda config: "token" if operation == "authenticated" else "",
    )
    selection = "alias" if operation == "slug" else "150672"

    def lookup():
        return provider.search("title")[0] if operation == "search" else provider._fetch_media(selection)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(lookup)
        try:
            assert entered.wait(10), "Old metadata request never started"
            if boundary == "title":
                provider.invalidate_title_cache(selection)
                assert provider._media_cache["222"] == unrelated
            else:
                provider.domains = ["https://new-embed.example"]
            assert lookup()["description"] == "Fresh metadata"
        finally:
            release.set()
        assert pending.result(timeout=10)["description"] == "Old metadata"

    assert provider._fetch_media("150672")["description"] == "Fresh metadata"
    if operation == "slug":
        assert provider._fetch_media("alias")["description"] == "Fresh metadata"
    assert requests == ["lookup", "lookup"]


@pytest.mark.parametrize("selection", ["150672", "alias", "https://anikototv.to/watch/oshi-no-ko/ep-1"])
@pytest.mark.parametrize("boundary", ["title", "scope"])
def test_old_identity_response_cannot_overwrite_refreshed_mapping(site, monkeypatch, response_gate, selection, boundary):
    entered, release, pause = response_gate
    provider = AnikotoProvider()
    provider.search("title")
    provider._remember_media("alias", site.media)
    monkeypatch.setattr("media_cli.services.anilist.fetch_one", lambda *args, **kwargs: site.media)
    request_site = site.request

    def request(req, timeout=None):
        path = urlsplit(req.full_url).path
        if path == "/watch/oshi-no-ko/ep-1":
            if not entered.is_set():
                pause()
                site_id = "562"
            else:
                site_id = "777"
            html = f'<main data-id="{site_id}"><h1>OSHI NO KO</h1></main>'
            return Mock(read=lambda: html.encode(), geturl=lambda: req.full_url)
        if path == "/ajax/episode/list/777":
            body = json.dumps({"result": '<a data-num="7" data-mal="52034">7</a>'})
            return Mock(read=lambda: body.encode(), geturl=lambda: req.full_url)
        return request_site(req, timeout)

    monkeypatch.setattr("urllib.request.urlopen", request)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(provider._get_anime_page, selection)
        try:
            assert entered.wait(10), "Old identity request never started"
            if boundary == "title":
                provider.invalidate_title_cache(selection)
            else:
                provider.domains = ["https://new-embed.example"]
            assert provider._get_anime_page(selection)[0] == "777"
        finally:
            release.set()
        assert pending.result(timeout=10)[0] == "562"

    assert provider._get_anime_page(selection)[0] == "777"
    assert all(value[0] == "777" for value in provider._anime_page_cache.values())


@pytest.mark.parametrize("failure", ["mal", "json"])
def test_old_catalog_rejection_cannot_evict_refreshed_identity(site, monkeypatch, response_gate, failure):
    entered, release, pause = response_gate
    provider = AnikotoProvider()
    provider.search("title")
    assert provider._get_anime_page("150672")[0] == "562"
    request_site = site.request

    def request(req, timeout=None):
        if urlsplit(req.full_url).path == "/ajax/episode/list/562" and not entered.is_set():
            pause()
            body = "invalid json" if failure == "json" else json.dumps({"result": '<a data-num="7" data-mal="999">7</a>'})
            return Mock(read=lambda: body.encode(), geturl=lambda: req.full_url)
        return request_site(req, timeout)

    monkeypatch.setattr("urllib.request.urlopen", request)
    monkeypatch.setattr("media_cli.services.anilist.fetch_one", lambda *args, **kwargs: site.media)
    watch_url = "https://anikototv.to/watch/oshi-no-ko/ep-1"
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(provider._episode_page, "562", watch_url, site.media)
        try:
            assert entered.wait(10), "Old catalog request never started"
            provider.invalidate_title_cache("150672")
            assert provider._get_anime_page("150672")[0] == "562"
        finally:
            release.set()
        if failure == "json":
            with pytest.raises(json.JSONDecodeError):
                pending.result(timeout=10)
        else:
            assert pending.result(timeout=10) is None
    assert provider._anime_page_cache["150672"] == ("562", watch_url)


@pytest.mark.parametrize("selection", ["150672", "999999"])
def test_get_title_without_search_exposes_actual_anilist_identity_and_loaded_empty_fields(site, monkeypatch, selection):
    from media_cli.domain.metadata import title_metadata_complete

    media = {**site.media, "description": None, "genres": []}
    requests = []

    def fetch(*args, **kwargs):
        requests.append(kwargs["anilist_id"])
        return media

    monkeypatch.setattr("media_cli.services.anilist.fetch_one", fetch)
    provider = AnikotoProvider()
    show = provider.get_title(selection)
    assert show["aniListId"] == "150672"
    assert show["_metadata_fields_loaded"] == ("description", "genres")
    assert title_metadata_complete(show) is True
    assert provider.get_title("150672")["aniListId"] == "150672"
    assert requests == [int(selection)]

