import io
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from media_cli.media.provider_skips import normalize_provider_skips
from media_cli.providers.anikoto import AnikotoProvider

WATCH = "https://anikototv.to/watch/selected/ep-1"
MASTER = "https://media.test/master.m3u8"
NATIVE = normalize_provider_skips({"op": {"start": 10, "end": 90}, "ed": {"start": 1200, "end": 1290}})


def transport(monkeypatch, response_for):
    def fetch(req, **kwargs):
        payload = response_for(req.full_url)
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        return io.BytesIO(payload.encode())

    monkeypatch.setattr("urllib.request.urlopen", fetch)


def server_response(payload, *, host="megaplay.buzz", retry=None):
    def response(url):
        path = urlsplit(url).path
        if path == "/ajax/server":
            return {"result": {"url": f"https://{host}/embed?s=primary"}}
        if path == "/embed":
            return '<div data-id="123"></div>'
        if "getSources" in path:
            return retry if retry is not None and parse_qs(urlsplit(url).query).get("s") == ["tcdn"] else payload
        if url == MASTER:
            return "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100,RESOLUTION=1280x720\n720.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=50,RESOLUTION=640x360\n360.m3u8\n"
        pytest.fail("Unexpected request")

    return response


@pytest.mark.parametrize("host", ["megaplay.buzz", "vidtube.site"])
def test_native_skips_reach_all_qualities_and_shared_resolver(monkeypatch, host):
    from media_cli.media.resolver import resolve_source

    payload = {"sources": [{"file": MASTER}], "intro": {"start": 10, "end": 90},
               "outro": {"start": 1200, "end": 1290}}
    transport(monkeypatch, server_response(payload, host=host))
    entries = AnikotoProvider()._extract_single_server("HD-2", "link", WATCH, "Softsub")
    assert len(entries) == 3
    for entry in entries:
        assert entry["skip_intervals"] == NATIVE
        assert resolve_source(entry, silent=True)[0]["skip_intervals"] == NATIVE


@pytest.mark.parametrize("decoded", [{"file": MASTER}, [{"file": MASTER}]])
def test_encrypted_fallback_retains_outer_skips_and_direct_file(monkeypatch, decoded):
    provider = AnikotoProvider()
    monkeypatch.setattr(provider, "_episode_identity", lambda *a: (None, None, None))
    monkeypatch.setattr("media_cli.providers.anikoto._decrypt_megaplay_enc", lambda enc: decoded)
    payload = {"enc": "fake", "intro": {"start": 0, "end": 0}, "outro": {"start": 4779, "end": 4870}}

    def response(url):
        if "/stream/ani/" in url:
            return '<div data-id="123"></div>'
        return payload

    transport(monkeypatch, response)
    result = provider.episode_sources("150672", "1", "sub")
    assert result is not None
    source = result["episode"]["sourceUrls"][0]
    assert source["streamUrl"] == MASTER
    assert source["skip_intervals"] == normalize_provider_skips({"ed": payload["outro"]})


@pytest.mark.parametrize("retry_has_skips", [True, False])
def test_cdn_retry_never_keeps_replaced_sources_skip_ranges(monkeypatch, retry_has_skips):
    initial = {"enc": "old", "intro": {"start": 10, "end": 90}}
    retry = {"enc": "new"}
    if retry_has_skips:
        retry["outro"] = {"start": 1200, "end": 1290}
    transport(monkeypatch, server_response(initial, retry=retry))
    monkeypatch.setattr("media_cli.providers.anikoto._decrypt_megaplay_enc",
                        lambda enc: {"file": "https://fetch.nexabloom.top/blocked" if enc == "old" else MASTER})
    entries = AnikotoProvider()._extract_single_server("HD-2", "link", WATCH, "Softsub")
    expected = normalize_provider_skips({"ed": retry["outro"]}) if retry_has_skips else []
    assert len(entries) == 3
    assert all(entry.get("skip_intervals", []) == expected for entry in entries)


def test_decrypted_ranges_override_outer_for_same_response(monkeypatch):
    transport(monkeypatch, server_response({"enc": "fake", "intro": {"start": 10, "end": 90}}))
    monkeypatch.setattr("media_cli.providers.anikoto._decrypt_megaplay_enc",
                        lambda enc: {"file": MASTER, "intro": {"start": 20, "end": 100}})
    entries = AnikotoProvider()._extract_single_server("HD-2", "link", WATCH, "Softsub")
    assert entries[0]["skip_intervals"] == normalize_provider_skips({"op": {"start": 20, "end": 100}})


def test_invalid_native_ranges_do_not_create_skips(monkeypatch):
    transport(monkeypatch, server_response({"sources": [{"file": MASTER}],
        "intro": {"start": True, "end": 90}, "outro": {"start": 90, "end": 0}}))
    entries = AnikotoProvider()._extract_single_server("HD-2", "link", WATCH, "Softsub")
    assert len(entries) == 3
    assert all(not entry.get("skip_intervals") for entry in entries)
