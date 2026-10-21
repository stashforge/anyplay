from types import SimpleNamespace

import pytest

from media_cli.app import playback_player
from media_cli.context import CliFlags, MachineState, UiState
from media_cli.ui import player_screen


@pytest.mark.parametrize("source_count", [1, 20])
def test_cached_stream_followup_runs_once_without_false_search_messages(monkeypatch, source_count):
    core = playback_player.app_core
    data = {"episode": {"sourceUrls": [{"sourceName": f"Mirror {i}"} for i in range(source_count)]}}
    streams = [{"source_name": f"Mirror {i} (720p)", "link": f"url-{i}"} for i in range(4)]
    show = {"_id": "one", "_provider": "example", "name": "Example"}
    ui = UiState(ui_show_ctx=show)
    ms = MachineState(show_id="one", current_ep="1", ep_cache_key=("one", "1", "sub", "example"), ep_cache_data=data)
    followups, messages = [], []
    monkeypatch.setattr(player_screen, "_player_ui_state", {})
    monkeypatch.setattr(player_screen, "reset_loading_cancel", lambda: None)
    monkeypatch.setattr(player_screen, "loading_cancel_requested", lambda: False)
    monkeypatch.setattr(core, "with_loading", lambda msg, work: work())
    monkeypatch.setattr(core, "prepare_show_display_state", lambda *a: None)
    monkeypatch.setattr(core, "ensure_episode_ids", lambda *a: ["1"])
    monkeypatch.setattr(core, "get_show_anilist_id", lambda *a: None)
    monkeypatch.setattr(core, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(core, "is_termux", lambda: False)
    monkeypatch.setattr(core, "_ipc_player", SimpleNamespace(prefetched_ep=None, prefetched_res=None))
    monkeypatch.setattr(core, "_get_cached_ep_data", lambda *a: data)
    monkeypatch.setattr(core, "get_preferred_mirror", lambda *a: {})
    monkeypatch.setattr(core, "info", messages.append)
    monkeypatch.setattr(core, "start_bg_resolve", lambda data, excluded, **kw: followups.append(set(excluded)))
    monkeypatch.setattr(playback_player.streams_mod, "start_bg_resolve", lambda *a: None)
    monkeypatch.setattr(playback_player.streams_mod, "_bg_thread", None)
    monkeypatch.setattr(playback_player, "_lookup_player_sources", lambda *a, **kw: data)
    monkeypatch.setattr(playback_player, "_local_stream_for_episode", lambda *a: None)
    monkeypatch.setattr(playback_player, "_wait_for_player_streams", lambda *a, **kw: streams)
    monkeypatch.setattr(playback_player, "_probe_player_mirrors", lambda candidates, *a, **kw: candidates[0])

    class SelectionReady(Exception):
        pass

    def stop_after_selection(*args, **kwargs):
        raise SelectionReady

    monkeypatch.setattr(core, "_extend_streams", stop_after_selection)
    args = SimpleNamespace(aniskip=False, download=False, print_url=False, sources=False)
    with pytest.raises(SelectionReady):
        playback_player.handle_play_state(CliFlags(), ui, ms, {}, args, "sub", "best", lambda *a: False)
    assert messages == []
    assert len(followups) == (1 if source_count == 20 else 0)
    if followups:
        assert all(f"Mirror {i}" in followups[0] for i in range(4))
