from pathlib import Path
from types import SimpleNamespace

import pytest

from media_cli.playback import desktop


@pytest.mark.parametrize("requires_proxy", [True, False])
@pytest.mark.parametrize("stream_type", ["hls", ""])
def test_desktop_keeps_local_subtitles_out_of_proxy_and_releases_files(monkeypatch, requires_proxy, stream_type):
    data = b"WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nOne sentence.\n"
    monkeypatch.setattr("media_cli.media.subtitles.fetch_subtitle_bytes", lambda *a, **kw: data)
    paths = []

    def proxy(*a, **kw):
        assert kw["subtitles"] == []
        return "http://127.0.0.1/video.m3u8", object()

    def load(*a, **kw):
        track = kw["subtitles"][0]
        path = Path(track["url"])
        assert path.read_bytes() == data
        assert track["label"] == "English" and track["default"]
        assert a[7] == str(path)
        paths.append(path)

    monkeypatch.setattr("media_cli.media.local_proxy.replace_active_local_proxy", lambda *a: None)
    monkeypatch.setattr("media_cli.media.local_proxy.cleanup_active_local_proxy", lambda: None)
    ipc = SimpleNamespace(props={}, load=load, wait_for_playback=lambda *a: ("QUIT", 0))
    desktop.play_desktop(ipc, "Title", "1", {
        "link": "https://media.test/video.m3u8", "requires_proxy": requires_proxy, "type": stream_type,
        "subtitles": [{"url": "https://media.test/sub", "label": "English", "default": True}],
    }, get_resume_time=lambda *a: 0, get_preferred_mirror=lambda *a: {}, update_stream_info=lambda *a: None,
        prepare_subtitles_fn=lambda rows, **kw: rows, prepare_proxy_fn=proxy)
    assert paths and not paths[0].exists()


def test_materialization_preserves_local_tracks_and_cleans_up_on_cancel(monkeypatch):
    from media_cli.media import mpv_subtitle_files as files

    requests = []
    cancelled = []

    def fetch(*a, **kw):
        requests.append(True)
        cancelled.append(True)
        return b"WEBVTT\n\n"

    monkeypatch.setattr("media_cli.media.subtitles.fetch_subtitle_bytes", fetch)
    tracks = [{"url": "/tmp/sub.vtt"}]
    resource = files.prepare_subtitle_files(tracks)
    assert resource.subtitles == tracks and not requests
    resource.close()
    with pytest.raises(files.SubtitleFilesCancelled):
        files.prepare_subtitle_files([{"url": "https://media.test/sub.vtt"}], cancel_check=lambda: bool(cancelled))


def test_failed_or_playlist_subtitle_retains_remote_fallback(monkeypatch):
    from media_cli.media import mpv_subtitle_files as files

    tracks = [{"url": "https://media.test/sub"}]
    monkeypatch.setattr("media_cli.media.subtitles.fetch_subtitle_bytes", lambda *a, **kw: b"#EXTM3U\nsegment.vtt\n")
    resource = files.prepare_subtitle_files(tracks)
    assert resource.subtitles == tracks
    resource.close()


def test_materialization_keeps_headers_and_source_data(monkeypatch):
    from media_cli.media import mpv_subtitle_files as files

    source = [{"url": "https://media.test/sub", "lang": "en", "default": True}]
    calls = []

    def fetch(url, **kw):
        calls.append((url, kw))
        return b"WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nText\n"

    monkeypatch.setattr("media_cli.media.subtitles.fetch_subtitle_bytes", fetch)
    resource = files.prepare_subtitle_files(source, headers={"Origin": "https://media.test"}, referer="https://media.test/")
    try:
        assert source[0]["url"] == "https://media.test/sub"
        assert resource.subtitles[0]["lang"] == "en"
        assert calls == [(source[0]["url"], {"headers": {"Origin": "https://media.test"}, "referer": "https://media.test/"})]
    finally:
        resource.close()


def test_cancelled_worker_releases_unclaimed_subtitle_files(monkeypatch):
    from media_cli.playback import engine
    from media_cli.ui import player_screen
    from media_cli.ui.transition import PreparationCancelled

    released = []

    def prepare(*a, **kw):
        return SimpleNamespace(close=lambda: released.append(True))

    def cancel_after_result(work):
        work()
        raise PreparationCancelled

    monkeypatch.setattr("media_cli.media.mpv_subtitle_files.prepare_subtitle_files", prepare)
    monkeypatch.setattr(player_screen, "run_loading_work", cancel_after_result)
    with pytest.raises(desktop.PlaybackPreparationCancelled):
        engine._prepare_mpv_subtitle_files([])
    assert released == [True]


@pytest.mark.parametrize("stream_type, enabled", [("mp4", True), ("hls", False)])
def test_non_hls_or_disabled_workaround_does_not_prepare_files(monkeypatch, stream_type, enabled):
    monkeypatch.setattr(desktop, "_HLS_LOCAL_SUBTITLES", enabled)
    subtitles = [{"url": "https://media.test/sub.vtt", "default": True}]

    def load(*args, **kwargs):
        assert kwargs["subtitles"] == subtitles

    ipc = SimpleNamespace(props={}, load=load, wait_for_playback=lambda *a: ("QUIT", 0))
    desktop.play_desktop(ipc, "Title", "1", {
        "link": "https://media.test/video", "type": stream_type, "subtitles": subtitles,
    }, get_resume_time=lambda *a: 0, get_preferred_mirror=lambda *a: {}, update_stream_info=lambda *a: None,
        prepare_subtitles_fn=lambda rows, **kw: rows,
        prepare_subtitle_files_fn=lambda *a, **kw: pytest.fail("Unexpected subtitle preparation"))
