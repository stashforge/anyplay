import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from media_cli.ui import player_screen


@pytest.mark.parametrize("cancelled", [False, True])
def test_cancel_check_reads_state_without_polling(monkeypatch, cancelled):
    event = threading.Event()
    if cancelled:
        event.set()
    monkeypatch.setattr(player_screen, "_loading_cancel_event", event)
    monkeypatch.setattr(player_screen, "_loading_quit_event", threading.Event())
    monkeypatch.setattr(player_screen, "_player_ui_state", {})
    monkeypatch.setattr(player_screen.os, "read", lambda *a: pytest.fail("State query must not read input"))
    assert player_screen.loading_cancel_requested() is cancelled


@pytest.mark.parametrize("state", ["idle", "event", "flag", "quit"])
def test_worker_can_inspect_cancel_state_without_acquiring_terminal(monkeypatch, state):
    cancelled, quit_event = threading.Event(), threading.Event()
    if state == "event":
        cancelled.set()
    elif state == "quit":
        quit_event.set()
        cancelled.set()
    monkeypatch.setattr(player_screen, "_loading_cancel_event", cancelled)
    monkeypatch.setattr(player_screen, "_loading_quit_event", quit_event)
    monkeypatch.setattr(player_screen, "_player_ui_state", {"loading_cancel_requested": state == "flag"})
    monkeypatch.setattr(player_screen.os, "read", lambda *a: pytest.fail("Worker tried to read terminal"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(player_screen.loading_cancel_requested)
        if state == "quit":
            with pytest.raises(KeyboardInterrupt):
                future.result(timeout=1)
        else:
            assert future.result(timeout=1) is (state != "idle")




