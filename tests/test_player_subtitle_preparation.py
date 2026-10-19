import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from media_cli.media import subtitle_scorer
from media_cli.playback import desktop, engine
from media_cli.ui import player_screen
from media_cli.ui.transition import PreparationCancelled


def test_subtitle_preparation_uses_worker_and_detached_inputs(monkeypatch):
    owner = threading.current_thread()
    subtitles = [{"url": "subtitle", "default": True}]
    headers = {"Referer": "original"}

    def score(items, **kwargs):
        assert threading.current_thread() is not owner
        assert not kwargs["cancel_check"]()
        items[0]["default"] = False
        kwargs["headers"]["Referer"] = "changed"
        return items

    def pump(work, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(work).result(timeout=1)

    monkeypatch.setattr(subtitle_scorer, "filter_subtitles", score)
    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    result = engine._prepare_subtitles_for_playback(subtitles, headers=headers)
    assert not result[0]["default"]
    assert subtitles[0]["default"]
    assert headers == {"Referer": "original"}


@pytest.mark.parametrize("failure", [PreparationCancelled, KeyboardInterrupt])
def test_subtitle_cancel_closes_prepared_torrent_and_never_loads_player(monkeypatch, failure):
    closed = []
    resource = SimpleNamespace(path="/tmp/episode.mkv", close=lambda: closed.append(True))

    def pump(*args, **kwargs):
        raise failure

    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    ipc = SimpleNamespace(load=lambda *a, **kw: pytest.fail("Cancelled subtitles started mpv"))
    kwargs = dict(
        get_resume_time=lambda *a: 0, get_preferred_mirror=lambda *a: {}, update_stream_info=lambda info: None,
        prepare_torrent_fn=lambda *a, **kw: resource,
        prepare_subtitles_fn=engine._prepare_subtitles_for_playback,
    )

    def play():
        return desktop.play_desktop(ipc, "Example", "1", {
            "type": "torrent", "link": "magnet:?xt=urn:btih:test", "subtitles": [{"url": "subtitle"}],
        }, **kwargs)

    if failure is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            play()
    else:
        assert play()[0] == "CANCELLED"
    assert closed == [True]


def test_subtitle_scoring_skips_network_when_already_cancelled(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: pytest.fail("Cancelled scorer fetched a subtitle"))
    assert subtitle_scorer.filter_subtitles([{"url": "https://example.test/sub.vtt"}], cancel_check=lambda: True) == []


def test_cancel_during_subtitle_request_stops_followup_requests(monkeypatch):
    cancelled = threading.Event()
    calls = []

    def read():
        cancelled.set()
        return b"subtitle data"

    def fetch(*args, **kwargs):
        calls.append(True)
        return nullcontext(SimpleNamespace(read=read))

    monkeypatch.setattr("urllib.request.urlopen", fetch)
    monkeypatch.setattr(subtitle_scorer, "_SCORER_CACHE", {})
    assert subtitle_scorer.filter_subtitles([
        {"url": "https://example.test/one.m3u8"}, {"url": "https://example.test/two.vtt"},
    ], cancel_check=cancelled.is_set) == []
    assert calls == [True]


def test_ordinary_scoring_error_keeps_existing_subtitles_and_starts_playback():
    loaded = []
    subtitles = [{"url": "https://example.test/sub.vtt"}]

    def score(*args, **kwargs):
        raise ValueError("Scoring unavailable")

    ipc = SimpleNamespace(
        props={}, load=lambda *a, **kw: loaded.append(kw["subtitles"]),
        wait_for_playback=lambda *a: ("QUIT", 0),
    )
    assert desktop.play_desktop(ipc, "Example", "1", {
        "link": "https://example.test/video.mp4", "subtitles": subtitles,
    }, get_resume_time=lambda *a: 0, get_preferred_mirror=lambda *a: {},
        update_stream_info=lambda info: None, prepare_subtitles_fn=score)[0] == "QUIT"
    assert loaded == [subtitles]
