import json
import pathlib
import tomllib
from unittest.mock import MagicMock, patch
from media_cli.providers.anikoto import AnikotoProvider, _vrf_encrypt


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


def test_default_install_includes_megaplay_crypto_backend():
    root = pathlib.Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    deps = [dep.lower() for dep in project["project"]["dependencies"]]

    assert any(
        dep.startswith(("cryptography", "pycryptodome")) for dep in deps
    ), "Anikoto MegaPlay sources need AES-CBC support in the default install."


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

    with patch("media_cli.services.anilist.fetch_one", side_effect=mock_fetch_one):
        with patch("media_cli.services.anilist_auth.stored_anilist_token", return_value="stale_token"):
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


def test_anikoto_resolves_anime_id_from_generic_data_id():
    p = AnikotoProvider()
    p._media_cache["154587"] = {
        "id": 154587,
        "title": {"romaji": "Sousou no Frieren", "english": "Frieren: Beyond Journey's End"},
        "synonyms": [],
    }

    search_html = '<a href="/watch/frieren-journey" class="film-name">Sousou no Frieren</a>'
    watch_html = '<main data-id="9999"><h1>Sousou no Frieren</h1></main>'

    def fake_urlopen(req, timeout=0):
        url = req.full_url
        mock_resp = MagicMock()
        mock_resp.read.return_value = (search_html if "/filter" in url else watch_html).encode("utf-8")
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert p._get_anime_page("154587") == ("9999", "https://anikototv.to/watch/frieren-journey")


def test_anikoto_get_anime_page_retries_search_without_vrf():
    import urllib.error

    p = AnikotoProvider()
    p._media_cache["154587"] = {
        "id": 154587,
        "title": {"english": "Frieren: Beyond Journey's End"},
        "synonyms": [],
    }

    search_html = '<a href="/watch/frieren-journey" class="film-name">Frieren: Beyond Journey\'s End</a>'
    watch_html = '<main data-id="9999"><h1>Frieren</h1></main>'
    seen_urls = []

    def fake_urlopen(req, timeout=0):
        url = req.full_url
        seen_urls.append(url)
        if "/filter" in url and "vrf=" in url:
            raise urllib.error.HTTPError(url, 500, "broken vrf", {}, None)
        mock_resp = MagicMock()
        mock_resp.read.return_value = (search_html if "/filter" in url else watch_html).encode("utf-8")
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert p._get_anime_page("154587") == ("9999", "https://anikototv.to/watch/frieren-journey")

    assert any("vrf=" in url for url in seen_urls)
    assert any("/filter" in url and "vrf=" not in url for url in seen_urls)


def test_anikoto_get_anime_page_tries_native_and_slug_candidates():
    import urllib.error

    p = AnikotoProvider()
    p._media_cache["184872"] = {
        "id": 184872,
        "title": {
            "romaji": "Nitian Xie Shen: Nian Fan",
            "english": None,
            "native": "逆天邪神 年番",
        },
        "synonyms": ["Against the Gods 2", "Nitian Xie Shen 2", "逆天邪神 第2季"],
    }

    native_search_html = """
    <a href="/watch/against-the-gods-2nd-season-ghmos/ep-1">
      <span>18</span><span>52</span><span>ONA</span>
    </a>
    """
    watch_html = '<div id="watch-main" data-id="8765"><h1>Against the Gods 2nd Season</h1></div>'
    seen_urls = []

    def fake_urlopen(req, timeout=0):
        url = req.full_url
        seen_urls.append(url)
        if "keyword=Nitian" in url or "keyword=Against" in url:
            raise urllib.error.HTTPError(url, 500, "broken ascii search", {}, None)
        mock_resp = MagicMock()
        mock_resp.read.return_value = (
            native_search_html if "/filter" in url else watch_html
        ).encode("utf-8")
        return mock_resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        assert p._get_anime_page("184872") == (
            "8765",
            "https://anikototv.to/watch/against-the-gods-2nd-season-ghmos/ep-1",
        )

    assert any("%E9%80%86" in url for url in seen_urls)
