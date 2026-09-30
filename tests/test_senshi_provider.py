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
