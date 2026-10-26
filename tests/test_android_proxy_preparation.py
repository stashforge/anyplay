import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace

import pytest

from media_cli.media import aniskip, local_proxy
from media_cli.playback import android, engine
from media_cli.ui import player_screen
from media_cli.ui.transition import PreparationCancelled

pytestmark = pytest.mark.usefixtures("installed_android_launcher")


@pytest.fixture(params=("standard", "dash", "split", "external_audio", "bound_audio", "bound_single", "dailymotion"))
def proxy_case(request, monkeypatch):
    stream = {"link": "https://example.org/video.mp4", "headers": {"User-Agent": "original"}}
    factory = "start_local_proxy"
    if request.param == "dash":
        stream.update(dash_video={"url": stream["link"]}, dash_audio={"url": "https://example.org/audio.mp4"})
        factory = "start_local_content_server"
    elif request.param == "split":
        stream.update(type="hls", split_video_url=stream["link"], split_audio_url="https://example.org/audio.m3u8")
        factory = "start_local_dual_proxy"
    elif request.param in ("external_audio", "bound_audio"):
        stream.update(type="hls", audio_url="https://example.org/audio.m3u8")
        factory = "start_local_dual_proxy"
        monkeypatch.setattr(android, "start_local_proxy", lambda *a, **kw: pytest.fail("Detached audio used video-only proxy"))
        if request.param == "bound_audio":
            stream['ip_version'] = 4
    elif request.param == "bound_single":
        stream.update(type="hls", ip_version=4)
    elif request.param == "dailymotion":
        stream.update(dailymotion_video=stream["link"], dailymotion_audio="https://example.org/audio.m3u8")
        factory = "start_local_dual_proxy"
    monkeypatch.setattr(aniskip, "generate_chapters_file", lambda *a: None)
    return stream, factory, request.param


def play(stream):
    return android.play_android(
        "Title", "1", stream, None, player="vlc",
        prepare_proxy_fn=engine._prepare_android_proxy,
    )


def test_android_proxy_is_prepared_off_owner_and_activated_only_after_acceptance(monkeypatch, proxy_case):
    stream, factory, variant = proxy_case
    before = deepcopy(stream)
    owner = threading.current_thread()
    server, calls = object(), []

    def start(*args, **kwargs):
        assert threading.current_thread() is not owner
        assert calls == ["cleanup"]
        if variant != "dash":
            assert kwargs["target_player"] == "vlc"
            assert kwargs["title"] == "Title - EP 1"
            assert callable(kwargs["on_progress"])
            if variant in ('bound_audio', 'bound_single'):
                assert kwargs['ip_version'] == 4
            if variant in ("external_audio", "bound_audio"):
                assert args[:2] == (stream["link"], stream["audio_url"])
            args[-1]["User-Agent"] = "changed"
        else:
            assert args[1:] == ("stream.mpd", "application/dash+xml")
        calls.append("prepare")
        return "http://127.0.0.1/video", server

    def activate(value):
        assert threading.current_thread() is owner
        assert value is server
        calls.append("activate")

    def pump(work):
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(work).result(timeout=1)

    def launch(command, **kwargs):
        assert threading.current_thread() is owner
        assert command[command.index("-d") + 1] == "http://127.0.0.1/video"
        assert command[command.index("-t") + 1] == ("application/dash+xml" if variant == "dash" else "video/*")
        calls.append("launch")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(android, factory, start)
    monkeypatch.setattr(android, "cleanup_active_local_proxy", lambda: calls.append("cleanup"))
    monkeypatch.setattr(android, "replace_active_local_proxy", activate)
    monkeypatch.setattr(android.subprocess, "run", launch)
    monkeypatch.setattr(local_proxy, "stop_local_proxy", lambda *a: pytest.fail("Accepted proxy stopped"))
    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    assert play(stream)
    assert calls == ["cleanup", "prepare", "activate", "launch"]
    assert stream == before


@pytest.mark.parametrize("late", (False, True))
def test_android_cancelled_proxy_is_closed_once_and_never_activated(monkeypatch, proxy_case, late):
    stream, factory, _ = proxy_case
    started, release = threading.Event(), threading.Event()
    server, stopped, pending = object(), [], []

    def start(*args, **kwargs):
        started.set()
        if late:
            assert release.wait(2)
        return "http://127.0.0.1/video", server

    with ThreadPoolExecutor(max_workers=1) as pool:
        def pump(work):
            if late:
                pending.append(pool.submit(work))
                assert started.wait(1)
            else:
                work()
            raise PreparationCancelled

        monkeypatch.setattr(android, factory, start)
        monkeypatch.setattr(android, "cleanup_active_local_proxy", lambda: None)
        monkeypatch.setattr(android, "replace_active_local_proxy", lambda *a: pytest.fail("Cancelled proxy activated"))
        monkeypatch.setattr(android.subprocess, "run", lambda *a, **kw: pytest.fail("Cancelled proxy launched player"))
        monkeypatch.setattr(local_proxy, "stop_local_proxy", stopped.append)
        monkeypatch.setattr(player_screen, "run_loading_work", pump)
        try:
            assert play(stream) is False
        finally:
            release.set()
        for future in pending:
            future.result(timeout=1)
    assert stopped == [server]


def test_android_proxy_ctrl_c_releases_unaccepted_server(monkeypatch, proxy_case):
    stream, factory, _ = proxy_case
    server, stopped = object(), []

    def pump(work):
        work()
        raise KeyboardInterrupt

    monkeypatch.setattr(android, factory, lambda *a, **kw: ("http://127.0.0.1/video", server))
    monkeypatch.setattr(android, "cleanup_active_local_proxy", lambda: None)
    monkeypatch.setattr(android, "replace_active_local_proxy", lambda *a: pytest.fail("Interrupted proxy activated"))
    monkeypatch.setattr(android.subprocess, "run", lambda *a, **kw: pytest.fail("Interrupted preparation launched player"))
    monkeypatch.setattr(local_proxy, "stop_local_proxy", stopped.append)
    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    with pytest.raises(KeyboardInterrupt):
        play(stream)
    assert stopped == [server]


def test_android_proxy_setup_error_does_not_activate_or_launch(monkeypatch, proxy_case):
    stream, factory, _ = proxy_case
    errors = []

    def start(*args, **kwargs):
        raise OSError("Proxy unavailable")

    def pump(work):
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(work).result(timeout=1)

    monkeypatch.setattr(android, factory, start)
    monkeypatch.setattr(android, "cleanup_active_local_proxy", lambda: None)
    monkeypatch.setattr(android, "replace_active_local_proxy", lambda *a: pytest.fail("Failed proxy activated"))
    monkeypatch.setattr(android.subprocess, "run", lambda *a, **kw: pytest.fail("Failed preparation launched player"))
    monkeypatch.setattr(android, "_error", errors.append)
    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    assert play(stream) is False
    assert len(errors) == 1
    assert "Proxy unavailable" in errors[0]
