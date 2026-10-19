from types import SimpleNamespace

import pytest

from media_cli.context import CliFlags, MachineState, UiState


def test_playback_entry_preparation_stays_under_loading(monkeypatch):
    from media_cli.app import playback_player

    core = playback_player.app_core
    active = []

    def loading(message, fn):
        active.append(True)
        try:
            return fn()
        finally:
            active.pop()

    def prepare(*args):
        assert active

    def episodes(*args):
        assert active
        return []

    monkeypatch.setattr(core, "with_loading", loading)
    monkeypatch.setattr(core, "prepare_show_display_state", prepare)
    monkeypatch.setattr(core, "ensure_episode_ids", episodes)
    monkeypatch.setattr(core, "_exit_player_screen", lambda: None)
    monkeypatch.setattr(core, "err", lambda value: None)
    assert (
        playback_player.handle_play_state(
            CliFlags(),
            UiState(ui_show_ctx={"_id": "one"}),
            MachineState(),
            {},
            SimpleNamespace(),
            "sub",
            "1080p",
            lambda *args: False,
        )
        == "DETAILS"
    )


def test_cancelled_playback_entry_does_not_commit_late_metadata(monkeypatch):
    from media_cli.app import playback_player
    from media_cli.ui.transition import PreparationCancelled

    core = playback_player.app_core
    ui = UiState(ui_show_ctx={"_id": "one"})

    def loading(message, fn):
        fn()
        raise PreparationCancelled

    monkeypatch.setattr(core, "with_loading", loading)
    monkeypatch.setattr(core, "prepare_show_display_state", lambda show, *a: show.update(name="Late"))
    monkeypatch.setattr(core, "ensure_episode_ids", lambda *a: ["1"])
    assert (
        playback_player.handle_play_state(
            CliFlags(), ui, MachineState(), {}, SimpleNamespace(), "sub", "1080p", lambda *args: False
        )
        == "DETAILS"
    )
    assert ui.ui_show_ctx == {"_id": "one"}


@pytest.mark.parametrize("cancel_stage", ["fetch", "sources", "cached_sources"])
def test_episode_fetch_uses_player_pump_and_cancel_does_not_commit_cache(monkeypatch, cancel_stage):
    import threading

    from media_cli.app import playback_player
    from media_cli.ui import player_screen
    from media_cli.ui.transition import PreparationCancelled

    core = playback_player.app_core
    ui = UiState(ui_show_ctx={"_id": "one", "_provider": "example", "name": "Example"})
    ms = MachineState(current_ep="1")
    original_data = {"sources": []}
    original_key = ("one", "1", "sub", "example") if cancel_stage == "cached_sources" else None
    ms.ep_cache_key = original_key
    ms.ep_cache_data = original_data if original_key else None
    calls = []

    def pump(prepare, **kwargs):
        calls.append("pump")
        result = prepare()
        if cancel_stage == "sources" and calls == ["pump"]:
            assert result == {"sources": []}
            return result
        player_screen.request_loading_cancel()
        raise PreparationCancelled

    def augment(data, *args, **kwargs):
        assert cancel_stage != "fetch", "Cancelled data used"
        data["sources"].append("new")
        return data

    monkeypatch.setattr(player_screen, "_player_ui_state", {})
    monkeypatch.setattr(player_screen, "_loading_cancel_event", threading.Event())
    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    monkeypatch.setattr(player_screen, "reset_loading_cancel", lambda: None)
    monkeypatch.setattr(player_screen, "loading_cancel_requested", lambda: True)
    monkeypatch.setattr(core, "with_loading", lambda message, prepare: prepare())
    monkeypatch.setattr(core, "prepare_show_display_state", lambda *a: None)
    monkeypatch.setattr(core, "ensure_episode_ids", lambda *a: ["1"])
    monkeypatch.setattr(core, "get_show_anilist_id", lambda *a: None)
    monkeypatch.setattr(core, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(core, "get_episode_data", lambda *a, **kw: {"sources": []})
    monkeypatch.setattr(core, "is_termux", lambda: False)
    monkeypatch.setattr(core, "_exit_player_screen", lambda: calls.append("exit"))
    monkeypatch.setattr(core, "set_action_feedback", lambda *a: None)
    monkeypatch.setattr(core, "info", lambda *a: None)
    monkeypatch.setattr(core, "_ipc_player", None)
    monkeypatch.setattr(playback_player.playback_mod, "_augment_episode_sources_with_torrents", augment)
    assert playback_player.handle_play_state(
        CliFlags(), ui, ms, {}, SimpleNamespace(aniskip=False), "sub", "1080p", lambda *a: False,
    ) == "DETAILS"
    assert calls == (["pump", "pump", "exit"] if cancel_stage == "sources" else ["pump", "exit"])
    assert ms.ep_cache_key == original_key
    assert ms.ep_cache_data == (original_data if original_key else None)
    assert original_data == {"sources": []}
