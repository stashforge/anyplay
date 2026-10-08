import json

from media_cli.providers.anidbapp import AniDBApp


def test_anidb_search_uses_current_api_and_fetches_post_details():
    provider = AniDBApp()
    calls = []

    def fake_fetch(url, headers):
        calls.append(url)
        if "/api/search" in url:
            return 200, json.dumps({"posts": [{"id": 1000000001, "poster": "poster.jpg"}]})
        if "/api/post" in url:
            return 200, json.dumps({
                "id": 1000000001,
                "title": "Shy",
                "type": "TV",
                "poster": "poster.jpg",
                "overview": "A hero story.",
                "status": "Finished Airing",
                "premiered": "Fall 2023",
                "score": "6.7",
                "genres": "Action, Drama",
            })
        raise AssertionError(url)

    provider._fetch = fake_fetch

    results = provider.search("shy")

    assert calls[0].startswith("https://anilab2.amdapi.click/api/search?")
    assert calls[1] == "https://anilab2.amdapi.click/api/post?id=1000000001"
    assert results[0]["_provider_id"] == "1000000001"
    assert results[0]["name"] == "Shy"
    assert results[0]["thumbnail"] == "poster.jpg"
    assert results[0]["status"] == "FINISHED"
    assert results[0]["genres"] == ["Action", "Drama"]


def test_anidb_episode_sources_expand_hls_qualities_for_requested_audio(monkeypatch):
    provider = AniDBApp()

    def fake_fetch(url, headers):
        if url == "https://play.anidb.app/api/anime/1000000001/episodes":
            return 200, json.dumps({"list": [{"id": "23316", "number": "1", "name": "Episode 1"}]})
        if url == "https://play.anidb.app/api/episode/23316/servers":
            return 200, json.dumps({
                "list": [
                    {"id": "23316/jpn", "lang": "sub", "name": "SUB Server #1"},
                    {"id": "23316/jpn", "lang": "sub", "name": "SUB Server #2"},
                    {"id": "23316/eng", "lang": "dub", "name": "DUB Server #1"},
                ]
            })
        if url == "https://play.anidb.app/api/episode/23316/jpn/iframe":
            assert headers["Referer"] == "https://play.app/"
            assert headers["X-Requested-With"] == "PLAY"
            return 200, json.dumps({"link": "https://cdn.example/master.m3u8"})
        raise AssertionError(url)

    def fake_extract(self, master_url, **kwargs):
        assert master_url == "https://cdn.example/master.m3u8"
        assert kwargs["name"] == "Japanese"
        return [
            {
                "source_name": "Japanese 1080p (1920×1080)",
                "link": "https://cdn.example/1080.m3u8",
                "type": "hls",
                "resolution": "1080p (1920×1080)",
                "headers": {"User-Agent": "UA"},
                "referer": "",
            },
            {
                "source_name": "Japanese 720p (1280×720)",
                "link": "https://cdn.example/720.m3u8",
                "type": "hls",
                "resolution": "720p (1280×720)",
                "headers": {"User-Agent": "UA"},
                "referer": "",
            },
        ]

    provider._fetch = fake_fetch
    monkeypatch.setattr("media_cli.extractors.base.BaseExtractor.extract_m3u8", fake_extract)

    sources = provider.episode_sources("1000000001", "1", "sub")
    urls = sources["episode"]["sourceUrls"]

    assert [src["sourceName"] for src in urls] == [
        "Japanese 1080p (1920×1080)",
        "Japanese 720p (1280×720)",
    ]
    assert urls[0]["link"] == "https://cdn.example/1080.m3u8"
    assert urls[0]["resolution"] == "1080p (1920×1080)"
