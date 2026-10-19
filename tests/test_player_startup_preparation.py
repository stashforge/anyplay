import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from media_cli.playback import desktop, engine
from media_cli.playback.mpv import MpvIpc
from media_cli.ui import player_screen
from media_cli.ui.transition import PreparationCancelled


def test_new_player_starts_off_thread_and_transfers_only_after_acceptance(monkeypatch):
    owner = threading.current_thread()
    shared = MpvIpc()
    candidate = MpvIpc()
    process, client = SimpleNamespace(poll=lambda: None), object()

    def start(*, cancel_check):
        assert threading.current_thread() is not owner
        assert shared.process is None
        assert not cancel_check()
        candidate.process, candidate.client, candidate.running = process, client, True

    def pump(work, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(work).result(timeout=1)

    monkeypatch.setattr(candidate, "start", start)
    monkeypatch.setattr(engine, "MpvIpc", lambda: candidate)
    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    engine._start_player_for_playback(shared)
    assert shared.process is process and shared.client is client and shared.running
    assert candidate.process is None and candidate.client is None and not candidate.running
    # Fakes are not real OS resources.
    shared.process = shared.client = None


@pytest.mark.parametrize("late", [False, True])
def test_cancelled_startup_never_replaces_shared_player_and_closes_candidate(monkeypatch, late):
    started, release = threading.Event(), threading.Event()
    shared, candidate = MpvIpc(), MpvIpc()
    stopped, threads = [], []

    def start(*, cancel_check):
        started.set()
        if late:
            assert release.wait(2)
            assert cancel_check()

    def cancel(work, **kwargs):
        if late:
            thread = threading.Thread(target=work)
            threads.append(thread)
            thread.start()
            assert started.wait(1)
        else:
            work()
        raise PreparationCancelled

    monkeypatch.setattr(candidate, "start", start)
    monkeypatch.setattr(candidate, "quit", lambda: stopped.append(True))
    monkeypatch.setattr(engine, "MpvIpc", lambda: candidate)
    monkeypatch.setattr(player_screen, "run_loading_work", cancel)
    try:
        with pytest.raises(desktop.PlaybackPreparationCancelled):
            engine._start_player_for_playback(shared)
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=1)
            assert not thread.is_alive()
    assert shared.process is None and shared.client is None
    assert stopped == [True]


def test_ready_player_does_not_run_startup_again(monkeypatch):
    shared = MpvIpc()
    shared.process = SimpleNamespace(poll=lambda: None)
    shared.client = object()
    monkeypatch.setattr(player_screen, "run_loading_work", lambda *a, **kw: pytest.fail("Ready player restarted"))
    engine._start_player_for_playback(shared)
    shared.process = shared.client = None


def test_socket_wait_cooperatively_cancels_and_releases_started_process(monkeypatch):
    from media_cli.playback import mpv

    cancelled = threading.Event()
    closed = []
    monkeypatch.setattr(mpv, "cleanup_mpv_runtime", lambda path: closed.append(path))
    monkeypatch.setattr(mpv, "create_mpv_runtime", lambda: ("runtime", "socket", "conf", "chapters", "lua"))
    monkeypatch.setattr(mpv.subprocess, "Popen", lambda *a, **kw: SimpleNamespace(poll=lambda: None, wait=lambda **kw: None))
    monkeypatch.setattr(mpv.os.path, "exists", lambda path: False)
    monkeypatch.setattr(mpv.time, "sleep", lambda seconds: cancelled.set())
    monkeypatch.setattr(mpv.socket, "socket", lambda *a: pytest.fail("Cancelled startup connected IPC"))
    player = MpvIpc()
    with pytest.raises(mpv.MpvStartupCancelled):
        player.start(cancel_check=cancelled.is_set)
    assert player.process is None and player.client is None
    assert "runtime" in closed
