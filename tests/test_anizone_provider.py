from unittest.mock import MagicMock, patch

from media_cli.providers.anizone import AniZoneProvider


def _urlopen_response(text: str):
    response = MagicMock()
    response.read.return_value = text.encode("utf-8")
    return response


ANIZONE_TITLE_HTML = r"""
<html>
  <body>
    <div
      x-data="{
        anmTitles: JSON.parse('{\u00225\u0022:\u0022Sousou no Frieren\u0022,\u00221\u0022:\u0022Frieren: Beyond Journey`s End\u0022,\u00228\u0022:\u0022\\u846c\\u9001\\u306e\\u30d5\\u30ea\\u30fc\\u30ec\\u30f3\u0022}'),
        items: JSON.parse('[{\u0022slug\u0022:\u00221\u0022,\u0022url\u0022:\u0022https:\\\/\\\/anizone.to\\\/anime\\\/mdkytdqp\\\/1\u0022,\u0022title_list\u0022:{\u00221\u0022:\u0022The Journey`s End\u0022},\u0022summary\u0022:\u0022The world celebrates the Demon King`s defeat.\u0022},{\u0022slug\u0022:\u00222\u0022,\u0022url\u0022:\u0022https:\\\/\\\/anizone.to\\\/anime\\\/mdkytdqp\\\/2\u0022,\u0022title_list\u0022:{\u00221\u0022:\u0022It Didn`t Have to Be Magic...\u0022},\u0022summary\u0022:\u0022Frieren instructs Fern.\u0022}]')
      }"
    ></div>
    <img src="https://anizone.to/images/anime/frieren.jpg" class="absolute w-full h-full object-cover" />
    <span class="inline-block">Completed</span>
    <span class="inline-block">28 Episodes</span>
    <span class="inline-block">2023</span>
    <h3 class="sr-only">Synopsis</h3>
    <div>* Based on a manga.<br /> <br /> Elf mage Frieren begins a new journey.</div>
  </body>
</html>
"""


def test_anizone_get_title_uses_embedded_title_page_metadata():
    provider = AniZoneProvider()

    with patch("urllib.request.urlopen", return_value=_urlopen_response(ANIZONE_TITLE_HTML)):
        title = provider.get_title("mdkytdqp")

    assert title["name"] == "Frieren: Beyond Journey`s End"
    assert title["romajiName"] == "Sousou no Frieren"
    assert title["nativeName"] == "葬送のフリーレン"
    assert title["thumbnail"] == "https://anizone.to/images/anime/frieren.jpg"
    assert title["description"] == "Elf mage Frieren begins a new journey."
    assert title["status"] == "FINISHED"
    assert title["episodeCount"] == 28
    assert title["availableEpisodes"] == {"sub": 28, "dub": 28, "raw": 0}
    assert title["airedStart"] == {"year": 2023}


def test_anizone_episode_catalog_uses_embedded_episode_json_without_links():
    provider = AniZoneProvider()

    with patch("urllib.request.urlopen", return_value=_urlopen_response(ANIZONE_TITLE_HTML)):
        catalog = provider.episode_catalog("mdkytdqp", "sub")

    assert catalog["state"] == "loaded"
    assert catalog["ids"] == ["1", "2"]
    assert catalog["labels"] == {
        "1": "The Journey`s End",
        "2": "It Didn`t Have to Be Magic...",
    }


def test_anizone_dub_catalog_reuses_multi_audio_episode_list():
    provider = AniZoneProvider()

    with patch("urllib.request.urlopen", return_value=_urlopen_response(ANIZONE_TITLE_HTML)):
        catalog = provider.episode_catalog("mdkytdqp", "dub")

    assert catalog["state"] == "loaded"
    assert catalog["ids"] == ["1", "2"]
    assert catalog["episodes"]["dub"][0]["id"] == "1"
    assert catalog["episodes_map_by_ttype"]["dub"]["1"]["label"] == "The Journey`s End"


def test_anizone_episode_sources_expand_master_hls_qualities(monkeypatch):
    provider = AniZoneProvider()
    episode_html = r"""
    <div x-data="vidstackPlayer(JSON.parse('{\u0022src\u0022:\u0022https:\\\/\\\/cdn.example\\\/master.m3u8\u0022,\u0022subtitles\u0022:[{\u0022title\u0022:\u0022English\u0022,\u0022file\u0022:\u0022https:\\\/\\\/cdn.example\\\/en.vtt\u0022}]}'))"></div>
    """

    def fake_extract(self, master_url, **kwargs):
        assert master_url == "https://cdn.example/master.m3u8"
        assert kwargs["name"] == "AniZone (VidStack)"
        assert kwargs["referer"] == "https://anizone.to/"
        assert kwargs["subtitles"][0]["url"] == "https://cdn.example/en.vtt"
        return [
            {
                "source_name": "AniZone (VidStack) 1080p (1920×1080)",
                "link": "https://cdn.example/1080.m3u8",
                "type": "hls",
                "resolution": "1080p (1920×1080)",
                "headers": {"User-Agent": "UA"},
                "referer": "https://anizone.to/",
            },
            {
                "source_name": "AniZone (VidStack) 360p (640×360)",
                "link": "https://cdn.example/360.m3u8",
                "type": "hls",
                "resolution": "360p (640×360)",
                "headers": {"User-Agent": "UA"},
                "referer": "https://anizone.to/",
            },
        ]

    monkeypatch.setattr("media_cli.extractors.base.BaseExtractor.extract_m3u8", fake_extract)

    with patch("urllib.request.urlopen", return_value=_urlopen_response(episode_html)):
        sources = provider.episode_sources("mdkytdqp", "1", "sub")

    urls = sources["episode"]["sourceUrls"]
    assert [src["sourceName"] for src in urls] == [
        "AniZone (VidStack) 1080p (1920×1080)",
        "AniZone (VidStack) 360p (640×360)",
    ]
    assert urls[0]["link"] == "https://cdn.example/1080.m3u8"
