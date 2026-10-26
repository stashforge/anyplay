import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from media_cli.media import aniskip, subtitle_scorer
from media_cli.playback import android, engine
from media_cli.ui import player_screen
from media_cli.ui.transition import PreparationCancelled

pytestmark = pytest.mark.usefixtures("installed_android_launcher")


@pytest.fixture
def launch(monkeypatch):
    calls = []
    monkeypatch.setattr(aniskip, "generate_chapters_file", lambda *a: None)
    monkeypatch.setattr(android, "cleanup_active_local_proxy", lambda: calls.append("cleanup"))
    monkeypatch.setattr(android, "replace_active_local_proxy", lambda server: None)

    def proxy(*args, **kwargs):
        calls.append(kwargs["subtitles"])
        return "http://127.0.0.1/video", object()

    monkeypatch.setattr(android, "start_local_proxy", proxy)
    monkeypatch.setattr(android.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=""))

    def play(stream):
        return android.play_android(
            "Title", 1, stream, None, player="vlc",
            prepare_subtitles_fn=engine._prepare_android_subtitles,
        )

    return play, calls


def test_android_subtitles_use_shared_worker_and_detached_inputs(monkeypatch, launch):
    owner = threading.current_thread()
    items = [{"url": "https://example.org/sub.vtt", "default": True}]
    headers = {"User-Agent": "original"}
    checks = []

    def score(subtitles, **kwargs):
        assert threading.current_thread() is not owner
        assert kwargs["video_duration_s"] == 1465
        checks.append(kwargs["cancel_check"])
        assert not checks[-1]()
        subtitles[0]["default"] = False
        kwargs["headers"]["User-Agent"] = "changed"
        return subtitles

    def pump(work):
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(work).result(timeout=1)

    monkeypatch.setattr(subtitle_scorer, "filter_subtitles", score)
    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    play, calls = launch
    assert play({"link": "https://example.org/video.mp4", "duration": 1465,
                 "subtitles": items, "headers": headers})
    assert calls == ["cleanup", [{"url": "https://example.org/sub.vtt", "default": False}]]
    assert items[0]["default"] is True
    assert headers == {"User-Agent": "original"}
    assert checks[0]()


@pytest.mark.parametrize("failure", [PreparationCancelled, KeyboardInterrupt])
def test_android_subtitle_cancellation_prevents_proxy_and_launch(monkeypatch, launch, failure):
    def pump(work):
        raise failure

    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    monkeypatch.setattr(android.subprocess, "run", lambda *a, **kw: pytest.fail("Cancelled preparation launched player"))
    play, calls = launch
    stream = {"link": "https://example.org/video.mp4", "subtitle_url": "https://example.org/sub.vtt"}
    if failure is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            play(stream)
    else:
        assert play(stream) is False
    assert calls == []


def test_android_subtitle_error_preserves_original_subtitles(monkeypatch, launch):
    def pump(work):
        raise ValueError("Scoring unavailable")

    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    play, calls = launch
    items = [{"url": "https://example.org/sub.vtt"}]
    assert play({"link": "https://example.org/video.mp4", "vtt": items})
    assert calls == ["cleanup", items]


def test_android_without_subtitles_does_not_start_subtitle_loading(monkeypatch, launch):
    monkeypatch.setattr(player_screen, "run_loading_work", lambda *a: pytest.fail("No subtitles to prepare"))
    play, calls = launch
    assert play({"link": "https://example.org/video.mp4"})
    assert calls == ["cleanup", []]


def test_late_android_subtitle_result_is_retired_and_never_reaches_proxy(monkeypatch, launch):
    started, release = threading.Event(), threading.Event()
    items = [{"url": "https://example.org/sub.vtt", "default": True}]

    def score(subtitles, **kwargs):
        started.set()
        assert release.wait(2)
        assert kwargs["cancel_check"]()
        subtitles[0]["default"] = False
        return subtitles

    monkeypatch.setattr(subtitle_scorer, "filter_subtitles", score)
    monkeypatch.setattr(android.subprocess, "run", lambda *a, **kw: pytest.fail("Late result launched player"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = []

        def pump(work):
            pending.append(pool.submit(work))
            assert started.wait(1)
            raise PreparationCancelled

        monkeypatch.setattr(player_screen, "run_loading_work", pump)
        play, calls = launch
        try:
            assert play({"link": "https://example.org/video.mp4", "subtitles": items}) is False
        finally:
            release.set()
        assert pending[0].result(timeout=1)[0]["default"] is False
    assert items[0]["default"] is True
    assert calls == []
