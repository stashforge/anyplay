import json
from unittest.mock import MagicMock, patch
from media_cli.providers.senshi import Senshi
from media_cli.media.decryption import decrypt_em3u8


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


def test_senshi_uses_direct_embed_url_when_vidcloud_resolution_is_unavailable():
    p = Senshi()
    p._sources_cache.clear()
    embeds = [
        {
            "remote_source_id": 3731,
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
            "source": {"src": "https://cdn.example/path/master.m3u8", "quality": "auto"},
            "tracks": [{"url": "https://cdn.example/en.vtt", "label": "English", "default": True}],
        }
    ]
    master = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1920x1080
1080/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=854x480
https://cdn.example/480/index.m3u8
"""

    def fake_urlopen(req, timeout=7, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        resp = MagicMock()
        if "_v1/sources" in url:
            resp.read.return_value = json.dumps(vidcloud_payload).encode("utf-8")
        elif url == "https://cdn.example/path/master.m3u8":
            resp.read.return_value = master.encode("utf-8")
        else:
            raise AssertionError(url)
        resp.__enter__.return_value = resp
        return resp

    with patch.object(p, "_fetch_json", return_value=embeds), patch("urllib.request.urlopen", side_effect=fake_urlopen):
        sources = p.episode_sources("52991", "1", "sub")

    urls = sources["episode"]["sourceUrls"]
    assert [src["sourceName"] for src in urls] == ["[HardSub] 1080p", "[HardSub] 480p"]
    assert urls[0]["link"] == "https://cdn.example/path/1080/index.m3u8"
    assert urls[1]["link"] == "https://cdn.example/480/index.m3u8"
    assert urls[0]["subtitles"][0]["url"] == "https://cdn.example/en.vtt"
