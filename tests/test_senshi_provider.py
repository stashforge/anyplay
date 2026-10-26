import json
from unittest.mock import MagicMock, patch

import pytest

from media_cli.media.decryption import decrypt_em3u8
from media_cli.providers.senshi import Senshi


def test_decrypt_em3u8_invalid_inputs():
    assert decrypt_em3u8(None) is None
    assert decrypt_em3u8("") is None
    assert decrypt_em3u8("EM3U8v1:") is None
    assert decrypt_em3u8("EM3U8v1:short") is None
    assert decrypt_em3u8("regular_playlist.m3u8") is None


def test_senshi_metadata():
    p = Senshi()
    assert p.id == "senshi"
    assert "senshi.to" in p.base_url
    assert p.name == "Senshi"
    assert p.browser_url(provider_id="123", episode="1") == "https://senshi.to/watch/123/1"


def test_senshi_search_native_success():
    p = Senshi()
    sample_response = {
        "total": 1,
        "data": [
            {
                "id": 58567,
                "title": "Ore dake Level Up",
                "title_english": "Solo Leveling Season 2",
                "anime_picture": "/posters/58567.webp",
                "type": "TV",
                "ani_status": "Finished Airing",
            }
        ],
    }

    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(sample_response).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        results = p.search("Solo Leveling")
        assert len(results) == 1
        assert results[0]["id"] == "58567"
        assert results[0]["name"] == "Solo Leveling Season 2"
        assert results[0]["thumbnail"] == "https://senshi.to/posters/58567.webp"


def test_senshi_episode_catalog():
    p = Senshi()
    sample_eps = [
        {"ep_id": 1, "ep_title": "Episode 1"},
        {"ep_id": 2, "ep_title": "Episode 2"},
    ]

    with patch.object(p, "_fetch_json", return_value=sample_eps):
        catalog = p.episode_catalog("58567", "sub")
        assert catalog["state"] == "loaded"
        assert catalog["ids"] == ["1", "2"]
        assert "1" in catalog["labels"]


@pytest.mark.parametrize("payload", [
    {"data": [], "total": 0},
    {"data": [{"title": "Missing provider ID"}]},
    {},
])
def test_senshi_search_never_adds_anilist_only_titles(payload):
    response = MagicMock()
    response.read.return_value = json.dumps(payload).encode()
    response.__enter__.return_value = response
    foreign_result = {"data": {"Page": {"media": [
        {"idMal": 123, "title": {"romaji": "Not in Senshi"}},
    ]}}}
    with patch("urllib.request.urlopen", return_value=response) as fetch, \
            patch("media_cli.core.anilist_fallback.search_anilist_with_fallback", return_value=foreign_result) as fallback:
        assert Senshi().search("missing title") == []
    fallback.assert_not_called()
    assert fetch.call_count == 1
    assert fetch.call_args.args[0].full_url == "https://senshi.to/anime/filter"


@pytest.mark.parametrize("error", [TimeoutError("timeout"), ValueError("invalid response")])
def test_senshi_search_failure_does_not_search_anilist(error):
    with patch("urllib.request.urlopen", side_effect=error), \
            patch("media_cli.core.anilist_fallback.search_anilist_with_fallback", return_value={}) as fallback:
        assert Senshi().search("missing title") == []
    fallback.assert_not_called()


def test_senshi_uses_direct_embed_url_without_remote_source_id():
    p = Senshi()
    p._sources_cache.clear()
    embeds = [
        {
            "url": "https://cdn1.anidap.biz/stream/token/playlist.m3u8",
            "status": "HardSub",
        },
    ]

    with patch.object(p, "_fetch_json", return_value=embeds):
        sources = p.episode_sources("52991", "1", "sub")

    urls = sources["episode"]["sourceUrls"]
    assert len(urls) == 1
    assert urls[0]["sourceName"] == "[HardSub] auto"
    assert urls[0]["streamUrl"] == "https://cdn1.anidap.biz/stream/token/playlist.m3u8"


def test_senshi_expands_vidcloud_master_playlist_variants():
    p = Senshi()
    p._sources_cache.clear()
    embeds = [
        {
            "remote_source_id": 3731,
            "status": "HardSub",
        },
    ]
    vidcloud_payload = [
        {
            "source": {"src": "https://s-90.bcdn1.se/path/master.m3u8", "quality": "auto"},
            "tracks": [{"url": "https://s-90.bcdn1.se/en.vtt", "label": "English", "default": True}],
        }
    ]
    master = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1920x1080
1080/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=854x480
https://s-90.bcdn1.se/480/index.m3u8
"""

    with patch.object(p, "_fetch_json", return_value=embeds), \
            patch.object(p, "_resolve_remote_sources", return_value=vidcloud_payload), \
            patch.object(p, "_read_playlist", return_value=("https://s-90.bcdn1.se/path/master.m3u8", master)):
        sources = p.episode_sources("52991", "1", "sub")

    urls = sources["episode"]["sourceUrls"]
    assert [src["sourceName"] for src in urls] == ["[HardSub] 1080p", "[HardSub] 480p"]
    assert urls[0]["link"] == "https://s-90.bcdn1.se/path/1080/index.m3u8"
    assert urls[1]["link"] == "https://s-90.bcdn1.se/480/index.m3u8"
    assert urls[0]["subtitles"][0]["url"] == "https://s-90.bcdn1.se/en.vtt"
