import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from media_cli.media import local_proxy
from media_cli.playback import desktop, engine
from media_cli.ui import player_screen
from media_cli.ui.transition import PreparationCancelled


def test_proxy_preparation_runs_on_worker_without_activating_proxy(monkeypatch):
    owner = threading.current_thread()
    server = object()
    headers = {"Referer": "original"}

    def start(url, referer, request_headers, **kwargs):
        assert threading.current_thread() is not owner
        request_headers["Referer"] = "changed"
        return "http://127.0.0.1/video", server

    def pump(work, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(work).result(timeout=1)

    monkeypatch.setattr(local_proxy, "start_local_proxy", start)
    monkeypatch.setattr(local_proxy, "replace_active_local_proxy", lambda *a: pytest.fail("Worker activated proxy"))
    monkeypatch.setattr(local_proxy, "stop_local_proxy", lambda *a: pytest.fail("Accepted resource stopped"))
    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    assert engine._prepare_proxy_for_playback("https://example.test/video", "", headers) == ("http://127.0.0.1/video", server)
    assert headers == {"Referer": "original"}


@pytest.mark.parametrize("late", [False, True])
def test_cancelled_proxy_is_stopped_once_and_never_activated(monkeypatch, late):
    started, release = threading.Event(), threading.Event()
    server, stopped, threads = object(), [], []

    def start(*args, **kwargs):
        started.set()
        if late:
            assert release.wait(2)
        return "http://127.0.0.1/video", server

    def cancel(work, **kwargs):
        if late:
            thread = threading.Thread(target=work)
            threads.append(thread)
            thread.start()
            assert started.wait(1)
        else:
            work()
        raise PreparationCancelled

    monkeypatch.setattr(local_proxy, "start_local_proxy", start)
    monkeypatch.setattr(local_proxy, "stop_local_proxy", stopped.append)
    monkeypatch.setattr(local_proxy, "replace_active_local_proxy", lambda *a: pytest.fail("Cancelled proxy activated"))
    monkeypatch.setattr(player_screen, "run_loading_work", cancel)
    try:
        with pytest.raises(desktop.PlaybackPreparationCancelled):
            engine._prepare_proxy_for_playback("https://example.test/video", "", {})
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=1)
            assert not thread.is_alive()
    assert stopped == [server]


@pytest.mark.parametrize("failure", [PreparationCancelled, KeyboardInterrupt])
def test_cancelled_proxy_preparation_closes_torrent_before_player_load(monkeypatch, failure):
    closed = []
    resource = SimpleNamespace(path="/tmp/video.mkv", close=lambda: closed.append(True))
    monkeypatch.setattr(player_screen, "run_loading_work", lambda *a, **kw: (_ for _ in ()).throw(failure()))
    ipc = SimpleNamespace(load=lambda *a, **kw: pytest.fail("Cancelled proxy started mpv"))

    def play():
        return desktop.play_desktop(ipc, "Example", "1", {
            "type": "torrent", "link": "magnet:?xt=urn:btih:test", "requires_proxy": True,
        }, get_resume_time=lambda *a: 0, get_preferred_mirror=lambda *a: {}, update_stream_info=lambda info: None,
            prepare_torrent_fn=lambda *a, **kw: resource, prepare_proxy_fn=engine._prepare_proxy_for_playback)

    if failure is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            play()
    else:
        assert play()[0] == "CANCELLED"
    assert closed == [True]


def test_accepted_proxy_is_activated_on_owner_and_cleaned_after_playback(monkeypatch):
    owner = threading.current_thread()
    server, calls = object(), []

    def start(*args, **kwargs):
        assert threading.current_thread() is not owner
        return "http://127.0.0.1/video", server

    def activate(value):
        assert threading.current_thread() is owner
        assert value is server
        calls.append("activate")

    def pump(work, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(work).result(timeout=1)

    monkeypatch.setattr(local_proxy, "start_local_proxy", start)
    monkeypatch.setattr(local_proxy, "replace_active_local_proxy", activate)
    monkeypatch.setattr(local_proxy, "cleanup_active_local_proxy", lambda: calls.append("cleanup"))
    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    ipc = SimpleNamespace(props={}, load=lambda *a, **kw: calls.append("load"),
                          wait_for_playback=lambda *a: ("QUIT", 0))
    assert desktop.play_desktop(ipc, "Example", "1", {
        "link": "https://example.test/video.mp4", "requires_proxy": True,
    }, get_resume_time=lambda *a: 0, get_preferred_mirror=lambda *a: {},
        update_stream_info=lambda info: None, prepare_proxy_fn=engine._prepare_proxy_for_playback)[0] == "QUIT"
    assert calls == ["activate", "load", "cleanup"]
