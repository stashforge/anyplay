"""AniPM title mapping keeps provider metadata and stable playback identity."""

from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest

from media_cli.providers.anipm import AniPMProvider


@pytest.fixture
def title():
    return {
        "anilistId": 20, "malId": 20, "title": "Naruto", "nativeTitle": "Native",
        "format": "TV", "status": "Finished", "year": 2002, "season": "fall",
        "score": 8.03, "rating": "PG-13", "durationMinutes": 24,
        "episodes": {"total": 220, "sub": 220, "dub": 217},
        "genres": ["Action", "Adventure"], "studios": ["Studio Pierrot"],
        "adult": False, "synopsis": "Synopsis", "poster": "https://ani.pm/cover",
        "banner": "https://ani.pm/banner", "url": "https://ani.pm/ani/example",
    }


def test_search_preserves_title_metadata_without_inventing_details(title):
    original = deepcopy(title)
    provider = AniPMProvider(request_json_fn=lambda *a, **kw: {"data": [title]})
    result = provider.search("Naruto")[0]
    assert result["_id"] == result["_provider_id"] == "20"
    assert result["_provider"] == "anipm"
    assert result["aniListId"] == result["malId"] == "20"
    assert result["name"] == "Naruto"
    assert result["nativeName"] == "Native"
    assert result["englishName"] == result["romajiName"] == ""
    assert result["thumbnail"] == title["poster"]
    assert result["banner"] == title["banner"]
    assert result["description"] == "Synopsis"
    assert result["format"] == "TV"
    assert result["status"] == "Finished"
    assert result["season"] == {"name": "fall", "year": 2002}
    assert result["episodeCount"] == 220
    assert result["availableEpisodes"] == {"sub": 220, "dub": 217, "raw": 0}
    assert result["availableEpisodesDetail"] == {"sub": [], "dub": [], "raw": []}
    assert result["score"] == 8.03
    for key in ("genres", "studios", "rating", "durationMinutes", "adult", "url"):
        assert result[key] == title[key]
    assert not result.get("_details_complete")
    assert title == original


def test_search_encodes_trimmed_query_and_overrides_default_headers():
    requests = []

    def request(url, **kwargs):
        requests.append((url, kwargs))
        return {"data": [], "hasMore": True}

    assert AniPMProvider(request_json_fn=request).search("  Oshi & Ko?  ") == []
    assert len(requests) == 1
    url, options = requests[0]
    parts = urlsplit(url)
    assert parts.scheme == "https" and parts.netloc == "ani.pm"
    assert parts.path == "/api/anime/catalog"
    assert parse_qs(parts.query) == {"q": ["Oshi & Ko?"], "page": ["1"], "limit": ["20"]}
    headers = options["extra_hdrs"]
    assert headers["Origin"] == "https://ani.pm"
    assert headers["Referer"] == "https://ani.pm/"
    assert headers["User-Agent"].startswith("Mozilla/")


@pytest.mark.parametrize("path, expected", [
    ("/api/anime/cover?key=poster", "https://ani.pm/api/anime/cover?key=poster"),
    ("https://cdn.example/cover.jpg", "https://cdn.example/cover.jpg"),
    ("//cdn.example/cover.jpg", "https://cdn.example/cover.jpg"),
    (None, ""),
    ("", ""),
])
def test_artwork_urls_are_absolute_in_search_and_details(title, path, expected):
    from media_cli.providers.anipm import _title

    title.update(poster=path, banner=path)
    original = deepcopy(title)
    provider = AniPMProvider(request_json_fn=lambda url, **kw:
                             {"items": [title]} if "/catalog?" in url else title)
    for show in (provider.search("Naruto")[0], provider.get_title("20"), _title(title)):
        assert show["thumbnail"] == expected
        assert show["banner"] == expected
    assert title == original


def test_rich_catalogue_supplies_aliases_without_per_row_requests_or_anilist(monkeypatch):
    from media_cli.core import enrichment
    from media_cli.domain.metadata import title_metadata_complete
    from media_cli.domain.titles import get_display_titles

    record = {"id": 6475, "source": "settlar", "anilistId": "150672", "malId": 52034,
              "title": "My Star", "native": "Native", "type": "TV", "episodeCount": 11,
              "subCount": 11, "dubCount": 11, "synopsis": "Synopsis", "genres": ["Drama"],
              "providerTitles": ["My Star", "Native", '"Oshi no Ko"', "A much longer alternative name"]}
    requests = []

    def request(url, **kw):
        requests.append(url)
        return {"items": [record]}

    def forbidden(*a, **kw):
        pytest.fail("Complete provider metadata must not request AniList")

    monkeypatch.setattr(enrichment, "fetch_anilist_by_ids", forbidden)
    show = AniPMProvider(request_json_fn=request).search("oshi")[0]
    assert len(requests) == 1
    assert show["_id"] == "150672"  # Never replace playback/history ID with Settlar 6475.
    assert show["nativeName"] == "Native"
    assert show["altNames"] == record["providerTitles"]
    assert show["englishName"] == show["romajiName"] == ""
    assert get_display_titles(show, "My Star") == '"Oshi no Ko"'
    assert title_metadata_complete(show)
    _, pending = enrichment.enrich_provider_results_fast([show])
    assert pending == []
    enrichment.enrich_provider_results_background([show])


def test_authoritative_empty_metadata_does_not_trigger_anilist():
    from media_cli.domain.metadata import title_metadata_complete

    record = {"anilistId": 20, "title": "Example", "synopsis": None, "genres": [],
              "providerTitles": []}
    show = AniPMProvider(request_json_fn=lambda *a, **kw: {"items": [record]}).search("Example")[0]
    assert title_metadata_complete(show)


def test_rich_details_use_verified_provider_mapping_and_keep_aliases():
    from media_cli.domain.titles import get_display_titles

    requests = []

    def request(url, **kw):
        requests.append(url)
        if "/ani/150672" in url:
            return {"id": 150672, "source": "anilist", "anilistId": "150672", "settlarId": 6475,
                    "title": "OSHI NO KO", "romaji": "[Oshi no Ko]"}
        return {"id": 6475, "source": "settlar", "anilistId": "150672", "malId": 52034,
                "title": "My Star", "native": "Native", "synopsis": "Synopsis", "genres": ["Drama"],
                "providerTitles": ["My Star", "Native", '"Oshi no Ko"']}

    show = AniPMProvider(request_json_fn=request).get_title("150672")
    assert show["_id"] == "150672"
    assert get_display_titles(show, "My Star") == '"Oshi no Ko"'
    assert requests == ["https://ani.pm/api/anime/ani/150672?routes=e4",
                        "https://ani.pm/api/anime/series/6475?routes=e4"]


def test_mismatched_provider_mapping_never_changes_title_identity():
    def request(url, **kw):
        if "/ani/20" in url:
            return {"anilistId": 20, "title": "Right title", "romaji": "Real romaji", "settlarId": 999}
        return {"anilistId": 21, "title": "Wrong title", "providerTitles": ["Wrong title", "Wrong alias"]}

    show = AniPMProvider(request_json_fn=request).get_title("20")
    assert show["_id"] == "20"
    assert show["name"] == "Right title"
    assert show["romajiName"] == "Real romaji"


def test_empty_query_does_not_request():
    def unexpected(*a, **kw):
        pytest.fail("Empty queries should not request anything")

    assert AniPMProvider(request_json_fn=unexpected).search("  ") == []


def test_mal_fallback_identity(title):
    title["anilistId"] = None
    result = AniPMProvider(request_json_fn=lambda *a, **kw: {"data": [title]}).search("Naruto")[0]
    assert result["_id"] == "mal-20"
    assert result["aniListId"] is None
    assert result["malId"] == "20"


@pytest.mark.parametrize("payload", [None, [], {"data": None}, {"data": {}}, {"error": {"code": "oops"}}])
def test_invalid_response_is_not_a_title(payload):
    assert AniPMProvider(request_json_fn=lambda *a, **kw: payload).search("Naruto") == []


def test_invalid_rows_are_skipped_without_losing_good_results(title):
    rows = [None, [], {}, {"title": "No identity"}, {**title, "title": " "}, title]
    result = AniPMProvider(request_json_fn=lambda *a, **kw: {"data": rows}).search("Naruto")
    assert [item["_id"] for item in result] == ["20"]


def test_request_failure_does_not_leak_exception_text(caplog):
    def failed(*a, **kw):
        raise OSError("secret request data")

    assert AniPMProvider(request_json_fn=failed).search("Naruto") == []
    assert "secret request data" not in caplog.text


def test_adapter_is_registered_with_primary_domain_only():
    from media_cli.providers import PROVIDER_FACTORIES, PROVIDER_REGISTRY

    assert PROVIDER_FACTORIES["anipm"] is AniPMProvider
    assert PROVIDER_REGISTRY["anipm"]["domains"] == ["https://ani.pm"]
    assert PROVIDER_REGISTRY["anipm"]["languages"] == ["sub", "dub"]


@pytest.fixture
def episodes():
    return [
        {"number": 1, "title": "First", "runtimeSeconds": 1416,
         "aired": "2026-01-01", "thumbnail": "https://ani.pm/episode.jpg",
         "available": {"sub": True, "dub": True},
         "embed": {"sub": "https://ani.pm/embed/1/sub", "dub": "https://ani.pm/embed/1/dub"}},
        {"number": 3.5, "available": {"sub": True, "dub": False}},
        {"number": "special", "available": {"sub": False, "dub": True}},
        {"number": 8, "available": {"sub": False, "dub": False}},
    ]


def test_get_title_uses_series_endpoint_and_shared_metadata(title, episodes):
    requests = []

    def request(url, **kwargs):
        requests.append(url)
        return {"data": {**title, "episodeList": episodes}}

    result = AniPMProvider(request_json_fn=request).get_title("20")
    assert requests == ["https://ani.pm/api/anime/ani/20?routes=e4"]
    assert result["name"] == "Naruto"
    assert result["availableEpisodesDetail"] == {"sub": ["1", "3.5"], "dub": ["1", "special"], "raw": []}
    assert result["availableEpisodes"] == {"sub": 2, "dub": 2, "raw": 0}
    assert result["episodeCount"] == 220


def test_catalog_preserves_actual_availability_and_episode_metadata(episodes):
    original = deepcopy(episodes)
    requests = []

    def request(url, **kwargs):
        requests.append(url)
        return {"data": episodes, "total": 999}

    result = AniPMProvider(request_json_fn=request).episode_catalog("mal-20", "dub")
    assert requests == ["https://ani.pm/api/partner/v1/titles/mal-20/episodes"]
    assert result["state"] == "loaded"
    assert result["ids"] == ["1", "special"]
    assert result["detail"] == {"sub": ["1", "3.5"], "dub": ["1", "special"], "raw": []}
    first = result["episodes_map"]["1"]
    assert first["title"] == "First"
    assert first["duration"] == 1416
    assert first["date"] == "2026-01-01"
    assert first["thumbnail"] == "https://ani.pm/episode.jpg"
    assert first["url"] == "https://ani.pm/embed/1/dub"
    assert first["translation_type"] == "dub"
    assert episodes == original


def test_empty_dub_never_falls_back_to_sub(episodes):
    rows = [{**episodes[0], "available": {"sub": True, "dub": False}}]
    result = AniPMProvider(request_json_fn=lambda *a, **kw: {"data": rows}).episode_catalog("20", "dub")
    assert result["state"] == "empty"
    assert result["ids"] == result["_provider_episode_ids"] == []
    assert result["episodes_map"] == result["by_number"] == result["labels"] == {}
    assert result["detail"]["sub"] == ["1"]


@pytest.mark.parametrize("payload", [None, {}, {"data": None}, {"data": {}}, {"error": {"code": "missing"}}])
def test_failed_catalog_is_not_reported_as_loaded_empty(payload):
    result = AniPMProvider(request_json_fn=lambda *a, **kw: payload).episode_catalog("20")
    assert result["state"] == "unavailable"
    assert result["error"]
    assert result["ids"] == []


def test_valid_empty_catalog_is_empty_not_unavailable():
    result = AniPMProvider(request_json_fn=lambda *a, **kw: {"data": []}).episode_catalog("20")
    assert result["state"] == "empty"
    assert result["error"] == ""


@pytest.mark.parametrize("identifier", ["", "../20", "20?token=secret", "mal-0", True])
def test_invalid_title_identity_never_requests(identifier):
    def unexpected(*a, **kw):
        pytest.fail("Invalid title identity must not be requested")

    provider = AniPMProvider(request_json_fn=unexpected)
    assert provider.get_title(identifier) is None
    assert provider.episode_catalog(identifier)["state"] == "unavailable"


def test_failed_details_and_catalog_can_be_retried(title, episodes):
    responses = iter([None, {"data": title}, OSError("secret"), {"data": episodes}])

    def request(*a, **kw):
        response = next(responses)
        if isinstance(response, Exception):
            raise response
        return response

    provider = AniPMProvider(request_json_fn=request)
    assert provider.get_title("20") is None
    assert provider.get_title("20")["name"] == "Naruto"
    assert provider.episode_catalog("20")["state"] == "unavailable"
    assert provider.episode_catalog("20")["ids"] == ["1", "3.5"]


def test_malformed_episode_rows_are_skipped(episodes):
    rows = [None, {}, {"number": True}, {"number": []},
            {"number": " ", "available": {"sub": True}},
            {"number": 2, "available": "yes"}, episodes[0]]
    result = AniPMProvider(request_json_fn=lambda *a, **kw: {"data": rows}).episode_catalog("20")
    assert result["ids"] == ["1"]


def test_browser_url_uses_verified_embed_route_without_network():
    def unexpected(*a, **kw):
        pytest.fail("Browser links must not resolve a session")

    provider = AniPMProvider(request_json_fn=unexpected)
    assert provider.browser_url("20", "3.5", "dub") == "https://ani.pm/embed/ani/20/3.5/dub"
    assert provider.browser_url("mal-20") == "https://ani.pm/embed/ani/mal-20/1/sub"
    assert "token" not in provider.browser_url("20", "1", cfg={"token": "secret"})


@pytest.mark.parametrize("identity,episode,language", [("../20", "1", "sub"), ("20", "1?x=secret", "sub"),
                                                      ("20", "../1", "sub"), ("20", "1", "bogus")])
def test_invalid_browser_target_falls_back_to_provider_home(identity, episode, language):
    assert AniPMProvider().browser_url(identity, episode, language) == "https://ani.pm"
