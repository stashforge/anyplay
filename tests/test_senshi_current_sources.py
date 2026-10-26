from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from curl_cffi import CurlOpt

from media_cli.extractors.senshi_runtime import SenshiRuntimeError
from media_cli.providers.senshi import HEADERS, Senshi
from media_cli.services.browser_access.operation import browser_operation
from media_cli.services.browser_access.profiles import BrowserAccessCancelled

MASTER = '''#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="Japanese",LANGUAGE="ja",DEFAULT=YES,URI="ja.jpg"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="English",LANGUAGE="en",DEFAULT=NO,URI="en.jpg"
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080,AUDIO="audio"
1080.jpg
#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=854x480,AUDIO="audio"
480.jpg
'''

UNTRUSTED = [
    "file:///etc/passwd", "https://localhost/a", "https://127.0.0.1/a",
    "https://[::1]/a", "https://unrelated.example/a", "https://bcdn1.se/a",
    "https://s-90.bcdn1.se.evil.example/a", "https://s.vidcloud.se.evil/a",
    "https://s-98.bcdn3.se.evil.example/a",
    "https://s-95.bcdn2.se.evil.example/a",
    "http://s-90.bcdn1.se/a", "https://user:pass@s-90.bcdn1.se/a",
    "https://s-90.bcdn1.se:444/a", "https://s-90.bcdn1.se/a\nsecret",
    "https://s-90.bcdn1.se/a\x7f", "https://s-90.bcdn1.se/a\\b",
    "https://s-90.bcdn1.se/a%0d%0aHeader", "https://s-90.bcdn1.se/a#fragment",
]


@pytest.fixture(autouse=True)
def no_network():
    with patch("urllib.request.urlopen", side_effect=AssertionError("Unexpected network")), \
            patch("curl_cffi.requests.Session.request", side_effect=AssertionError("Unexpected network")):
        yield


@pytest.mark.parametrize("url", UNTRUSTED)
@pytest.mark.parametrize("field", ["source", "file"])
def test_untrusted_runtime_source_is_rejected_without_playlist_fetch(url, field):
    provider = Senshi()
    with patch.object(provider, "_resolve_remote_sources", return_value=[{field: url}]), \
            patch.object(provider, "_read_playlist") as read:
        assert provider._extract_embed_sources([{"remote_source_id": 1}], False) == []
    read.assert_not_called()


@pytest.mark.parametrize("url", UNTRUSTED)
def test_untrusted_source_is_not_cached_or_fetched(url):
    provider = Senshi()
    with patch("media_cli.extractors.senshi_runtime.resolve_vidcloud", return_value=[{"source": url}]):
        assert provider._resolve_remote_sources(1) == []
    assert not provider._remote_sources_cache


@pytest.mark.parametrize("url", UNTRUSTED)
@pytest.mark.parametrize("child", ["1080.jpg", "ja.jpg"])
def test_untrusted_selected_video_or_audio_is_not_returned(url, child):
    provider = Senshi()
    with patch.object(provider, "_read_playlist", return_value=("https://s-90.bcdn1.se/master", MASTER.replace(child, url))):
        variants = provider._playlist_variants("https://s-90.bcdn1.se/master", "auto", False)
    assert all(url not in (row["url"], row.get("audio_url")) for row in variants)
    if child == "ja.jpg":
        assert variants == []


@pytest.mark.parametrize("url", UNTRUSTED)
def test_untrusted_tracks_are_not_returned(url):
    assert Senshi._subtitles([{"file": url, "label": "English"}], False) == []


def response(url, status=200, location=None):
    return SimpleNamespace(url=url, status_code=status, headers={"Location": location} if location is not None else {})


@pytest.mark.parametrize("url", UNTRUSTED)
def test_untrusted_playlist_is_rejected_before_transport(url):
    with patch("media_cli.extractors.senshi_runtime.fetch_bytes") as fetch:
        with pytest.raises(SenshiRuntimeError):
            Senshi()._read_playlist(url)
    fetch.assert_not_called()


@pytest.mark.parametrize("location", UNTRUSTED + ["//localhost/a", "/a\nsecret", ""])
def test_redirect_destination_is_rejected_before_next_fetch(location):
    url = "https://s-90.bcdn1.se/master"
    with patch("media_cli.extractors.senshi_runtime.fetch_bytes", return_value=(response(url, 302, location), b"")) as fetch:
        with pytest.raises(SenshiRuntimeError):
            Senshi()._read_playlist(url)
    assert fetch.call_count == 1


def test_playlist_transport_has_one_deadline_size_limit_shared_agent_and_no_credentials():
    url = "https://s-90.bcdn1.se/master"
    final = "https://cdn.vidcloud.se/final/master"
    cancel = Event()
    payload = b"#EXTM3U\n#EXTINF:5,\na.ts\n"
    with browser_operation(cancel=cancel), \
            patch("media_cli.providers.senshi.time.monotonic", return_value=100), \
            patch("curl_cffi.requests.Session") as session, \
            patch("media_cli.extractors.senshi_runtime.fetch_bytes", side_effect=[
                (response(url, 302, final), b""), (response(final), payload),
            ]) as fetch:
        assert Senshi()._read_playlist(url) == (final, payload.decode())
    session.assert_called_once_with(impersonate="chrome", trust_env=False,
                                    curl_options={CurlOpt.IPRESOLVE: 1, CurlOpt.PROXY: ""})
    assert fetch.call_count == 2
    for call in fetch.call_args_list:
        args, kwargs = call
        assert args[0] is session.return_value.__enter__.return_value
        assert args[1] == "GET" and args[4] is None
        assert args[5:7] == (110, cancel)
        assert args[3] == {**HEADERS, "Referer": "https://senshi.to/", "Origin": "https://senshi.to"}
        assert kwargs == {"limit": 1048576, "allow_redirect_response": True}


def test_relative_redirects_stop_after_three_hops():
    url = "https://s-90.bcdn1.se/master"
    def redirect(session, method, current, *args, **kwargs):
        return response(current, 302, "next"), b""
    with patch("media_cli.extractors.senshi_runtime.fetch_bytes", side_effect=redirect) as fetch:
        with pytest.raises(SenshiRuntimeError):
            Senshi()._read_playlist(url)
    assert fetch.call_count == 4
    assert fetch.call_args_list[1].args[2] == "https://s-90.bcdn1.se/next"


def test_redirects_do_not_restart_elapsed_deadline():
    url = "https://s-90.bcdn1.se/master"
    with patch("media_cli.providers.senshi.time.monotonic", side_effect=[100, 100, 111]), \
            patch("media_cli.extractors.senshi_runtime.fetch_bytes", return_value=(response(url, 302, "/next"), b"")) as fetch:
        with pytest.raises(SenshiRuntimeError, match="timed out"):
            Senshi()._read_playlist(url)
    assert fetch.call_count == 1


@pytest.mark.parametrize("during_fetch", [False, True])
def test_playlist_cancellation_propagates(during_fetch):
    url = "https://s-90.bcdn1.se/master"
    cancel = Event()
    if not during_fetch:
        cancel.set()
    def cancel_fetch(*args, **kwargs):
        cancel.set()
        raise SenshiRuntimeError("Senshi extraction cancelled")
    with browser_operation(cancel=cancel), \
            patch("media_cli.extractors.senshi_runtime.fetch_bytes", side_effect=cancel_fetch) as fetch:
        with pytest.raises((SenshiRuntimeError, BrowserAccessCancelled), match="cancelled"):
            Senshi()._playlist_variants(url, "auto", False)
    assert fetch.call_count == int(during_fetch)


@pytest.mark.parametrize("payload", [b"", b"<html>blocked</html>", b"garbage", b"#EXTM3U", b"x" * 1048577],
                         ids=["empty", "html", "garbage", "empty-playlist", "oversized"])
def test_invalid_playlist_never_advertises_dead_auto_mirror(payload):
    url = "https://s-90.bcdn1.se/master"
    with patch("media_cli.extractors.senshi_runtime.fetch_bytes", return_value=(response(url), payload)):
        assert Senshi()._playlist_variants(url, "auto", False) == []


def test_failed_playlist_never_advertises_dead_auto_mirror():
    with patch.object(Senshi, "_read_playlist", side_effect=SenshiRuntimeError("Senshi runtime transport failed")):
        assert Senshi()._playlist_variants("https://s-90.bcdn1.se/master", "auto", False) == []


def test_transport_final_url_is_validated_and_used_exactly():
    url = "https://s-90.bcdn1.se/master"
    with patch("media_cli.extractors.senshi_runtime.fetch_bytes", return_value=(response("file:///etc/passwd"), b"#EXTM3U")):
        with pytest.raises(SenshiRuntimeError):
            Senshi()._read_playlist(url)


@pytest.mark.parametrize("url", [
    "https://s.vidcloud.se/master", "https://cdn.vidcloud.se/master",
    "https://s-90.bcdn1.se:443/master", "https://a.b.bcdn1.se/master",
    "https://s-98.bcdn3.se/master",
    "https://s-95.bcdn2.se/master",
])
def test_current_trusted_families_accept_media_playlists_and_tracks(url):
    payload = b"#EXTM3U\n#EXTINF:5,\na.ts\n"
    with patch("media_cli.extractors.senshi_runtime.fetch_bytes", return_value=(response(url), payload)):
        assert Senshi()._playlist_variants(url, "480p", False) == [{"url": url, "resolution": "480p"}]
    assert Senshi._subtitles([{"file": url, "label": "English"}], False)[0]["url"] == url


def test_all_candidate_shapes_filter_untrusted_urls_before_language_selection():
    entry = {"source": [
        {"src": "file:///etc/passwd", "label": "sub"},
        {"url": "https://localhost/a", "label": "sub"},
        {"file": "https://unrelated.example/a", "label": "sub"},
        {"url": "https://s.vidcloud.se/master", "label": "both"},
    ]}
    provider = Senshi()
    with patch.object(provider, "_resolve_remote_sources", return_value=[entry]), \
            patch.object(provider, "_read_playlist", return_value=("https://s.vidcloud.se/master", MASTER)) as read:
        rows = provider._extract_embed_sources([{"remote_source_id": 1}], False)
    assert [row["resolution"] for row in rows] == ["1080p", "480p"]
    read.assert_called_once_with("https://s.vidcloud.se/master")


def test_encrypted_playlist_still_decrypts_before_quality_and_audio_selection():
    url = "https://s-90.bcdn1.se/master"
    with patch("media_cli.extractors.senshi_runtime.fetch_bytes", return_value=(response(url), b"EM3U8v1:synthetic")), \
            patch("media_cli.media.decryption.decrypt_em3u8", return_value=MASTER):
        variants = Senshi()._playlist_variants(url, "auto", True)
    assert [row["resolution"] for row in variants] == ["1080p", "480p"]
    assert all(row["audio_url"] == "https://s-90.bcdn1.se/en.jpg" for row in variants)


@pytest.mark.parametrize("status", [304, 400, 403, 500])
def test_unsuccessful_http_playlist_never_advertises_a_source(status):
    url = "https://s-90.bcdn1.se/master"
    with patch("media_cli.extractors.senshi_runtime.fetch_bytes", return_value=(response(url, status), MASTER.encode())):
        assert Senshi()._playlist_variants(url, "auto", False) == []


def test_playlist_cancellation_exception_propagates_without_context_event():
    with patch.object(Senshi, "_read_playlist", side_effect=SenshiRuntimeError("Senshi extraction cancelled")):
        with pytest.raises(SenshiRuntimeError, match="cancelled"):
            Senshi()._playlist_variants("https://s-90.bcdn1.se/master", "auto", False)


def test_current_sources_select_language_and_keep_external_audio():
    provider = Senshi()
    embeds = [{"remote_source_id": 3731, "status": label} for label in ("Dub", "HardSub")]
    entries = [{"source": [{"src": "https://s-90.bcdn1.se/master.txt", "label": "both"}],
                "tracks": [{"vtt_url": "https://s-90.bcdn1.se/en.vtt", "label": "English"},
                           {"vtt_url": "https://s-90.bcdn1.se/dub.vtt", "label": "English (Dub)"},
                           {"vtt_url": "https://s-90.bcdn1.se/chapter.vtt", "label": "chapter"}]}]
    with patch.object(provider, "_fetch_json", return_value=embeds), \
            patch.object(provider, "_resolve_remote_sources", return_value=entries) as resolve, \
            patch.object(provider, "_read_playlist", return_value=("https://s-90.bcdn1.se/final/master.txt", MASTER)):
        sub = provider.episode_sources("52991", "1", "sub")["episode"]["sourceUrls"]
        dub = provider.episode_sources("52991", "1", "dub")["episode"]["sourceUrls"]
    assert [s["resolution"] for s in sub] == ["1080p", "480p"]
    assert sub[0]["audio_url"] == "https://s-90.bcdn1.se/final/ja.jpg"
    assert dub[0]["audio_url"] == "https://s-90.bcdn1.se/final/en.jpg"
    assert sub[0]["streamUrl"] == "https://s-90.bcdn1.se/final/1080.jpg"
    assert sub[0]["ip_version"] == dub[0]["ip_version"] == 4
    assert [t["label"] for t in sub[0]["subtitles"]] == ["English"]
    assert [t["label"] for t in dub[0]["subtitles"]] == ["English (Dub)"]
    assert resolve.call_count == 2  # Only requested language, no eager opposite extraction.


def test_failed_source_fetch_is_retryable_and_never_advertises_obsolete_fallback():
    provider = Senshi()
    row = {"remote_source_id": 3731, "status": "HardSub", "url": "https://cdn1.anidap.biz/obsolete/playlist.m3u8"}
    with patch.object(provider, "_fetch_json", side_effect=[[], [row]]) as fetch, \
            patch.object(provider, "_resolve_remote_sources", return_value=[]):
        assert provider.episode_sources("52991", "1") is None
        assert provider.episode_sources("52991", "1") is None
    assert fetch.call_count == 2


def test_source_cache_is_instance_scoped():
    a, b = Senshi(), Senshi()
    a._sources_cache["synthetic"] = "other-session"
    assert "synthetic" not in b._sources_cache


def test_positive_source_cache_expires_and_is_scoped_to_domain():
    provider = Senshi()
    rows = [{"status": "HardSub", "url": "https://s-90.bcdn1.se/one.m3u8"}]
    with patch.object(provider, "_fetch_json", return_value=rows) as fetch, \
            patch("media_cli.providers.senshi.time.monotonic", return_value=0) as now:
        first = provider.episode_sources("1", "1")
        now.return_value = 119
        assert provider.episode_sources("1", "1") == first
        assert fetch.call_count == 1
        now.return_value = 121
        rows[0]["url"] = "https://s-90.bcdn1.se/two.m3u8"
        assert provider.episode_sources("1", "1")["episode"]["sourceUrls"][0]["streamUrl"].endswith("two.m3u8")
        provider.domains = ["https://other.senshi.to"]
        provider.episode_sources("1", "1")
        assert fetch.call_count == 3


def test_source_cache_is_bounded():
    provider = Senshi()
    with patch.object(provider, "_fetch_json", return_value=[{"url": "https://s-90.bcdn1.se/v.m3u8"}]):
        for episode in range(65):
            assert provider.episode_sources("1", str(episode))
    assert len(provider._sources_cache) <= 32


def test_remote_cache_reuses_handshake_across_languages_and_expires():
    provider = Senshi()
    entries = [{"source": "https://s-90.bcdn1.se/master.txt"}]
    rows = [{"remote_source_id": 3731, "status": status} for status in ("Dub", "HardSub")]
    with patch.object(provider, "_fetch_json", return_value=rows), \
            patch("media_cli.extractors.senshi_runtime.resolve_vidcloud", return_value=entries) as resolve, \
            patch.object(provider, "_read_playlist", return_value=("https://s-90.bcdn1.se/master.txt", MASTER)), \
            patch("media_cli.providers.senshi.time.monotonic", return_value=0) as now:
        assert provider.episode_sources("1", "1", "sub")
        assert provider.episode_sources("1", "1", "dub")
        assert resolve.call_count == 1
        now.return_value = 121
        assert provider.episode_sources("1", "1", "sub")
        assert resolve.call_count == 1
        now.return_value = 241
        assert provider.episode_sources("1", "1", "dub")
        assert resolve.call_count == 2
        provider.domains = ["https://other.senshi.to"]
        assert provider.episode_sources("1", "1", "sub")
        assert resolve.call_count == 3


@pytest.mark.parametrize("reason", [
    "Senshi needs Node.js 26 or newer for local extraction",
    "Senshi local extraction needs Linux bubblewrap, prlimit and Node.js 26+",
    "Senshi runtime transport failed",
])
def test_remote_failure_logs_sanitized_reason_and_does_not_poison_cache(caplog, reason):
    provider = Senshi()
    entries = [{"source": "https://s-90.bcdn1.se/master.txt"}]
    error = SenshiRuntimeError(reason)
    error.__cause__ = RuntimeError("raw transport secret")
    with patch("media_cli.extractors.senshi_runtime.resolve_vidcloud", side_effect=[error, entries]) as resolve:
        with caplog.at_level("DEBUG"):
            assert provider._resolve_remote_sources(3731) == []
        assert provider._resolve_remote_sources(3731) == entries
        assert resolve.call_count == 2
    assert reason in caplog.text
    assert "raw transport secret" not in caplog.text


def test_remote_cache_is_instance_scoped_and_bounded():
    a, b = Senshi(), Senshi()
    with patch("media_cli.extractors.senshi_runtime.resolve_vidcloud", return_value=[{"file": "https://s-90.bcdn1.se/v.m3u8"}]) as resolve:
        for remote_id in range(1, 67):
            assert a._resolve_remote_sources(remote_id)
        assert b._resolve_remote_sources(66)
        assert resolve.call_count == 67
    assert len(a._remote_sources_cache) <= 64


def test_remote_resolver_receives_operation_cancellation_and_propagates_it():
    provider = Senshi()
    cancel = Event()

    def fail(remote_id, *, timeout, cancel):
        assert remote_id == 3731 and timeout == 30
        cancel.set()
        raise SenshiRuntimeError("Senshi extraction cancelled")

    with browser_operation(cancel=cancel), \
            patch("media_cli.extractors.senshi_runtime.resolve_vidcloud", side_effect=fail):
        with pytest.raises(SenshiRuntimeError, match="cancelled"):
            provider._resolve_remote_sources(3731)


@pytest.mark.parametrize("source", [
    {"source": [{"src": "https://s-90.bcdn1.se/wrong.txt", "label": "dub"}, {"src": "https://s-90.bcdn1.se/right.txt", "label": "sub"}]},
    {"source": {"src": "https://s-90.bcdn1.se/right.txt"}},
    {"source": "https://s-90.bcdn1.se/right.txt"},
    {"file": "https://s-90.bcdn1.se/right.txt"},
])
def test_supported_source_shapes(source):
    provider = Senshi()
    with patch.object(provider, "_resolve_remote_sources", return_value=[source]), \
            patch.object(provider, "_read_playlist", return_value=("https://s-90.bcdn1.se/right.txt", "#EXTM3U\n#EXTINF:5,\na.ts")) as read:
        sources = provider._extract_embed_sources([{"remote_source_id": 1}], False)
    assert sources[0]["link"] == "https://s-90.bcdn1.se/right.txt"
    read.assert_called_once_with("https://s-90.bcdn1.se/right.txt")


def test_opposite_source_label_is_never_used_and_external_servers_survive():
    provider = Senshi()
    entry = {"source": [{"src": "https://s-90.bcdn1.se/dub.txt", "label": "dub"}]}
    row = {"remote_source_id": 1, "url": "https://s-90.bcdn1.se/stale.m3u8", "server2": "https://server2.example/embed", "serverFM": "https://fm.example/embed"}
    with patch.object(provider, "_resolve_remote_sources", return_value=[entry]), \
            patch.object(provider, "_read_playlist") as read:
        sources = provider._extract_embed_sources([row], False)
    assert [s["type"] for s in sources] == ["embed", "embed"]
    read.assert_not_called()


def test_matching_source_precedes_both_and_subtitles_use_file_and_ai_dub():
    provider = Senshi()
    entry = {"source": [{"src": "https://s-90.bcdn1.se/both.txt", "label": "both"}, {"src": "https://s-90.bcdn1.se/dub.txt", "label": "dub"}],
             "tracks": [{"file": "https://s-90.bcdn1.se/fr.vtt", "label": "French"},
                        {"file": "https://s-90.bcdn1.se/en.vtt", "label": "English"},
                        {"file": "https://s-90.bcdn1.se/ai_dub/en.vtt", "label": "English"},
                        {"file": "https://s-90.bcdn1.se/thumb.vtt", "label": "Thumbnails"}]}
    with patch.object(provider, "_resolve_remote_sources", return_value=[entry]), \
            patch.object(provider, "_read_playlist", return_value=("https://s-90.bcdn1.se/dub.txt", "#EXTM3U\n#EXTINF:5,\na.ts")) as read:
        dub = provider._extract_embed_sources([{"remote_source_id": 1}], True)
        entry["tracks"] = entry["tracks"][:2]
        fallback = provider._extract_embed_sources([{"remote_source_id": 1}], True)
    assert read.call_args.args == ("https://s-90.bcdn1.se/dub.txt",)
    assert [s["url"] for s in dub[0]["subtitles"]] == ["https://s-90.bcdn1.se/ai_dub/en.vtt"]
    assert fallback[0]["subtitles"][0]["label"] == "English"
    assert fallback[0]["subtitles"][0]["default"] is True


def test_video_only_variants_without_requested_audio_are_not_advertised():
    provider = Senshi()
    master = MASTER.replace('LANGUAGE="en"', 'LANGUAGE="fr"')
    with patch.object(provider, "_resolve_remote_sources", return_value=[{"source": "https://s-90.bcdn1.se/master.txt"}]), \
            patch.object(provider, "_read_playlist", return_value=("https://s-90.bcdn1.se/master.txt", master)):
        assert provider._extract_embed_sources([{"remote_source_id": 1}], True) == []


def test_runtime_cancellation_message_propagates_without_context_event():
    with patch("media_cli.extractors.senshi_runtime.resolve_vidcloud", side_effect=SenshiRuntimeError("Senshi extraction cancelled")):
        with pytest.raises(SenshiRuntimeError, match="cancelled"):
            Senshi()._resolve_remote_sources(1)


@pytest.mark.parametrize("entry", [
    {"source": "https://s-90.bcdn1.se/dub.txt", "label": "dub"},
    {"file": "https://s-90.bcdn1.se/dub.txt", "label": "dub"},
])
def test_entry_label_protects_string_and_file_fallback(entry):
    provider = Senshi()
    with patch.object(provider, "_resolve_remote_sources", return_value=[entry]), \
            patch.object(provider, "_read_playlist") as read:
        assert provider._extract_embed_sources([{"remote_source_id": 1}], False) == []
    read.assert_not_called()


def test_playlist_redirect_base_and_per_variant_audio_group():
    provider = Senshi()
    master = '''#EXTM3U
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="English",LANGUAGE="en",URI="audio/a.jpg"
#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="b",NAME="English",LANGUAGE="en",URI="audio/b.jpg"
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080,AUDIO="a"
1080.jpg
#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=854x480,AUDIO="b"
480.jpg
'''
    with patch.object(provider, "_read_playlist",
                      return_value=("https://s-90.bcdn1.se/final/master.txt", master)):
        variants = provider._playlist_variants("https://s-90.bcdn1.se/original/master.txt", "auto", True)
    assert variants == [
        {"url": "https://s-90.bcdn1.se/final/1080.jpg", "resolution": "1080p", "audio_url": "https://s-90.bcdn1.se/final/audio/a.jpg"},
        {"url": "https://s-90.bcdn1.se/final/480.jpg", "resolution": "480p", "audio_url": "https://s-90.bcdn1.se/final/audio/b.jpg"},
    ]
