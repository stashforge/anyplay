import threading
from types import SimpleNamespace

import pytest

from media_cli.app import playback_player
from media_cli.context import CliFlags, MachineState, UiState
from media_cli.ui import player_screen
from media_cli.ui.transition import PreparationCancelled


@pytest.mark.parametrize("outcome", ["success", "cancel", "empty", "cache_cancel"])
@pytest.mark.parametrize("result", ["NEXT_MIRROR", "ERROR"])
@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize('quality_first', [False, True, 'pending'])
def test_recovery_fallback_owns_loading_frame_and_preserves_exclusions(monkeypatch, outcome, result, cached, quality_first):
    core = playback_player.app_core
    show = {"_id": "one", "_provider": "torrent", "name": "Example"}
    ui = UiState(ui_show_ctx=show)
    failed = {"source_name": "Failed", "link": "failed-url"}
    replacement = {"source_name": "Replacement", "link": "replacement-url"}
    lower = {'source_name': 'Hardsub English Rumble 720p', 'resolution': '720p', 'link': 'lower-url'}
    if quality_first:
        show['_provider'] = 'animexin'
        replacement.update(source_name='All Sub Dailymotion 1080p', resolution='1080p')
    data = {"episode": {"sourceUrls": []}}
    ms = MachineState(current_ep="1", selected_stream=failed,
                      ep_cache_key=("one", "1", "sub", show['_provider']), ep_cache_data=data)
    calls = []
    monkeypatch.setattr(player_screen, "_player_ui_state", {})
    monkeypatch.setattr(player_screen, "_loading_cancel_event", threading.Event())
    monkeypatch.setattr(player_screen, "_loading_quit_event", threading.Event())
    monkeypatch.setattr(player_screen, "loading_cancel_requested", lambda: False)
    monkeypatch.setattr(core, "with_loading", lambda message, fn: fn())
    monkeypatch.setattr(core, "prepare_show_display_state", lambda *a: None)
    monkeypatch.setattr(core, "ensure_episode_ids", lambda *a: ["1"])
    monkeypatch.setattr(core, "get_show_anilist_id", lambda *a: None)
    monkeypatch.setattr(core, "get_show_mal_id", lambda *a, **kw: None)
    monkeypatch.setattr(core, "get_custom_skips", lambda *a: {})
    monkeypatch.setattr(core, "is_termux", lambda: False)
    monkeypatch.setattr(playback_player, '_lookup_player_sources', lambda *a, **kw: data)
    monkeypatch.setattr(core, "enter_alt_screen", lambda: calls.append("enter"))

    def exit_screen(*args, **kwargs):
        player_screen._player_ui_state["active"] = False
        calls.append("exit")

    monkeypatch.setattr(core, "_exit_player_screen", exit_screen)
    monkeypatch.setattr(core, "play_desktop", lambda *a, **kw: (result, 0, 10, 1000, 5))
    monkeypatch.setattr(core, "_ipc_player", SimpleNamespace(send_cmd=lambda *a: None, quit=lambda: None))
    monkeypatch.setattr(core, "_get_cached_ep_data", lambda *a: data)
    monkeypatch.setattr(playback_player.streams_mod, 'background_resolution_pending',
                        lambda key: quality_first == 'pending' and cached)

    def snapshot(*args, **kwargs):
        assert player_screen._player_ui_state["active"]
        calls.append("cache")
        if outcome == "cache_cancel":
            raise PreparationCancelled
        if quality_first == 'pending' and cached and calls.count('cache') == 1:
            return [failed, lower]
        return ([failed, lower, replacement] if quality_first else [failed, replacement]) if cached else []

    def pump(work, **kwargs):
        return work()

    monkeypatch.setattr(core, "_stream_snapshot", snapshot)
    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    monkeypatch.setattr(core, "save_resume_time", lambda *a: None)
    monkeypatch.setattr(core, "info", lambda *a: None)
    monkeypatch.setattr(core, "warn", lambda *a: None)
    monkeypatch.setattr(core, "_prune_dead_stream", lambda *a: None)
    monkeypatch.setattr(core, "set_action_feedback", lambda *a: None)
    monkeypatch.setattr(core, "_extend_streams", lambda *a, **kw: calls.append("commit"))
    monkeypatch.setattr(playback_player, "trigger_background_auto_download", lambda *a: None)

    def fallback(data_arg, key, **kwargs):
        assert player_screen._player_ui_state["active"]
        assert player_screen._player_ui_state["mpv_props"] is None
        assert kwargs["exclude_sources"] == frozenset(("Failed", "failed-url"))
        calls.append("fallback")
        if outcome == "cancel":
            raise PreparationCancelled
        return (replacement, "Replacement", data_arg, [replacement]) if outcome == "success" else None

    monkeypatch.setattr(playback_player, "_fetch_player_fallback", fallback)
    monkeypatch.setattr(core, "fetch_episode_stream", lambda *a, **kw: pytest.fail("Recovery bypassed shared helper"))

    def probe(candidates, key, **kwargs):
        assert player_screen._player_ui_state["active"]
        assert kwargs["recovery"] is True
        assert candidates == ((replacement, lower) if quality_first else (replacement,))
        calls.append("probe")
        if outcome == "cancel":
            raise PreparationCancelled
        return replacement if outcome == "success" else None

    monkeypatch.setattr(playback_player, "_probe_player_mirrors", probe)
    monkeypatch.setattr(core, "ping_stream_liveness", lambda *a, **kw: pytest.fail("Recovery bypassed shared probe"))
    args = SimpleNamespace(download=False, print_url=False, sources=False, binge=False, aniskip=False, auto_skip=False)
    if quality_first:
        args.quality = '1080p'
    state = playback_player.handle_play_state(CliFlags(), ui, ms, {}, args, "sub", "best", lambda *a: False)
    assert state == ("PLAY" if outcome == "success" else "DETAILS")
    assert not player_screen._player_ui_state["active"]
    work = ["cache"]
    if quality_first == 'pending' and cached and outcome != 'cache_cancel':
        work.append('cache')
    work += (["probe"] if cached and outcome != "cache_cancel" else [])
    if outcome != "cache_cancel" and (not cached or outcome == "empty"):
        work.append("fallback")
    assert calls == ["enter", "exit", "enter"] + work + ["exit"] + (["commit"] if outcome == "success" and not cached else [])
    if outcome in ("cancel", "cache_cancel"):
        assert ms.selected_stream is None
