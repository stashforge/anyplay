"""Missing native helpers affect their source, not every mirror/provider."""

import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from media_cli.core.api import ProviderDependencyError


def missing():
    from media_cli.services.requirements import MissingPythonDependency
    return MissingPythonDependency(("bs4",), "Filemoon extraction")


def embed():
    return {"sourceName": "Filemoon", "sourceUrl": "https://filemoon.sx/e/test", "type": "embed"}


def test_missing_native_import_can_use_ytdlp_and_keeps_a_hint(monkeypatch):
    from media_cli.media import resolver

    monkeypatch.setattr(resolver, "find_extractor", Mock(side_effect=missing()))
    monkeypatch.setattr(resolver.shutil, "which", lambda name: "/test/yt-dlp")
    fallback = Mock(return_value=[{"link": "https://cdn.example/video.mp4", "source_name": "Filemoon"}])
    monkeypatch.setattr(resolver, "resolve_ytdlp_embed", fallback)
    result = resolver.resolve_source(embed(), silent=True)
    assert len(result) == 1 and fallback.call_count == 1
    assert "beautifulsoup4" in result[0]["dependency_hints"][0]
    assert "may fail" in result[0]["dependency_hints"][0]


@pytest.mark.parametrize("fallback_available", [False, True])
def test_missing_native_error_preserved_when_fallback_cannot_resolve(monkeypatch, fallback_available):
    from media_cli.media import resolver

    failure = missing()
    monkeypatch.setattr(resolver, "find_extractor", Mock(side_effect=failure))
    monkeypatch.setattr(resolver.shutil, "which", lambda name: "/test/yt-dlp" if fallback_available else None)
    fallback = Mock(return_value=[])
    monkeypatch.setattr(resolver, "resolve_ytdlp_embed", fallback)
    with pytest.raises(ProviderDependencyError) as caught:
        resolver.resolve_source(embed(), silent=True)
    assert caught.value is failure
    assert fallback.call_count == int(fallback_available)


def test_foreground_tries_good_mirror_after_missing_native_dependency(monkeypatch):
    from media_cli.core import streams

    failure = missing()
    good = {"link": "https://cdn.example/good.mp4", "source_name": "Good", "resolution": "720p"}
    resolve = Mock(side_effect=[failure, [good]])
    monkeypatch.setattr(streams, "resolve_source", resolve)
    monkeypatch.setattr(streams, "expand_direct_sources", lambda sources: sources)
    monkeypatch.setattr(streams, "get_preferred_mirror", lambda *args: {})
    payload = {"episode": {"sourceUrls": [embed(), {"sourceName": "Good", "sourceUrl": "https://good.example/embed"}]}}
    result = streams.fetch_episode_stream("show", "1", ep_data=payload)
    assert result[0]["link"] == good["link"]
    assert resolve.call_count == 2
    assert "beautifulsoup4" in result[0]["dependency_hints"][0]


def test_foreground_exhausts_alternatives_before_dependency_error(monkeypatch):
    from media_cli.core import streams

    failure = missing()
    resolve = Mock(side_effect=[failure, [], []])
    monkeypatch.setattr(streams, "resolve_source", resolve)
    monkeypatch.setattr(streams, "expand_direct_sources", lambda s: s)
    monkeypatch.setattr(streams, "get_preferred_mirror", lambda *args: {})
    data = {"episode": {"sourceUrls": [embed(), {"sourceName": "Other", "sourceUrl": "https://other.example"}]}}
    with pytest.raises(ProviderDependencyError) as caught:
        streams.fetch_episode_stream("show", "1", ep_data=data)
    assert caught.value is failure
    assert resolve.call_count == 3  # Missing libraries are not retried; transient failures are.


def test_builtin_direct_fallback_survives_missing_native_and_ytdlp(monkeypatch):
    from media_cli.media import resolver

    monkeypatch.setattr(resolver, "find_extractor", Mock(side_effect=missing()))
    monkeypatch.setattr(resolver.shutil, "which", lambda _: None)
    monkeypatch.setattr(resolver, "_extract_mp4upload", lambda _: "https://cdn.example/video.mp4")
    monkeypatch.setattr(resolver, "is_alive", lambda *a, **kw: True)
    monkeypatch.setattr(resolver, "get_size", lambda *a, **kw: 0)
    result = resolver.resolve_source({"sourceName": "Upload", "sourceUrl": "https://mp4upload.com/embed-test"}, silent=True)
    assert result[0]["link"] == "https://cdn.example/video.mp4"
    assert "beautifulsoup4" in result[0]["dependency_hints"][0]


def test_selected_native_import_has_typed_requirement(monkeypatch):
    from media_cli import extractors
    from media_cli.services import requirements

    monkeypatch.setattr(extractors, "_INSTANCES", {})
    monkeypatch.setattr(requirements, "optional_python", lambda _: None)
    with pytest.raises(ProviderDependencyError, match="beautifulsoup4"):
        extractors.find_extractor("https://filemoon.sx/e/test")
    assert extractors.find_extractor("https://unknown.example/e/test") is None


def test_missing_parser_does_not_break_plain_vtt(monkeypatch):
    monkeypatch.setitem(sys.modules, "m3u8", None)
    from media_cli.media.subtitles import fetch_prepared_subtitle_bytes

    text = b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nText\n"
    assert fetch_prepared_subtitle_bytes("https://sub.example/track.vtt", fetch_fn=lambda *args, **kwargs: text) == text


def test_missing_segment_parser_retains_remote_subtitle_with_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "m3u8", None)
    from media_cli.media import mpv_subtitle_files as files

    monkeypatch.setattr("media_cli.media.subtitles.fetch_subtitle_bytes", lambda *args, **kwargs: b"#EXTM3U\n#EXT-X-ENDLIST\n")
    track = {"url": "https://sub.example/hls"}
    resource = files.prepare_subtitle_files([track])
    try:
        assert resource.subtitles == [track]
        assert "m3u8" in resource.dependency_hints[0]
        assert "subtitle" in resource.dependency_hints[0].lower()
    finally:
        resource.close()


@pytest.mark.parametrize("alternative", [False, True])
def test_background_dependency_feedback_survives_only_for_current_attempt(monkeypatch, alternative):
    from media_cli.core import storage, streams

    payload = {"episode": {"sourceUrls": [embed()]}}
    if alternative:
        payload["episode"]["sourceUrls"].append({"sourceName": "Good", "sourceUrl": "https://good.example/embed"})
    failure = missing()
    monkeypatch.setattr(streams, "_streams_cache", {})
    monkeypatch.setattr(streams, "all_streams", [])
    monkeypatch.setattr(streams, "_streams_lock", threading.RLock())
    monkeypatch.setattr(streams, "_streams_generation", 0)
    monkeypatch.setattr(streams, "_bg_thread", None)
    monkeypatch.setattr(streams, "_start_revalidation_timer", lambda: None)
    monkeypatch.setattr(streams, "_stream_snapshot", lambda *a, **kw: [])
    monkeypatch.setattr(streams, "expand_direct_sources", lambda s: s)
    monkeypatch.setattr(storage, "get_preferred_mirror", lambda _: None)
    resolve = Mock(side_effect=[failure, [{"link": "good", "source_name": "Good"}]] if alternative else failure)
    monkeypatch.setattr(streams, "resolve_source", resolve)
    key = streams.make_stream_key("show", "1", "sub", "example", "best")
    streams.start_bg_resolve(payload, set(), *key)
    streams._bg_thread.join(2)
    assert not streams._bg_thread.is_alive()
    assert resolve.call_count == (2 if alternative else 1)
    hints, error = streams.resolution_feedback(key)
    assert "beautifulsoup4" in hints[0]
    assert error is failure
    assert streams.resolution_feedback(("other", *key[1:])) == ((), None)
    # A retired worker cannot overwrite feedback for a newer attempt.
    assert not streams._record_dependency_feedback(key, streams._streams_generation - 1, failure)
    monkeypatch.setattr(streams, "resolve_source", lambda *a, **kw: [])
    streams.start_bg_resolve(payload, set(), *key)
    streams._bg_thread.join(2)
    assert streams.resolution_feedback(key) == ((), None)


@pytest.mark.parametrize("module,url", [("uni", "https://uns.bio/#test"), ("misterdonghua", "https://misterdonghua.in/embed/test")])
def test_crypto_missing_is_typed_before_network(monkeypatch, module, url):
    import importlib

    from media_cli.services import requirements
    native = importlib.import_module(f"media_cli.extractors.{module}")
    extractor = native.UniExtractor() if module == "uni" else native.MisterDonghuaExtractor()
    monkeypatch.setattr(requirements, "optional_python", lambda _: None)
    fetch = Mock(side_effect=AssertionError("Must not fetch before checking decryptor"))
    monkeypatch.setattr(extractor, "fetch_page", fetch)
    with pytest.raises(ProviderDependencyError):
        extractor.extract(url)
    fetch.assert_not_called()


def test_metadata_refresh_retires_dependency_feedback(monkeypatch):
    from media_cli.core import streams

    key = streams.make_stream_key("show", "1", "sub", "example", "best")
    entry = {"ep_data_version": "old", "resolution_version": "old", "resolution_generation": 4,
             "dependency_error": missing(), "dependency_hints": ["Old hint"], "streams": []}
    monkeypatch.setattr(streams, "_streams_cache", {key: entry})
    monkeypatch.setattr(streams, "_streams_generation", 4)
    streams._set_cached_ep_data({"episode": {"sourceUrls": [embed()]}}, key=key)
    assert streams.resolution_feedback(key) == ((), None)
    assert not streams._record_dependency_feedback(key, 4, missing())


def test_metadata_changed_during_native_fallback_rejects_stale_stream(monkeypatch):
    from media_cli.core import storage, streams

    key = streams.make_stream_key("show", "1", "sub", "example", "best")
    data = {"episode": {"sourceUrls": [embed()]}}
    monkeypatch.setattr(streams, "_streams_cache", {})
    monkeypatch.setattr(streams, "all_streams", [])
    monkeypatch.setattr(streams, "_streams_generation", 0)
    monkeypatch.setattr(streams, "_bg_thread", None)
    monkeypatch.setattr(streams, "_start_revalidation_timer", lambda: None)
    monkeypatch.setattr(streams, "_stream_snapshot", lambda *a, **kw: [])
    monkeypatch.setattr(streams, "expand_direct_sources", lambda s: s)
    monkeypatch.setattr(storage, "get_preferred_mirror", lambda _: None)
    changed = []

    def resolve(*args, **kwargs):
        changed.append(True)
        streams._set_cached_ep_data({"episode": {"sourceUrls": [
            {"sourceName": "New", "sourceUrl": "https://new.example/video"},
        ]}}, key=key)
        return [{"link": "stale", "source_name": "Old", "dependency_hints": ["Old dependency hint"]}]

    monkeypatch.setattr(streams, "resolve_source", resolve)
    streams.start_bg_resolve(data, set(), *key)
    streams._bg_thread.join(2)
    assert not streams._bg_thread.is_alive()
    assert changed == [True]
    assert streams._streams_cache[key]["streams"] == []
    assert streams.all_streams == []
    assert streams.resolution_feedback(key) == ((), None)
    assert streams._streams_cache[key]["state"] == "STALE"


def test_download_reports_missing_subtitle_parser_without_failing_video(monkeypatch, tmp_path):
    from media_cli.media import subtitles
    from media_cli.services.requirements import MissingPythonDependency

    monkeypatch.setattr(subtitles, "fetch_prepared_subtitle_bytes", Mock(side_effect=MissingPythonDependency(("m3u8",), "Segmented subtitles")))
    hints = []
    assert subtitles.download_sidecar_subtitles(
        [{"url": "https://sub.example/hls", "language": "en"}], str(tmp_path / "video"),
        quiet=True, on_hint=hints.append,
    ) == []
    assert "install m3u8" in hints[0]
    assert "subtitles skipped" in hints[0]


def test_desktop_owner_reports_subtitle_hint_and_still_plays(monkeypatch):
    from media_cli.core import reporting
    from media_cli.playback import desktop

    owner = threading.current_thread()
    messages = []
    closed, loaded = [], []

    def warn(message):
        assert threading.current_thread() is owner
        messages.append(message)

    monkeypatch.setattr(reporting, "warn", warn)
    resource = SimpleNamespace(subtitles=[{"url": "https://sub.example/hls"}],
                               dependency_hints=["Remote subtitles; install m3u8."], close=lambda: closed.append(True))
    ipc = SimpleNamespace(props={}, load=lambda *a, **kw: loaded.append(kw["subtitles"]),
                          wait_for_playback=lambda *a: ("QUIT", 0))
    desktop.play_desktop(
        ipc, "Title", "1", {"link": "https://cdn.example/video.m3u8", "type": "hls",
                              "subtitles": resource.subtitles, "dependency_hints": ["Fallback playback may fail."]},
        get_resume_time=lambda *a: 0, get_preferred_mirror=lambda *a: {}, update_stream_info=lambda *a: None,
        prepare_subtitles_fn=lambda rows, **kw: rows, prepare_subtitle_files_fn=lambda *a, **kw: resource,
    )
    assert messages == ["Fallback playback may fail.", "Remote subtitles; install m3u8."]
    assert loaded == [resource.subtitles] and closed == [True]
