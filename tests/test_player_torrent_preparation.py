import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from media_cli.core import reporting
from media_cli.playback import engine
from media_cli.torrents import runtime
from media_cli.ui import player_screen
from media_cli.ui.transition import PreparationCancelled


def test_torrent_preparation_transfers_resource_only_after_ui_accepts(monkeypatch):
    owner = threading.current_thread()
    closed, progress = [], []
    resource = SimpleNamespace(close=lambda: closed.append(True))

    def prepare(stream, *, progress_callback, cancel_check):
        assert threading.current_thread() is not owner
        assert not cancel_check()
        progress_callback("Buffering torrent")
        return resource

    def pump(work, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(work).result(timeout=1)

    monkeypatch.setattr(runtime, "prepare_torrent_stream", prepare)
    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    monkeypatch.setattr(reporting, "progress", progress.append)
    assert engine._prepare_torrent_for_playback({"type": "torrent"}) is resource
    assert closed == []
    assert progress == ["Buffering torrent"]


@pytest.mark.parametrize("late", [False, True])
@pytest.mark.parametrize("failure", [PreparationCancelled, KeyboardInterrupt])
def test_cancelled_torrent_preparation_closes_unaccepted_resource_once(monkeypatch, late, failure):
    started, release = threading.Event(), threading.Event()
    closed, threads = [], []
    resource = SimpleNamespace(close=lambda: closed.append(True))

    def prepare(stream, **kwargs):
        started.set()
        if late:
            assert release.wait(2)
            assert kwargs["cancel_check"]()
        return resource

    def cancel(work, **kwargs):
        if late:
            thread = threading.Thread(target=work)
            threads.append(thread)
            thread.start()
            assert started.wait(1)
        else:
            assert work() is resource
        raise failure

    monkeypatch.setattr(runtime, "prepare_torrent_stream", prepare)
    monkeypatch.setattr(player_screen, "run_loading_work", cancel)
    try:
        with pytest.raises(runtime.TorrentCancelled if failure is PreparationCancelled else KeyboardInterrupt):
            engine._prepare_torrent_for_playback({"type": "torrent"})
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=1)
            assert not thread.is_alive()
    assert closed == [True]


def test_progress_report_uses_existing_sink_without_prefix(monkeypatch):
    seen = []
    with reporting.status_sink_scope(lambda message, color: seen.append((message, color)) or True):
        reporting.progress("Buffering torrent")
    assert seen == [("Buffering torrent", "\033[1;92m")]


def test_engine_injects_shared_torrent_preparation_into_desktop(monkeypatch):
    def desktop(*args, **kwargs):
        assert kwargs["prepare_torrent_fn"] is engine._prepare_torrent_for_playback
        assert kwargs["prepare_subtitles_fn"] is engine._prepare_subtitles_for_playback
        assert kwargs["prepare_proxy_fn"] is engine._prepare_proxy_for_playback
        assert kwargs["start_player_fn"] is engine._start_player_for_playback
        assert kwargs["playback_input_scope"] is engine._playback_input_scope
        return ("CANCELLED", 0, 0, 0, 0)

    monkeypatch.setattr(engine.desktop_playback, "play_desktop", desktop)
    assert engine.play_desktop("Example", "1", {"type": "torrent"})[0] == "CANCELLED"
