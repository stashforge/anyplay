import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from media_cli.media import aniskip
from media_cli.playback import android, engine
from media_cli.ui import player_screen
from media_cli.ui.transition import PreparationCancelled


@pytest.mark.parametrize("enabled", [False, True])
def test_android_unknown_duration_writes_real_chapters_or_clears_disabled_marks(monkeypatch, tmp_path, enabled):
    chapters = tmp_path / "chapters.txt"
    chapters.write_text("stale chapters", encoding="utf-8")
    write = aniskip.generate_chapters_file

    def fetch(*args, **kwargs):
        assert enabled
        assert kwargs == {"episode_length": 0.0}
        return [{"type": "op", "label": "Opening", "start": 0, "end": 90, "episode_length": 4921}]

    monkeypatch.setattr(aniskip, "fetch_skip_times", fetch)
    monkeypatch.setattr(aniskip, "generate_chapters_file", lambda intervals, path: write(intervals, str(chapters)))
    monkeypatch.setattr(android, "start_local_proxy", lambda *a, **kw: ("http://127.0.0.1/video", object()))
    monkeypatch.setattr(android, "cleanup_active_local_proxy", lambda: None)
    monkeypatch.setattr(android, "replace_active_local_proxy", lambda server: None)
    monkeypatch.setattr(android.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=""))
    assert android.play_android(
        "Title", 1, {"link": "https://example.org/video.mp4"}, None,
        player="vlc", mal_id=52034, aniskip_enabled=enabled,
    )
    if enabled:
        content = chapters.read_text(encoding="utf-8")
        assert content.startswith(";FFMETADATA1")
        assert "TITLE=Opening" in content
        assert "END=90000" in content
    else:
        assert not chapters.exists()


def test_android_skip_lookup_runs_in_worker_and_exports_only_on_owner(monkeypatch):
    owner = threading.current_thread()
    calls = []
    interval = {"type": "ed", "start": 1300, "end": 1390, "episode_length": 1465}

    def fetch(mal_id, episode, *, episode_length):
        assert threading.current_thread() is not owner
        assert (mal_id, episode, episode_length) == (52034, 1, 0.0)
        return [interval]

    def pump(work):
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(work).result(timeout=1)

    def write(intervals, path):
        assert threading.current_thread() is owner
        calls.append(intervals)

    monkeypatch.setattr(aniskip, "fetch_skip_times", fetch)
    monkeypatch.setattr(aniskip, "generate_chapters_file", write)
    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    monkeypatch.setattr(android.subprocess, "run", lambda *a, **kw: None)
    android.play_android(
        "Title", 1, {"link": "https://example.org/video.mp4", "duration": 1465},
        None, player="vlc", mal_id=52034,
        fetch_skip_times_fn=engine._fetch_android_skip_times,
    )
    assert calls == [[interval]]


@pytest.mark.parametrize("failure", [PreparationCancelled, KeyboardInterrupt])
def test_cancelled_android_skip_lookup_does_not_export_or_launch(monkeypatch, failure):
    def pump(work):
        raise failure

    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    monkeypatch.setattr(aniskip, "generate_chapters_file", lambda *a: pytest.fail("Cancelled lookup wrote chapters"))
    monkeypatch.setattr(android.subprocess, "run", lambda *a, **kw: pytest.fail("Cancelled lookup launched player"))

    def play():
        return android.play_android(
            "Title", 1, {"link": "https://example.org/video.mp4", "duration": 1465},
            None, player="vlc", mal_id=52034,
            fetch_skip_times_fn=engine._fetch_android_skip_times,
        )

    if failure is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            play()
    else:
        assert play() is False


def test_late_android_skip_result_has_no_chapter_or_launch_side_effects(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def fetch(*args, **kwargs):
        started.set()
        assert release.wait(2)
        return []

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = []

        def pump(work):
            pending.append(pool.submit(work))
            assert started.wait(1)
            raise PreparationCancelled

        monkeypatch.setattr(aniskip, "fetch_skip_times", fetch)
        monkeypatch.setattr(player_screen, "run_loading_work", pump)
        monkeypatch.setattr(aniskip, "generate_chapters_file", lambda *a: pytest.fail("Late result wrote chapters"))
        monkeypatch.setattr(android.subprocess, "run", lambda *a, **kw: pytest.fail("Late result launched player"))
        try:
            assert android.play_android(
                "Title", 1, {"link": "https://example.org/video.mp4", "duration": 1465},
                None, player="vlc", mal_id=52034,
                fetch_skip_times_fn=engine._fetch_android_skip_times,
            ) is False
        finally:
            release.set()
        assert pending[0].result(timeout=1) == []
