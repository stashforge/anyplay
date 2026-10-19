import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from media_cli.context import FLAGS
from media_cli.ui import display, loading_preparation, player_screen, terminal_runtime
from media_cli.ui.transition import PreparationCancelled


@pytest.fixture
def player(monkeypatch):
    monkeypatch.setattr(FLAGS, "plain_mode", False)
    monkeypatch.setattr(player_screen, "sys", SimpleNamespace(
        stdin=SimpleNamespace(isatty=lambda: True), stdout=SimpleNamespace(isatty=lambda: True),
    ))
    monkeypatch.setattr(display, "is_alt_screen_active", lambda: True)
    monkeypatch.setattr(player_screen, "_player_ui_state", {
        "active": True, "mpv_props": None, "status_lines": [],
        "loading_provider_name": "Example", "loading_start_time": time.time() - 2,
    })
    monkeypatch.setattr(player_screen, "_loading_cancel_event", threading.Event())
    monkeypatch.setattr(player_screen, "_loading_quit_event", threading.Event())
    events = []

    @contextmanager
    def terminal():
        events.append("borrow")
        yield SimpleNamespace(fileno=lambda: 10)
        events.append("release")

    monkeypatch.setattr(terminal_runtime, "borrow_terminal", terminal)

    class Keys:
        def __init__(self, fd, *, on_key, cancel_keys=()):
            self.on_key = on_key
            self.has_pending_sequence = False

        def poll(self):
            return None

    monkeypatch.setattr(loading_preparation, "LoadingInput", Keys)
    return events


def test_loading_work_owns_main_thread_render_without_resuming_ticker(player, monkeypatch):
    from media_cli.core import reporting

    owner = threading.current_thread()
    release = threading.Event()
    frames = []

    def work():
        assert threading.current_thread() is not owner
        reporting.info("Fetching episode")
        assert release.wait(2)
        return {"sources": []}

    def render(**kwargs):
        assert threading.current_thread() is owner
        snapshot = kwargs["loading_snapshot"]
        frames.append(snapshot)
        release.set()

    monkeypatch.setattr(player_screen, "render", render)
    try:
        assert player_screen.run_loading_work(work) == {"sources": []}
    finally:
        release.set()
    assert player == ["borrow", "release"]
    assert frames[0].elapsed >= 2
    assert "[INFO] Fetching episode" in player_screen._player_ui_state["status_lines"][0]


@pytest.mark.parametrize("failure", [PreparationCancelled, ValueError, KeyboardInterrupt])
def test_loading_work_does_not_resume_ticker_on_failure(player, monkeypatch, failure):
    from media_cli.ui import player_preparation

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(player_preparation, "run_player_preparation", fail)
    with pytest.raises(failure):
        player_screen.run_loading_work(lambda: None)
    assert "resume" not in player
    if failure is PreparationCancelled:
        assert player_screen._player_ui_state["loading_cancel_requested"] is True


def test_plain_loading_work_does_not_borrow_terminal(player, monkeypatch):
    monkeypatch.setattr(FLAGS, "plain_mode", True)
    owner = threading.current_thread()
    assert player_screen.run_loading_work(threading.current_thread) is owner
    assert player == []


@pytest.mark.parametrize("quit_requested", [False, True])
def test_loading_work_preserves_input_received_before_handoff(player, quit_requested):
    if quit_requested:
        player_screen._loading_quit_event.set()
    else:
        player_screen.request_loading_cancel()
    with pytest.raises(KeyboardInterrupt if quit_requested else PreparationCancelled):
        player_screen.run_loading_work(lambda: pytest.fail("Work must not start"))
    assert player == []


def test_loading_work_uses_supplied_task_without_resuming_legacy_reader(player, monkeypatch):
    from media_cli.ui import player_preparation
    from media_cli.ui.actions import ActionAvailability, ActionBindings, ResolvedAction
    from media_cli.ui.loading_model import LoadingInteraction, LoadingOwner, LoadingPlacement, LoadingTaskSpec
    from media_cli.ui.view_model import ViewAction

    task = LoadingTaskSpec(
        "launch", LoadingOwner("player", 0), "Opening player...", LoadingInteraction.BLOCKING,
        LoadingPlacement.PLAYER_STATUS,
        ActionBindings((ResolvedAction(ViewAction("back", "Cancel", ("q", "Q", "ESC")),
                                       ActionAvailability.BLOCKED, "Already opening the player."),)),
    )

    def prepare(spec, work, **kwargs):
        assert spec is task
        assert player_screen._loading_cancel_hint(SimpleNamespace(actions=spec.actions)) == "Ctrl+C stops Anyplay"
        return work(lambda text: None)

    monkeypatch.setattr(player_preparation, "run_player_preparation", prepare)
    assert player_screen.run_loading_work(lambda: "opened", task=task) == "opened"
    assert player == ["borrow", "release"]


def test_non_cancellable_loading_consumes_q_and_esc_but_keeps_pumping(player, monkeypatch):
    from media_cli.ui.actions import ActionAvailability, ActionBindings, ResolvedAction
    from media_cli.ui.loading_model import LoadingInteraction, LoadingOwner, LoadingPlacement, LoadingTaskSpec
    from media_cli.ui.view_model import ViewAction

    task = LoadingTaskSpec(
        "launch", LoadingOwner("player", 0), "Opening player...", LoadingInteraction.BLOCKING,
        LoadingPlacement.PLAYER_STATUS,
        ActionBindings((ResolvedAction(ViewAction("back", "Cancel", ("q", "Q", "ESC")),
                                       ActionAvailability.BLOCKED, "Already opening the player."),)), delay=0,
    )
    keys, frames = ["q", "ESC"], []
    release = threading.Event()

    class Keys:
        def __init__(self, fd, *, on_key, cancel_keys):
            self.on_key = on_key
            self.has_pending_sequence = False

        def poll(self):
            if keys:
                self.on_key(keys.pop(0))

    def render(**kwargs):
        frames.append(kwargs["loading_snapshot"])
        if not keys:
            release.set()

    monkeypatch.setattr(loading_preparation, "LoadingInput", Keys)
    monkeypatch.setattr(player_screen, "render", render)
    try:
        assert player_screen.run_loading_work(lambda: release.wait(2), task=task)
    finally:
        release.set()
    assert not keys
    assert len(frames) >= 2
    assert not player_screen._loading_cancel_event.is_set()
    assert "resume" not in player


def test_production_code_never_starts_legacy_player_ticker():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "media_cli"
    calls = []
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name == "start_loading_ticker":
                calls.append(f"{path.relative_to(root)}:{node.lineno}")
    assert not calls, f"Legacy loading threads restarted by production code: {calls}"
