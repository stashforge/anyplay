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

    def fake_play(path, **fields):
        assert fields["id"] == "1000000001"
        if path == "/episodes":
            return {"list": [{"id": "23316", "number": "1", "name": "Episode 1", "servers": [
                {"id": "23316/jpn"}, {"id": "23316/jpn"}, {"id": "23316/eng"},
            ]}]}
        if path == "/stream":
            assert fields["server"] == "23316/jpn"
            return {"link": "https://cdn.example/master.m3u8", "subtitles": [
                {"file": "https://cdn.example/en.vtt", "label": "English", "lang": "eng"},
                {"file": "off", "lang": "OFF"},
            ]}
        raise AssertionError(path)

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

    provider._play_json = fake_play
    monkeypatch.setattr("media_cli.extractors.base.BaseExtractor.extract_m3u8", fake_extract)

    sources = provider.episode_sources("1000000001", "1", "sub")
    urls = sources["episode"]["sourceUrls"]

    assert [src["sourceName"] for src in urls] == [
        "Japanese 1080p (1920×1080)",
        "Japanese 720p (1280×720)",
    ]
    assert urls[0]["link"] == "https://cdn.example/1080.m3u8"
    assert urls[0]["resolution"] == "1080p (1920×1080)"

    assert urls[0]["subtitles"] == [{"url": "https://cdn.example/en.vtt", "label": "English", "lang": "eng"}]


def test_signed_playback_request_and_clock_retry(monkeypatch):
    import hashlib
    import hmac
    import io
    import urllib.error
    from media_cli.providers import anidbapp
    provider = AniDBApp()
    monkeypatch.setattr(anidbapp.time, "time", lambda: 1000)
    monkeypatch.setattr(anidbapp.secrets, "token_bytes", lambda n: b"a" * n)
    calls = []

    class Response(io.BytesIO):
        headers = {"Date": "Thu, 01 Jan 1970 00:17:00 GMT"}

    def open_request(req, **kwargs):
        calls.append(req)
        assert req.full_url == "https://api.kyotoplayer.com/episodes"
        assert req.get_method() == "POST"
        assert req.data == b"id=1000000001"
        timestamp = "1000" if len(calls) == 1 else "1020"
        canonical = "\n".join(("POST", "/episodes", hashlib.sha256(req.data).hexdigest(),
                                timestamp, "YWFhYWFhYWFhYWFhYWFhYQ", "135"))
        assert req.get_header("X-kyoto-sig") == hmac.new(anidbapp.KYOTO_KEY, canonical.encode(), hashlib.sha256).hexdigest()
        assert req.get_header("X-kyoto-cert") == anidbapp.KYOTO_CERT
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 401, "clock skew", Response.headers, io.BytesIO(b"rejected"))
        return Response(b'{"list": []}')

    monkeypatch.setattr(anidbapp.urllib.request, "urlopen", open_request)
    assert provider._play_json("/episodes", id="1000000001") == {"list": []}
    assert len(calls) == 2


def test_failed_episode_request_is_error_not_empty_catalogue(monkeypatch):
    import urllib.error
    provider = AniDBApp()
    def fail(*args, **kwargs):
        raise urllib.error.URLError("DNS lookup failed")
    monkeypatch.setattr(provider, "_play_json", fail)
    catalogue = provider.episode_catalog("1000000001")
    assert catalogue["state"] == "error"
    assert "DNS lookup failed" in catalogue["error"]


def test_cloudflare_fallback_is_lazy_scoped_and_closes_response(monkeypatch):
    import io
    import urllib.error
    from types import SimpleNamespace
    from media_cli.providers import anidbapp
    from media_cli.services import requirements
    provider = AniDBApp()
    calls = []
    closed = []
    def blocked(req, **kwargs):
        raise urllib.error.HTTPError(req.full_url, 403, "blocked", {"Server": "cloudflare"}, io.BytesIO(b"blocked"))
    class CurlResponse:
        status_code = 200
        headers = {}
        def iter_content(self, size):
            yield b'{"list": []}'
        def close(self):
            closed.append(True)
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return CurlResponse()
    monkeypatch.setattr(anidbapp.urllib.request, "urlopen", blocked)
    monkeypatch.setattr(requirements, "optional_curl_requests", lambda: SimpleNamespace(post=post))
    assert provider._play_json("/episodes", id="1000000001") == {"list": []}
    assert calls[0][0] == "https://api.kyotoplayer.com/episodes"
    assert calls[0][1]["allow_redirects"] is False
    assert calls[0][1]["data"] == b"id=1000000001"
    assert closed == [True]
