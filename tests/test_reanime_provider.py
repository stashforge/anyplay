from media_cli.providers.reanime import ReAnimeProvider
from media_cli.media.local_proxy import _detect_fake_media_header, _flixcloud_ensure_token, _xor_bytes


def test_reanime_returns_hls_hosters_and_direct_downloads_for_requested_audio(monkeypatch):
    provider = ReAnimeProvider()
    provider._anime_meta["slime"] = {"anilist_id": 123, "title": "Slime"}
    provider._fetch_json = lambda path, referer=None, timeout=12: {
        "success": True,
        "servers": [
            {
                "serverName": "HD-1",
                "dataType": "sub",
                "dataLink": "https://flixcloud.cc/e/sub1",
                "softsub": False,
            },
            {
                "serverName": "HD-2",
                "dataType": "sub",
                "dataLink": "https://flixcloud.cc/e/sub2",
                "softsub": True,
            },
            {
                "serverName": "HD-1",
                "dataType": "dub",
                "dataLink": "https://flixcloud.cc/e/dub1",
                "softsub": False,
            },
        ],
    }

    def fake_direct(server):
        return {
            "sourceName": f"[Sub] {server['serverName']} Download (1080p)",
            "link": f"https://fetch.flixcloud.cc/{server['serverName']}.mkv",
            "sourceUrl": f"https://fetch.flixcloud.cc/{server['serverName']}.mkv",
            "type": "mp4",
            "resolution": "1080p",
            "audio": "sub",
            "priority": 1,
        }

    monkeypatch.setattr(provider, "_resolve_direct_mkv", fake_direct)
    monkeypatch.setattr(provider, "_resolve_flixcloud_hls", lambda server: [{
        "sourceName": f"[Sub] {server['serverName']} (1080P)",
        "link": f"https://cdn.example/{server['serverName']}/1080.m3u8",
        "sourceUrl": f"https://cdn.example/{server['serverName']}/1080.m3u8",
        "type": "hls",
        "resolution": "1080p",
        "audio": "sub",
        "priority": 2,
    }])

    sources = provider.episode_sources("slime", "1", "sub")
    urls = sources["episode"]["sourceUrls"]

    assert [src["sourceName"] for src in urls] == [
        "[Sub] HD-1 Download (1080p)",
        "[Sub] HD-2 Download (1080p)",
        "[Sub] HD-1 (1080P)",
        "[Sub] HD-2 (1080P)",
    ]
    assert all("dub" not in str(src.get("audio", "")).lower() for src in urls)


def test_reanime_dub_mode_filters_to_dub_servers(monkeypatch):
    provider = ReAnimeProvider()
    provider._anime_meta["slime"] = {"anilist_id": 123, "title": "Slime"}
    provider._fetch_json = lambda path, referer=None, timeout=12: {
        "success": True,
        "servers": [
            {
                "serverName": "HD-1",
                "dataType": "sub",
                "dataLink": "https://flixcloud.cc/e/sub1",
            },
            {
                "serverName": "HD-1",
                "dataType": "dub",
                "dataLink": "https://flixcloud.cc/e/dub1",
            },
        ],
    }
    monkeypatch.setattr(provider, "_resolve_direct_mkv", lambda server: None)
    monkeypatch.setattr(provider, "_resolve_flixcloud_hls", lambda server: [])

    sources = provider.episode_sources("slime", "1", "dub")
    urls = sources["episode"]["sourceUrls"]

    assert [src["sourceName"] for src in urls] == ["[Dub] HD-1"]
    assert urls[0]["sourceUrl"] == "https://flixcloud.cc/e/dub1"


def test_reanime_flixcloud_hls_expands_quality_variants(monkeypatch):
    provider = ReAnimeProvider()
    html = '''
    <html><head><link href="/assets/hls.js"></head>
    <script>type:"data",data:{stream_id:"abc",subtitles:[{url:"https://subs.example/en.vtt",language:"English"}],intro_chapter:undefined},uses:"x"</script>
    </html>
    '''
    master = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1920x1080
https://flixcloud.cc/video/1080.m3u8?token=tok
#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=1280x720
https://flixcloud.cc/video/720.m3u8?token=tok
"""

    def fake_fetch(url, headers=None, timeout=12):
        if url == "https://flixcloud.cc/e/sub1":
            return html
        if url == "https://flixcloud.cc/assets/hls.js":
            return "for(var f=[1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16]"
        if url == "https://flixcloud.cc/api/m3u8/token123":
            return '{"encrypted":true}'
        if url.startswith("https://enc-dec.app/api/parse-flixcloud?"):
            return master
        raise AssertionError(url)

    def fake_post(url, payload, timeout=12):
        if url.endswith("type=token"):
            assert "subtitles" not in payload["data"]
            return {"status": 200, "result": {"token": "token123", "context": {"ctx": "value"}}}
        if url.endswith("type=stream"):
            return {
                "status": 200,
                "result": {
                    "stream": "https://flixcloud.cc/master.m3u8?token=tok",
                    "context": {"w_payload": "wpayload"},
                },
            }
        raise AssertionError(url)

    monkeypatch.setattr(provider, "_fetch_page", fake_fetch)
    monkeypatch.setattr(provider, "_post_json", fake_post)

    streams = provider._resolve_flixcloud_hls({
        "serverName": "HD-1",
        "dataType": "sub",
        "dataLink": "https://flixcloud.cc/e/sub1",
    })

    assert [stream["sourceName"] for stream in streams] == ["[Sub] HD-1 (1080P)", "[Sub] HD-1 (720P)"]
    assert streams[0]["flixcloud_w_payload"] == "wpayload"
    assert streams[0]["flixcloud_xor_mask"] == list(range(1, 17))
    assert streams[0]["subtitles"][0]["url"] == "https://subs.example/en.vtt"


def test_flixcloud_proxy_helpers_strip_token_and_xor_segments():
    parent = "https://flixcloud.cc/video/720.m3u8?token=abc"
    assert _flixcloud_ensure_token("https://flixcloud.cc/seg/1.ts", parent).endswith("?token=abc")

    payload = bytes([0x47 ^ 1, 0x41 ^ 2, 0x42 ^ 3])
    assert _xor_bytes(payload, [1, 2, 3]) == b"GAB"
    assert _detect_fake_media_header(b"RIFFxxxxWEBP" + payload) == 12


def test_reanime_title_details_keep_provider_and_tracker_identity(monkeypatch):
    from media_cli.domain.titles import title_provider_key
    provider = ReAnimeProvider()
    monkeypatch.setattr(provider, '_fetch_json', lambda *args, **kwargs: {
        'title': {'english': 'Example'}, 'anilist_id': 123,
        'cover_image': {'large': 'https://example.test/cover.jpg'},
        'description': 'Description',
    })
    title = provider.get_title('example-slug')
    assert title_provider_key(title) == 'reanime'
    assert title['_provider_id'] == 'example-slug'
    assert title['aniListId'] == '123'
    assert title['name'] == 'Example'
    assert title['description'] == 'Description'


def test_saved_anilist_link_routes_real_reanime_details_to_reanime_catalogue(monkeypatch):
    from media_cli.core import storage
    from media_cli.domain import matching
    from media_cli.services import catalog
    provider = ReAnimeProvider()
    requests = []

    def fetch(path, **kwargs):
        requests.append(path)
        if '/episodes?' in path:
            return {'data': [{'episode_number': 1}]}
        return {'title': {'english': 'Example'}, 'anilist_id': 123}

    monkeypatch.setattr(provider, '_fetch_json', fetch)
    monkeypatch.setattr(storage, 'get_al_match', lambda key: {
        '_provider': 'reanime', '_id': 'example-slug', 'match_source': 'id',
    })
    monkeypatch.setattr(catalog, 'require_provider_operation', lambda *args: None)

    def get_provider(key, *args):
        assert key == 'reanime', 'Saved link lost its provider before catalogue fetch'
        return provider

    monkeypatch.setattr(catalog, 'get_provider', get_provider)
    show = matching.match_anilist_show_to_provider({'_id': '123', 'name': 'Example'}, provider_id='reanime')
    result = catalog._perform_catalog_fetch(show, 'sub', None, '')
    assert result == ['1']
    assert requests == ['/api/v1/anime/example-slug', '/api/v1/anime/example-slug/episodes?limit=2000']
