import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from media_cli.app import playback_player
from media_cli.ui import player_screen, transition
from media_cli.ui.actions import ActionAvailability
from media_cli.ui.message_choices import MAX_MESSAGE_LENGTH


def test_countdown_waits_on_worker_and_updates_status_without_mutating_screen(monkeypatch):
    owner = threading.current_thread()
    messages = []
    state = {"active": True, "status_lines": ["existing"]}
    before = deepcopy(state)
    monkeypatch.setattr(player_screen, "_player_ui_state", state)

    def update(message):
        assert threading.current_thread() is not owner
        messages.append(message)

    def pump(work, *, task, **kwargs):
        assert task.message == "Starting 'Example' (720p) in 3s..."
        for key in ("q", "Q", "ESC"):
            assert task.actions.resolve(key).availability is ActionAvailability.AVAILABLE
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(work).result(timeout=5)

    monkeypatch.setattr(transition, "set_preparation_status", update)
    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    playback_player._wait_for_android_start("Example", " (720p)")
    assert messages == [f"Starting 'Example' (720p) in {second}s..." for second in (3, 2, 1)]
    assert state == before


@pytest.mark.parametrize("failure", [transition.PreparationCancelled, KeyboardInterrupt])
def test_cancelled_countdown_retires_worker_without_late_status_updates(monkeypatch, failure):
    started = threading.Event()
    messages, pending = [], []

    def update(message):
        messages.append(message)
        started.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        def pump(work, **kwargs):
            pending.append(pool.submit(work))
            assert started.wait(1)
            raise failure

        monkeypatch.setattr(transition, "set_preparation_status", update)
        monkeypatch.setattr(player_screen, "run_loading_work", pump)
        with pytest.raises(failure):
            playback_player._wait_for_android_start("Example", "")
        pending[0].result(timeout=0.5)
    assert messages == ["Starting 'Example' in 3s..."]


def test_countdown_status_sanitizes_provider_labels_and_respects_message_limit(monkeypatch):
    tasks = []

    def pump(work, *, task, **kwargs):
        tasks.append(task)

    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    playback_player._wait_for_android_start("\033[31m" + "source" * 50, "\n (1080p)")
    message = tasks[0].message
    assert len(message) <= MAX_MESSAGE_LENGTH
    assert "\033" not in message and "\n" not in message
    assert message.endswith(" in 3s...")
