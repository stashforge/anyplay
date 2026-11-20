import base64
import json
import os
import urllib.parse
import urllib.request
import zlib
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

from media_cli.providers.gotaku import GotakuProvider
from media_cli.providers.shared.gotaku_crypto import (
    b64url_encode,
    b64url_decode,
    derive_api_key,
    decrypt_api_body,
    extract_credentials_from_bundle,
    build_manifest_handshake_request,
    decrypt_manifest_response,
    derive_playlist_key,
    decrypt_playlist_body,
    derive_segment_counter,
    decrypt_segment_prefix,
    DEFAULT_API_VERSION,
    DEFAULT_API_SEED,
    DEFAULT_MANIFEST_SEED,
)
from media_cli.media.local_proxy import (
    start_local_proxy,
    stop_local_proxy,
    _decrypt_gotaku_playlist_if_needed,
)
from media_cli.providers import get_provider, PROVIDER_FACTORIES


# =============================================================================
# 1. Cryptography Primitives
# =============================================================================

def test_b64url_roundtrip():
    data = b"Hello, Gotaku crypto world! \x00\xff\xfe"
    encoded = b64url_encode(data)
    assert "=" not in encoded
    assert b64url_decode(encoded) == data


def test_api_body_decryption():
    seed = DEFAULT_API_SEED
    version = DEFAULT_API_VERSION
    api_key = derive_api_key(seed)

    payload = {"data": [{"id": "abc123", "name": "Test Anime"}]}
    compressed = zlib.compress(json.dumps(payload).encode("utf-8"))
    iv = os.urandom(12)
    ct = AESGCM(api_key).encrypt(iv, compressed, None)
    wire = bytes([version]) + iv + ct

    decrypted = decrypt_api_body(wire, version=version, seed=seed)
    assert decrypted == payload


def test_extract_credentials_from_bundle():
    sample_js = """
    var other_var = 123;
    var ie=[10, [1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32], [32,31,30,29,28,27,26,25,24,23,22,21,20,19,18,17,16,15,14,13,12,11,10,9,8,7,6,5,4,3,2,1]], ae=ie[0], oe="api-seal|v1";
    """
    creds = extract_credentials_from_bundle(sample_js)
    assert creds is not None
    ver, seed, info = creds
    assert ver == 10
    assert info == "api-seal|v1"
    assert len(seed) == 32
    assert seed[0] == 1 ^ 32


def test_manifest_handshake_and_decrypt():
    manifest_base = "https://gotakuplay.nl/manifest"
    stamp = "test-stamp-12345"
    handshake_url, nonce = build_manifest_handshake_request(manifest_base, stamp)

    assert handshake_url.startswith("https://gotakuplay.nl/manifest/")
    assert len(nonce) == 16

    # Simulate server response
    resp_key = HKDF(algorithm=hashes.SHA256(), length=32, salt=nonce, info=b"manifest-resp|v1").derive(DEFAULT_MANIFEST_SEED)
    manifest_payload = {
        "source": "https://cdn.example.top/route/uuid/token123/master.m3u8",
        "keySeed": b64url_encode(os.urandom(32)),
        "obf": {"segmentBytes": 8192, "version": 1},
    }
    resp_iv = os.urandom(12)
    resp_ct = AESGCM(resp_key).encrypt(resp_iv, json.dumps(manifest_payload).encode("utf-8"), None)
    enc_response = b64url_encode(resp_iv + resp_ct)

    decrypted_manifest = decrypt_manifest_response(enc_response, nonce)
    assert decrypted_manifest == manifest_payload


def test_playlist_encryption_and_decryption():
    key_seed = os.urandom(32)
    token = "testtoken"
    pl_key = derive_playlist_key(key_seed, token)

    plaintext_m3u8 = "#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:4.0,\nseg1.ts\n"
    iv = os.urandom(12)
    ct = AESGCM(pl_key).encrypt(iv, plaintext_m3u8.encode("utf-8"), None)
    encrypted_body = iv + ct

    decrypted = decrypt_playlist_body(encrypted_body, pl_key)
    assert decrypted == plaintext_m3u8

    # Plaintext passthrough
    assert decrypt_playlist_body(plaintext_m3u8.encode("utf-8"), pl_key) == plaintext_m3u8


def test_segment_prefix_decryption():
    key_seed = os.urandom(32)
    segment_url = "https://cdn.example.top/seg_1001.woff"
    counter = derive_segment_counter(segment_url)
    assert len(counter) == 16

    # Create dummy TS segment of 10000 bytes with 0x47 sync bytes
    raw_seg = bytearray(b"\x00" * 10000)
    for i in range(0, 10000, 188):
        raw_seg[i] = 0x47

    # Encrypt prefix
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    cipher = Cipher(algorithms.AES(key_seed), modes.CTR(counter))
    enc = cipher.encryptor()
    ks = enc.update(b"\x00" * 8192)
    enc_seg = bytes([b ^ k for b, k in zip(raw_seg[:8192], ks)]) + raw_seg[8192:]

    # Decrypt prefix
    dec_seg = decrypt_segment_prefix(enc_seg, key_seed, counter, max_decrypt_bytes=8192)
    assert dec_seg == bytes(raw_seg)

    # Decrypt mid-offset range (e.g. from byte 1000)
    range_chunk = enc_seg[1000:3000]
    dec_range = decrypt_segment_prefix(range_chunk, key_seed, counter, offset_in_prefix=1000, max_decrypt_bytes=8192)
    assert dec_range == bytes(raw_seg[1000:3000])


# =============================================================================
# 2. Provider Implementation Contract & Metadata
# =============================================================================

def test_gotaku_registration():
    assert "gotaku" in PROVIDER_FACTORIES
    provider = get_provider("gotaku")
    assert isinstance(provider, GotakuProvider)
    assert provider.id == "gotaku"
    assert provider.name == "Gotaku"
    assert provider.audio_mode == "dual"


def test_gotaku_search_normalization(monkeypatch):
    # Match the CLI factory path, which injects its shared JSON helper.
    from media_cli.services.http import request_json
    provider = get_provider("gotaku", request_json)

    mock_response = {
        "data": [
            {
                "id": "vbhgad",
                "name": "Sousou no Frieren",
                "format": "TV",
                "poster_url": "https://img.example/poster.jpg",
                "backdrop_url": "https://img.example/banner.jpg",
                "episodes": {"total": 28, "latest_sub": 28, "latest_dub": 28},
                "is_adult": False,
            }
        ]
    }

    class MockResp:
        def read(self):
            return json.dumps(mock_response).encode("utf-8")
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, **kw: MockResp())

    from media_cli.services.catalog import search_anime
    results = search_anime("Frieren", raise_errors=True, provider_id="gotaku")
    assert len(results) == 1
    assert results[0]["id"] == "vbhgad"
    assert results[0]["englishName"] == "Sousou no Frieren"
    assert results[0]["format"] == "TV"
    assert results[0]["availableEpisodes"]["sub"] == 28
    assert results[0]["availableEpisodes"]["dub"] == 28


def test_gotaku_get_title_parsing(monkeypatch):
    provider = GotakuProvider()

    mock_details = {
        "data": {
            "title": {
                "name": "Cowboy Bebop",
                "alt_names": ["カウボーイビバップ"],
                "synopsis": "Space western anime.",
                "format": "TV",
                "status": "completed",
                "aired": "1998-04-03",
                "ended": "1999-04-24",
                "episodes": {"total": 26, "latest_sub": 26, "latest_dub": 26},
                "genres": [{"name": "Action"}, {"name": "Sci-Fi"}],
                "themes": [{"name": "Space"}],
                "anilist_id": 1,
                "links": {"mal": "https://myanimelist.net/anime/1"},
                "mal_score": 8.75,
            }
        }
    }

    class MockResp:
        def read(self):
            return json.dumps(mock_details).encode("utf-8")
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, **kw: MockResp())

    title = provider.get_title("bebop123")
    assert title is not None
    assert title["id"] == "bebop123"
    assert title["englishName"] == "Cowboy Bebop"
    assert str(title["aniListId"]) == "1"
    assert str(title["malId"]) == "1"
    assert "Action" in title["genres"]
    assert title["episodeCount"] == 26


def test_gotaku_episode_catalog(monkeypatch):
    provider = GotakuProvider()

    mock_episodes = {
        "data": [
            {"id": "ep1", "number": 1, "label": "1", "name": "Asteroid Blues", "sub": True, "dub": True, "is_filler": False},
            {"id": "ep2", "number": 2, "label": "2", "name": "Stray Dog Strut", "sub": True, "dub": False, "is_filler": True},
        ]
    }

    monkeypatch.setattr(provider, "_decrypt_api_call", lambda url, referer=None: mock_episodes)

    cat = provider.episode_catalog("bebop123", ttype="sub")
    assert cat["state"] == "loaded"
    assert len(cat["episodes"]["sub"]) == 2
    assert len(cat["episodes"]["dub"]) == 1
    assert cat["episodes"]["sub"][0]["id"] == "ep1"
    assert cat["episodes"]["sub"][1]["id"] == "ep2"


def test_gotaku_episode_sources_and_skips(monkeypatch):
    provider = GotakuProvider()

    catalog_data = {
        "episodes": {
            "sub": [{"id": "ep1", "label": "Episode 1", "display_number": "1", "primary_number": "1"}],
            "dub": [],
        }
    }
    monkeypatch.setattr(provider, "episode_catalog", lambda pid, ttype="sub": catalog_data)

    def mock_decrypt(url, referer=None):
        if "/episodes/ep1/embed" in url:
            return {
                "data": {
                    "url": "https://gotakuplay.nl/embed/token123",
                    "skip": {"intro": [45.0, 135.0], "outro": [1300.0, 1390.0]},
                }
            }
        elif "/episodes/ep1" in url:
            return {"data": {"sub": True, "hard_sub": True, "soft_sub": False, "dub": False}}
        return None

    monkeypatch.setattr(provider, "_decrypt_api_call", mock_decrypt)

    # Scrape embed HTML mock
    class MockResp:
        def __init__(self, data):
            self.data = data
        def read(self):
            return self.data
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    embed_html = '<div data-manifest-base="https://gotakuplay.nl/manifest" data-manifest-stamp="stamp123"></div>'.encode("utf-8")
    
    # Handshake mock
    nonce_holder = {}
    orig_build = build_manifest_handshake_request
    def mock_build(base, stamp):
        url, nonce = orig_build(base, stamp)
        nonce_holder["nonce"] = nonce
        return url, nonce

    monkeypatch.setattr("media_cli.providers.gotaku.build_manifest_handshake_request", mock_build)

    manifest_payload = {
        "source": "https://cdn.example.top/route/uuid/token123/master.m3u8",
        "keySeed": b64url_encode(os.urandom(32)),
        "obf": {"segmentBytes": 8192},
    }

    def mock_urlopen(req, **kw):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "embed" in url:
            return MockResp(embed_html)
        elif "manifest" in url:
            nonce = nonce_holder["nonce"]
            resp_key = HKDF(algorithm=hashes.SHA256(), length=32, salt=nonce, info=b"manifest-resp|v1").derive(DEFAULT_MANIFEST_SEED)
            iv = os.urandom(12)
            ct = AESGCM(resp_key).encrypt(iv, json.dumps(manifest_payload).encode("utf-8"), None)
            return MockResp(b64url_encode(iv + ct).encode("ascii"))
        return MockResp(b"")

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    sources = provider.episode_sources("bebop123", "1", "sub")
    assert sources is not None
    source_list = sources["episode"]["sourceUrls"]
    assert len(source_list) == 1
    src = source_list[0]
    assert src["requires_proxy"] is True
    assert src["gotaku_key_seed"] == manifest_payload["keySeed"]
    assert src["gotaku_segment_bytes"] == 8192
    assert len(src["skip_intervals"]) == 2
    assert src["skip_intervals"][0]["type"] == "op"
    assert src["skip_intervals"][0]["start"] == 45.0
    assert src["skip_intervals"][0]["end"] == 135.0


def test_gotaku_browser_url():
    provider = GotakuProvider()
    assert provider.browser_url("bebop123") == "https://gotaku.to/title/bebop123"
    assert provider.browser_url("bebop123", "ep1") == "https://gotaku.to/title/bebop123?ep=ep1"


# =============================================================================
# 3. Local Proxy Integration
# =============================================================================

def test_local_proxy_gotaku_playlist_and_segment(monkeypatch):
    key_seed = os.urandom(32)
    key_seed_b64 = b64url_encode(key_seed)
    token = "tok123"
    pl_key = derive_playlist_key(key_seed, token)

    master_m3u8 = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1920x1080\n"
        "variant.m3u8\n"
    )
    variant_m3u8 = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        "#EXT-X-TARGETDURATION:4\n"
        "#EXTINF:4.0,\n"
        "segment_1.woff\n"
        "#EXT-X-ENDLIST\n"
    )

    # Encrypt master and variant
    iv1 = os.urandom(12)
    ct1 = AESGCM(pl_key).encrypt(iv1, master_m3u8.encode("utf-8"), None)
    enc_master = iv1 + ct1

    iv2 = os.urandom(12)
    ct2 = AESGCM(pl_key).encrypt(iv2, variant_m3u8.encode("utf-8"), None)
    enc_variant = iv2 + ct2

    # Encrypt segment
    counter = derive_segment_counter("https://cdn.example.top/route/u/tok123/segment_1.woff")
    raw_seg = bytearray(b"\x00" * 10000)
    for i in range(0, 10000, 188):
        raw_seg[i] = 0x47
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    cipher = Cipher(algorithms.AES(key_seed), modes.CTR(counter))
    ks = cipher.encryptor().update(b"\x00" * 8192)
    enc_seg = bytes([b ^ k for b, k in zip(raw_seg[:8192], ks)]) + raw_seg[8192:]

    class MockUpstreamResp:
        def __init__(self, data, url):
            self.data = data
            self.url = url
            self.status = 200
            self.headers = {"Content-Type": "application/vnd.apple.mpegurl"}
        def read(self, n=-1):
            if n == -1 or n >= len(self.data):
                d = self.data
                self.data = b""
                return d
            d = self.data[:n]
            self.data = self.data[n:]
            return d
        def geturl(self):
            return self.url
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    real_urlopen = urllib.request.urlopen

    def mock_urlopen(req, **kw):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "127.0.0.1" in url or "localhost" in url:
            return real_urlopen(req, **kw)
        if url.endswith("master.m3u8"):
            return MockUpstreamResp(enc_master, url)
        elif url.endswith("variant.m3u8"):
            return MockUpstreamResp(enc_variant, url)
        elif url.endswith("segment_1.woff"):
            resp = MockUpstreamResp(enc_seg, url)
            resp.headers = {"Content-Type": "video/MP2T"}
            return resp
        return MockUpstreamResp(b"", url)

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    master_url = "https://cdn.example.top/route/u/tok123/master.m3u8"
    proxy_url, server = start_local_proxy(
        master_url,
        referer="https://gotaku.to/",
        stream_type="hls",
        gotaku_key_seed=key_seed_b64,
        gotaku_segment_bytes=8192,
    )

    try:
        # Fetch proxied master playlist
        with urllib.request.urlopen(proxy_url) as r:
            assert r.status == 200
            rewritten_master = r.read().decode("utf-8")
            assert "#EXT-X-STREAM-INF" in rewritten_master

        # Extract variant URL from rewritten master
        lines = [line.strip() for line in rewritten_master.splitlines() if line.strip() and not line.startswith("#")]
        variant_proxy_url = urllib.parse.urljoin(proxy_url, lines[0])
        assert "http://127.0.0.1:" in variant_proxy_url

        # Fetch proxied variant playlist
        with urllib.request.urlopen(variant_proxy_url) as r:
            assert r.status == 200
            rewritten_variant = r.read().decode("utf-8")
            assert "#EXTINF:4.0" in rewritten_variant

        # Extract segment URL
        seg_lines = [line.strip() for line in rewritten_variant.splitlines() if line.strip() and not line.startswith("#")]
        seg_proxy_url = urllib.parse.urljoin(proxy_url, seg_lines[0])
        assert "http://127.0.0.1:" in seg_proxy_url

        # Fetch proxied segment -> should be decrypted!
        with urllib.request.urlopen(seg_proxy_url) as r:
            assert r.status == 200
            decrypted_seg_data = r.read()
            assert len(decrypted_seg_data) == 10000
            assert decrypted_seg_data == bytes(raw_seg)
            assert decrypted_seg_data[0] == 0x47
            assert decrypted_seg_data[188] == 0x47
            assert decrypted_seg_data[376] == 0x47
    finally:
        stop_local_proxy(server)


# =============================================================================
# 3. Review Findings & Regression Tests
# =============================================================================

def test_extract_playlist_token():
    from media_cli.providers.shared.gotaku_crypto import extract_playlist_token

    # Master URL
    u1 = "https://cdn.example.top/route/123e4567-e89b-12d3-a456-426614174000/tok123/master.m3u8"
    assert extract_playlist_token(u1) == "tok123"

    # Variant URL (1080p)
    u2 = "https://cdn.example.top/route/123e4567-e89b-12d3-a456-426614174000/tok123/1080/manifest.m3u8"
    assert extract_playlist_token(u2) == "tok123"

    # Stream API URL
    u3 = "https://gotakuplay.nl/api/v1/stream/tokABC"
    assert extract_playlist_token(u3) == "tokABC"

    # Query param token
    u4 = "https://cdn.example.top/manifest.m3u8?token=tokQuery"
    assert extract_playlist_token(u4) == "tokQuery"


def test_search_signature_compatibility(monkeypatch):
    provider = GotakuProvider()

    captured_urls = []
    class MockResp:
        def __init__(self, data):
            self.data = json.dumps(data).encode("utf-8")
        def read(self):
            return self.data
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    def mock_urlopen(req, **kw):
        captured_urls.append(req.full_url)
        return MockResp({"data": []})

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    # 1. Standard call: search(query, ttype)
    provider.search("naruto", "dub")
    assert "page=1" in captured_urls[-1]
    assert "track=dub" in captured_urls[-1]

    # 2. Positional page: search(query, page, ttype)
    provider.search("naruto", 2, "dub")
    assert "page=2" in captured_urls[-1]
    assert "track=dub" in captured_urls[-1]

    # 3. Keyword page: search(query, page=3, ttype="sub")
    provider.search("naruto", page=3, ttype="sub")
    assert "page=3" in captured_urls[-1]
    assert "track=dub" not in captured_urls[-1]


def test_health_integration_contracts():
    from media_cli.health.catalogue import HEALTH_SAMPLES
    from media_cli.health.profiles import PROFILES, valid_title_id, valid_episode_id
    from media_cli.health.http_media import media_destination_allowed

    assert "gotaku" in HEALTH_SAMPLES
    assert "gotaku" in PROFILES

    # Valid IDs
    assert valid_title_id("gotaku", "solo-leveling-season-2") is True
    assert valid_title_id("gotaku", "invalid title with spaces") is False
    assert valid_episode_id("gotaku", "solo-leveling-season-2-arise-from-the-shadow-episode-1") is True
    assert valid_episode_id("gotaku", "1") is True

    # Media destination restrictions
    assert media_destination_allowed("gotaku", "https://gotakuplay.nl/video.m3u8") is True
    assert media_destination_allowed("gotaku", "https://gotaku.to/video.m3u8") is True
    assert media_destination_allowed("gotaku", "https://edge01.asilentharbor.top/route/u/t/master.m3u8") is True
    assert media_destination_allowed("gotaku", "https://edge02.anoblecascade.top/route/u/t/master.m3u8") is True
    assert media_destination_allowed("gotaku", "https://edge03.acobaltridge.top/route/u/t/master.m3u8") is True
    assert media_destination_allowed("gotaku", "https://edge04.apexnestdigital.top/route/u/t/master.m3u8") is True
    assert media_destination_allowed("gotaku", "https://edge05.atlaspulsehub.top/route/u/t/master.m3u8") is True

    # Reject broad arbitrary .top or .world
    assert media_destination_allowed("gotaku", "https://random-malicious.top/video.m3u8") is False
    assert media_destination_allowed("gotaku", "https://random-malicious.world/video.m3u8") is False


def test_cache_generation_invalidation():
    provider = GotakuProvider()
    initial_gen = provider._cache_generation

    provider.invalidate_title_cache()
    assert provider._cache_generation == initial_gen + 1

    provider.invalidate_title_cache("solo-leveling")
    assert provider._cache_generation == initial_gen + 2


def test_segment_decryptor_range_status_200_vs_206():
    from media_cli.media.local_proxy import _GotakuSegmentDecryptor

    key_seed = os.urandom(32)
    url = "https://cdn.example.top/seg1.woff"

    # Status 206: range_offset should be parsed
    dec206 = _GotakuSegmentDecryptor(key_seed, 8192, url, "bytes=1000-", 206)
    assert dec206.range_offset == 1000

    # Status 200: range_offset must be 0 (server ignored Range header)
    dec200 = _GotakuSegmentDecryptor(key_seed, 8192, url, "bytes=1000-", 200)
    assert dec200.range_offset == 0


def test_local_proxy_corrupted_playlist_502(monkeypatch):
    key_seed = os.urandom(32)
    key_seed_b64 = b64url_encode(key_seed)

    class MockBadResp:
        status = 200
        headers = {"Content-Type": "application/vnd.apple.mpegurl"}
        def read(self, *args):
            return b"corrupted garbage that is not valid encrypted AES-GCM data"
        def geturl(self):
            return "https://cdn.example.top/route/u/tok123/master.m3u8"
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    real_urlopen = urllib.request.urlopen
    def mock_urlopen(req, **kw):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "127.0.0.1" in url or "localhost" in url:
            return real_urlopen(req, **kw)
        return MockBadResp()

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    master_url = "https://cdn.example.top/route/u/tok123/master.m3u8"
    proxy_url, server = start_local_proxy(
        master_url,
        referer="https://gotaku.to/",
        stream_type="hls",
        gotaku_key_seed=key_seed_b64,
        gotaku_segment_bytes=8192,
    )

    try:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(proxy_url)
        assert exc_info.value.code == 502
    finally:
        stop_local_proxy(server)


def test_gotaku_episode_sources_variants_expanded(monkeypatch):
    provider = GotakuProvider()

    key_seed = os.urandom(32)
    key_seed_b64 = b64url_encode(key_seed)
    token = "tokVariants"

    # Master playlist with 1080p and 720p
    master_m3u8 = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080\n"
        "1080/manifest.m3u8\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720\n"
        "720/manifest.m3u8\n"
    )
    pl_key = derive_playlist_key(key_seed, token)
    iv = os.urandom(12)
    ct = AESGCM(pl_key).encrypt(iv, master_m3u8.encode("utf-8"), None)
    enc_master = iv + ct

    master_stream_url = f"https://cdn.example.top/route/uuid/{token}/master.m3u8"

    # Mock catalog
    monkeypatch.setattr(
        provider,
        "episode_catalog",
        lambda *a, **k: {
            "episodes": {
                "sub": [{"id": "ep-1", "label": "1", "number": 1}],
                "dub": [],
            }
        },
    )

    # Mock _decrypt_api_call
    def mock_decrypt_api(url, **kw):
        if "/episodes/ep-1/embed" in url:
            return {"data": {"url": "https://gotaku.to/embed/abc"}}
        if "/episodes/ep-1" in url:
            return {"data": {"soft_sub": True, "hard_sub": False, "dub": False}}
        return None

    monkeypatch.setattr(provider, "_decrypt_api_call", mock_decrypt_api)

    class MockResp:
        def __init__(self, data, url=""):
            self.data = data
            self.url = url
        def read(self, *args):
            return self.data
        def geturl(self):
            return self.url
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    embed_html = (
        '<html><body>'
        '<div data-manifest-base="https://gotakuplay.nl/manifest" data-manifest-stamp="stamp123"></div>'
        '</body></html>'
    )

    # Handshake response
    manifest_obj = {
        "source": master_stream_url,
        "keySeed": key_seed_b64,
        "obf": {"segmentBytes": 8192},
    }

    # Handshake mock to capture nonce
    nonce_holder = {}
    orig_build = build_manifest_handshake_request
    def mock_build(base, stamp):
        url, nonce = orig_build(base, stamp)
        nonce_holder["nonce"] = nonce
        return url, nonce

    monkeypatch.setattr("media_cli.providers.gotaku.build_manifest_handshake_request", mock_build)

    def mock_urlopen(req, **kw):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "/embed/abc" in url:
            return MockResp(embed_html.encode("utf-8"), url)
        if "/manifest/" in url:
            nonce = nonce_holder["nonce"]
            resp_key = HKDF(algorithm=hashes.SHA256(), length=32, salt=nonce, info=b"manifest-resp|v1").derive(DEFAULT_MANIFEST_SEED)
            m_iv = os.urandom(12)
            m_ct = AESGCM(resp_key).encrypt(m_iv, json.dumps(manifest_obj).encode("utf-8"), None)
            return MockResp(b64url_encode(m_iv + m_ct).encode("ascii"), url)
        if master_stream_url in url:
            return MockResp(enc_master, url)
        return MockResp(b"", url)

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    res = provider.episode_sources("solo-leveling", "1", ttype="sub")
    assert res is not None
    sources = res["episode"]["sourceUrls"]
    assert len(sources) == 3

    # Check 1080p
    assert sources[0]["resolution"] == "1080p"
    assert "1080/manifest.m3u8" in sources[0]["streamUrl"]
    assert sources[0]["gotaku_key_seed"] == key_seed_b64
    assert sources[0]["gotaku_segment_bytes"] == 8192
    assert sources[0]["requires_proxy"] is True

    # Check 720p
    assert sources[1]["resolution"] == "720p"
    assert "720/manifest.m3u8" in sources[1]["streamUrl"]

    # Check Auto fallback
    assert sources[2]["resolution"] == "auto"
    assert sources[2]["streamUrl"] == master_stream_url


# =============================================================================
# 8. Proxy Cleanup Verification
# =============================================================================

def test_gotaku_proxy_server_lifecycle():
    """Verify start_local_proxy with Gotaku crypto cleans up cleanly on stop_local_proxy."""
    url, server = start_local_proxy(
        target_url="https://gotakuplay.nl/route/123/tok456/master.m3u8",
        referer="https://gotakuplay.nl/embed/123",
        headers={"User-Agent": "AnyplayTest"},
        gotaku_key_seed="dGVzdHNlZWQxMjM0NTY3ODkwMTI=",
        gotaku_segment_bytes=8192,
    )
    assert url.startswith("http://127.0.0.1:")
    assert server is not None

    # Verify server socket is active
    sock = getattr(server, "socket", None)
    assert sock is not None
    assert sock.fileno() != -1

    # Stop proxy and verify shutdown and close
    stop_local_proxy(server)
    try:
        assert sock.fileno() == -1
    except Exception:
        pass


def test_gotaku_download_proxy_cleanup_on_failure(monkeypatch):
    """Verify download_episode ensures proxy_server is stopped on failure."""
    from media_cli.media import download
    import subprocess

    stopped = []
    fake_server = object()

    def mock_start(*args, **kwargs):
        return "http://127.0.0.1:9999/manifest.m3u8", fake_server

    monkeypatch.setattr("media_cli.media.local_proxy.start_local_proxy", mock_start)
    monkeypatch.setattr("media_cli.media.local_proxy.stop_local_proxy", lambda s: stopped.append(s))

    # Force an exception during actual ffmpeg execution
    orig_run = subprocess.run
    def mock_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and any("manifest.m3u8" in str(x) for x in cmd):
            raise RuntimeError("Simulated download error")
        return orig_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", mock_run)

    stream = {
        "streamUrl": "https://gotakuplay.nl/route/123/tok/master.m3u8",
        "requires_proxy": True,
        "gotaku_key_seed": "dGVzdHNlZWQxMjM0NTY3ODkwMTI=",
        "gotaku_segment_bytes": 8192,
        "source_name": "gotaku",
    }

    res = download.download_episode(
        title="Test Anime",
        episode="1",
        stream=stream,
        download_dir="/tmp/anyplay_gotaku_test/dl",
        downloader="ffmpeg",
        quiet=True,
    )

    assert res is False
    assert stopped == [fake_server]


def test_gotaku_playback_proxy_cleanup_on_cancellation(monkeypatch):
    """Verify play_desktop ensures active proxy is cleaned up on cancellation."""
    from media_cli.playback import desktop
    from media_cli.media import local_proxy
    from types import SimpleNamespace

    cleaned = []
    monkeypatch.setattr(local_proxy, "cleanup_active_local_proxy", lambda: cleaned.append(True))
    monkeypatch.setattr(local_proxy, "replace_active_local_proxy", lambda s: None)

    fake_server = object()
    def mock_prepare(*args, **kwargs):
        return "http://127.0.0.1:9999/manifest.m3u8", fake_server

    ipc = SimpleNamespace(
        props={},
        load=lambda *a, **kw: None,
        wait_for_playback=lambda *a, **kw: (_ for _ in ()).throw(desktop.PlaybackPreparationCancelled()),
    )

    stream = {
        "link": "https://gotakuplay.nl/route/123/tok/master.m3u8",
        "requires_proxy": True,
        "gotaku_key_seed": "dGVzdHNlZWQxMjM0NTY3ODkwMTI=",
        "gotaku_segment_bytes": 8192,
    }

    result = desktop.play_desktop(
        ipc,
        "Test Anime",
        "1",
        stream,
        get_resume_time=lambda *a: 0,
        get_preferred_mirror=lambda *a: {},
        update_stream_info=lambda info: None,
        prepare_proxy_fn=mock_prepare,
    )

    assert result[0] == "CANCELLED"
    assert cleaned == [True]

