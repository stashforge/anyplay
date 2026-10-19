import threading
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from types import SimpleNamespace

import pytest

from media_cli.app import playback_player
from media_cli.media import aniskip, custom_skips
from media_cli.ui import player_screen
from media_cli.ui.transition import PreparationCancelled


@pytest.mark.parametrize("android,expected", [
    (False, "Awaiting video duration to validate skip times"),
    (True, "Skip times ready; duration not checked"),
])
def test_pending_skip_status_matches_platform_duration_policy(android, expected):
    assert playback_player._pending_skip_validation_status(android=android) == expected


@pytest.mark.parametrize("source", ["direct", "future", "timeout", "error"])
def test_skip_loading_runs_wait_or_fetch_on_worker(monkeypatch, source):
    owner = threading.current_thread()
    skips = [{"type": "op"}]

    def fetch(mal_id, episode):
        assert threading.current_thread() is not owner
        assert (mal_id, episode) == (123, 1)
        return skips

    class Pending:
        def result(self, *, timeout):
            assert threading.current_thread() is not owner
            assert timeout == 4.0
            if source == "timeout":
                raise TimeoutError
            if source == "error":
                raise ValueError("Network failed")
            return skips

    def pump(work, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(work).result(timeout=1)

    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    monkeypatch.setattr(aniskip, "fetch_skip_times", fetch)
    assert playback_player._load_player_skips(123, 1, None if source == "direct" else Pending()) == (
        [] if source in ("timeout", "error") else skips
    )


@pytest.mark.parametrize("custom", [False, True])
def test_skip_cancel_returns_to_details_and_custom_skips_bypass_wait(monkeypatch, custom):
    from media_cli.context import CliFlags, MachineState, UiState
    from media_cli.core import workers

    core = playback_player.app_core
    data = {"episode": {"sourceUrls": []}}
    show = {"_id": "one", "_provider": "torrent", "name": "Example"}
    ui = UiState(ui_show_ctx=show)
    ms = MachineState(current_ep="1", selected_stream={"link": "file", "source_name": "Local File"},
                      ep_cache_key=("one", "1", "sub", "torrent"), ep_cache_data=data)
    calls = []

    class Pool:
        def __init__(self, **kw):
            pass

        def submit(self, *a):
            return Future()

        def shutdown(self, *, wait):
            assert not wait
            calls.append("shutdown")

    def wait(*args, **kwargs):
        assert not custom, "Custom skips must bypass remote data waiting"
        calls.append("wait")
        raise PreparationCancelled

    monkeypatch.setattr(player_screen, "_player_ui_state", {})
    monkeypatch.setattr(player_screen, "reset_loading_cancel", lambda: None)
    monkeypatch.setattr(player_screen, "loading_cancel_requested", lambda: False)
    monkeypatch.setattr(workers, "DaemonExecutor", Pool)
    monkeypatch.setattr(core, "with_loading", lambda msg, work: work())
    monkeypatch.setattr(core, "prepare_show_display_state", lambda *a: None)
    monkeypatch.setattr(core, "ensure_episode_ids", lambda *a: ["1"])
    monkeypatch.setattr(core, "get_show_anilist_id", lambda *a: None)
    monkeypatch.setattr(core, "get_show_mal_id", lambda *a, **kw: 123)
    monkeypatch.setattr(core, "get_custom_skips", lambda *a: {})
    monkeypatch.setattr(core, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(core, "_ipc_player", None)
    monkeypatch.setattr(core, "_exit_player_screen", lambda *a, **kw: calls.append("exit"))
    monkeypatch.setattr(core, "set_action_feedback", lambda *a: calls.append("feedback"))
    monkeypatch.setattr(core, "is_termux", lambda: False)
    def play(*args, **kwargs):
        assert custom, "Cancelled preparation started playback"
        return ("CANCELLED", 0, 0, 0, 0)

    monkeypatch.setattr(core, "play_desktop", play)
    monkeypatch.setattr(playback_player, "trigger_background_auto_download", lambda *a: None)
    monkeypatch.setattr(custom_skips, "resolve_custom_skips", lambda **kw: {"intervals": [], "mode": "saved"} if custom else None)
    monkeypatch.setattr(playback_player, "_load_player_skips", wait)
    args = SimpleNamespace(aniskip=True, download=False, print_url=False, sources=False, binge=False, auto_skip=False)
    assert playback_player.handle_play_state(
        CliFlags(), ui, ms, {}, args, "sub", "best", lambda *a: False,
    ) == "DETAILS"
    if not custom:
        assert calls == ["wait", "exit", "feedback", "shutdown"]
    else:
        assert any("Custom Skips" in line for line in player_screen._player_ui_state["status_lines"])
