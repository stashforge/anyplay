import json
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest

from media_cli.providers.anikoto import AnikotoProvider

ANILIST_ID = "150672"
SITE_ID = "562"
WATCH_URL = "https://anikototv.to/watch/oshi-no-ko/ep-1"
STREAM_URL = "https://cdn.example.test/selected-episode.mp4"


def install_responses(monkeypatch, responses):
    requests = []

    def urlopen(request, timeout=None):
        requests.append(request)
        parsed = urlsplit(request.full_url)
        key = (parsed.netloc, parsed.path)
        assert key in responses, f"Unexpected network request: {request.full_url}"
        payload = responses[key]
        body = json.dumps(payload) if isinstance(payload, dict) else payload
        return Mock(read=Mock(return_value=body.encode("utf-8")))

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    return requests


@pytest.mark.parametrize(
    "sources",
    [[{"file": STREAM_URL}], {"url": STREAM_URL}],
    ids=["source-list", "source-dict"],
)
def test_unresolved_identity_uses_selected_anilist_id_for_megaplay(monkeypatch, sources):
    provider = AnikotoProvider()
    # Neither unrelated cached media nor a previously resolved site ID owns this selection.
    provider._media_cache["166531"] = {"id": 166531, "title": {"english": "Stale selection"}}
    provider._media_cache[ANILIST_ID] = {"id": 166531}
    provider._anime_page_cache["166531"] = (SITE_ID, WATCH_URL)
    resolve = Mock(return_value=(None, None))
    monkeypatch.setattr(provider, "_get_anime_page", resolve)
    requests = install_responses(
        monkeypatch,
        {
            ("megaplay.buzz", "/stream/ani/150672/1/sub"): '<div data-id="98765"></div>',
            ("megaplay.buzz", "/stream/getSources"): {
                "sources": sources,
                "tracks": [
                    {
                        "file": "https://cdn.example.test/english.vtt",
                        "label": "English",
                        "kind": "captions",
                        "default": True,
                    }
                ],
            },
        },
    )

    result = provider.episode_sources(ANILIST_ID, "1", "sub")

    resolve.assert_called_once_with(ANILIST_ID)
    assert [request.full_url for request in requests] == [
        "https://megaplay.buzz/stream/ani/150672/1/sub",
        "https://megaplay.buzz/stream/getSources?id=98765",
    ]
    assert result is not None
    assert result["_provider"] == "anikoto"
    assert result["_provider_id"] == ANILIST_ID
    assert result["_provider_episode"] == "1"
    assert result["_provider_sources"] == result["episode"]["sourceUrls"]
    assert len(result["_provider_sources"]) == 1
    source = result["_provider_sources"][0]
    assert source["streamUrl"] == STREAM_URL
    assert source["sourceName"] == "MegaPlay (Auto)"
    assert source["type"] == "mp4"
    assert source["requires_proxy"] is True
    assert source["headers"]["Referer"] == "https://megaplay.buzz/"
    assert source["headers"]["Origin"] == "https://megaplay.buzz"
    assert source["subtitle_url"] == "https://cdn.example.test/english.vtt"
    assert source["subtitles"] == [
        {
            "url": "https://cdn.example.test/english.vtt",
            "label": "English",
            "kind": "captions",
            "default": True,
        }
    ]


def test_verified_site_id_drives_episode_catalog_request(monkeypatch):
    provider = AnikotoProvider()
    provider._media_cache[ANILIST_ID] = {"id": 150672}
    resolve = Mock(return_value=(SITE_ID, WATCH_URL))
    monkeypatch.setattr(provider, "_get_anime_page", resolve)
    fetch_one = Mock(side_effect=AssertionError("Cached metadata must not be fetched again"))
    monkeypatch.setattr("media_cli.services.anilist.fetch_one", fetch_one)
    requests = install_responses(
        monkeypatch,
        {
            ("anikototv.to", "/ajax/episode/list/562"): {
                "result": """
            <div class="episodes"><ul>
              <li><a data-num="1" data-sub="1" data-dub="1"><span class="d-title">First</span></a></li>
              <li><a data-num="2" data-sub="1" data-dub="0"><span class="d-title">Second</span></a></li>
            </ul></div>
        """
            },
        },
    )

    catalog = provider.episode_catalog(ANILIST_ID, "sub")

    resolve.assert_called_once_with(ANILIST_ID, _verified_catalogs={})
    fetch_one.assert_not_called()
    assert len(requests) == 1
    assert requests[0].get_header("Referer") == WATCH_URL
    assert catalog["state"] == "loaded"
    assert catalog["_provider_id"] == ANILIST_ID
    assert catalog["ids"] == ["1", "2"]
    assert catalog["detail"]["sub"] == ["1", "2"]
    assert catalog["detail"]["dub"] == ["1"]
    assert catalog["labels"]["2"] == "2. Second"


def test_verified_site_id_drives_sources_and_exact_episode_label(monkeypatch):
    provider = AnikotoProvider()
    provider._media_cache[ANILIST_ID] = {"id": 150672}
    resolve = Mock(return_value=(SITE_ID, WATCH_URL))
    monkeypatch.setattr(provider, "_get_anime_page", resolve)
    requests = install_responses(
        monkeypatch,
        {
            ("anikototv.to", "/ajax/episode/list/562"): {
                "result": """
            <a data-num="2" data-ids="wrong-episode">Episode 2</a>
            <a data-num="1" data-ids="selected-episode">Episode 1</a>
        """
            },
            ("anikototv.to", "/ajax/server/list"): {
                "result": """
            <div class="type" data-type="sub">
              <li data-link-id="selected-server">HD-2</li>
            </div>
        """
            },
            ("anikototv.to", "/ajax/server"): {
                "result": {"url": "https://megaplay.buzz/embed/selected"},
            },
            ("megaplay.buzz", "/embed/selected"): '<div data-id="98765"></div>',
            ("megaplay.buzz", "/stream/getSourcesNew"): {
                "sources": [{"file": STREAM_URL}],
            },
        },
    )

    result = provider.episode_sources(ANILIST_ID, "1", "sub")

    resolve.assert_called_once_with(ANILIST_ID)
    assert [urlsplit(request.full_url).path for request in requests] == [
        "/ajax/episode/list/562",
        "/ajax/server/list",
        "/ajax/server",
        "/embed/selected",
        "/stream/getSourcesNew",
    ]
    assert requests[0].get_header("Referer") == WATCH_URL
    assert parse_qs(urlsplit(requests[1].full_url).query) == {"servers": ["selected-episode"]}
    assert parse_qs(urlsplit(requests[2].full_url).query) == {"get": ["selected-server"]}
    assert parse_qs(urlsplit(requests[4].full_url).query) == {"id": ["98765"], "s": ["tcdn"]}
    assert result is not None
    assert result["_provider_id"] == ANILIST_ID
    assert result["_provider_episode"] == "1"
    assert result["_provider_sources"] == result["episode"]["sourceUrls"]
    assert len(result["_provider_sources"]) == 1
    assert result["_provider_sources"][0]["streamUrl"] == STREAM_URL


def test_changed_catalog_identity_cannot_supply_sources(monkeypatch):
    provider = AnikotoProvider()
    provider._media_cache[ANILIST_ID] = {"id": 150672, "idMal": 52034}
    provider._anime_page_cache[ANILIST_ID] = (SITE_ID, WATCH_URL)
    requests = install_responses(
        monkeypatch,
        {
            ("anikototv.to", "/ajax/episode/list/562"): {
                "result": '<a data-num="1" data-mal="16894" data-ids="unrelated-server">1</a>'
            },
            ("megaplay.buzz", "/stream/ani/150672/1/sub"): '<div data-id="98765"></div>',
            ("megaplay.buzz", "/stream/getSources"): {"sources": [{"file": STREAM_URL}]},
        },
    )
    result = provider.episode_sources(ANILIST_ID, "1")
    assert result is not None
    assert result["_provider_sources"][0]["sourceName"] == "MegaPlay (Auto)"
    assert not any("/ajax/server/" in req.full_url or "/ajax/server/list" in req.full_url for req in requests)
    assert ANILIST_ID not in provider._anime_page_cache


def test_unnumbered_anchor_cannot_bypass_mal_check_via_list_position(monkeypatch):
    provider = AnikotoProvider()
    provider._media_cache[ANILIST_ID] = {"id": 150672, "idMal": 52034}
    provider._anime_page_cache[ANILIST_ID] = (SITE_ID, WATCH_URL)
    requests = install_responses(
        monkeypatch,
        {
            ("anikototv.to", "/ajax/episode/list/562"): {
                "result": '<a data-mal="16894" data-ids="wrong-server">Unnumbered item</a>'
                '<a data-num="2" data-mal="52034" data-ids="episode-two">2</a>'
            },
            ("megaplay.buzz", "/stream/ani/150672/1/sub"): '<div data-id="98765"></div>',
            ("megaplay.buzz", "/stream/getSources"): {"sources": [{"file": STREAM_URL}]},
        },
    )
    result = provider.episode_sources(ANILIST_ID, "1")
    assert result is not None
    assert result["_provider_sources"][0]["sourceName"] == "MegaPlay (Auto)"
    assert not any("/ajax/server/list" in req.full_url for req in requests)
