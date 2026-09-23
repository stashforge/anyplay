import json
from unittest.mock import MagicMock, patch
from allmanga_cli.providers.anikoto import AnikotoProvider, _vrf_encrypt


def test_anikoto_vrf_encryption():
    assert _vrf_encrypt("") == ""
    # Non-empty inputs should produce non-empty URL-quoted strings
    v1 = _vrf_encrypt("8691")
    assert len(v1) > 0
    assert "%" in v1 or v1.isalnum()


def test_anikoto_metadata():
    p = AnikotoProvider()
    assert p.id == "anikoto"
    assert p.name == "Anikoto"
    assert "anikototv.to" in p.anikoto_url


def test_anikoto_fetch_media_token_failure_fallback():
    p = AnikotoProvider()
    # Mock anilist.fetch_one so that first call (with token) raises an exception,
    # and second call (without token) returns valid media.
    fake_media = {
        "id": 182205,
        "title": {
            "romaji": "Tensei Shitara Slime Datta Ken 4th Season",
            "english": "That Time I Got Reincarnated as a Slime Season 4",
        },
        "episodes": 24,
    }

    def mock_fetch_one(*args, **kwargs):
        token = args[2] if len(args) > 2 else kwargs.get("token", "")
        if token:
            import urllib.error
            raise urllib.error.HTTPError("https://graphql.anilist.co", 400, "Bad Request", {}, None)
        return fake_media

    with patch("allmanga_cli.services.anilist.fetch_one", side_effect=mock_fetch_one):
        with patch("allmanga_cli.services.anilist_auth.stored_anilist_token", return_value="stale_token"):
            media = p._fetch_media("182205")
            assert media is not None
            assert media["id"] == 182205
            assert media["title"]["english"] == "That Time I Got Reincarnated as a Slime Season 4"


def test_anikoto_episode_catalog_site_parsing():
    p = AnikotoProvider()
    mock_episodes_html = """
    <div class="episodes">
      <ul>
        <li><a href="#" data-num="1" data-sub="1" data-dub="1" data-ids="id_ep1"><span class="d-title">Episode One</span></a></li>
        <li><a href="#" data-num="2" data-sub="1" data-dub="1" data-ids="id_ep2"><span class="d-title">Episode Two</span></a></li>
        <li><a href="#" data-num="3" data-sub="1" data-dub="0" data-ids="id_ep3"><span class="d-title">Episode Three</span></a></li>
      </ul>
    </div>
    """

    with patch.object(p, "_get_anime_page", return_value=("8691", "https://anikototv.to/watch/slime/ep-1")):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"result": mock_episodes_html}).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            cat_sub = p.episode_catalog("182205", "sub")
            assert cat_sub["state"] == "loaded"
            assert cat_sub["ids"] == ["1", "2", "3"]
            assert cat_sub["detail"]["sub"] == ["1", "2", "3"]
            assert cat_sub["detail"]["dub"] == ["1", "2"]
            assert cat_sub["labels"]["3"] == "3. Episode Three"

            cat_dub = p.episode_catalog("182205", "dub")
            assert cat_dub["ids"] == ["1", "2"]
