import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from media_cli.app import playback_player
from media_cli.ui import player_screen
from media_cli.ui.transition import PreparationCancelled

KEY = ("show", "1", "sub", "example", "best")


@pytest.mark.parametrize('higher_arrives', [True, False])
def test_recovery_exhausts_pending_sources_before_lowering_quality(monkeypatch, higher_arrives):
    key = (*KEY[:4], '1080p')
    failed = {'source_name': 'Failed', 'resolution': '1080p', 'link': 'failed-url'}
    lower = {'source_name': 'Rumble', 'resolution': '720p', 'link': 'lower-url'}
    higher = {'source_name': 'Ok.ru', 'resolution': '1080p', 'link': 'higher-url'}
    calls, clock = [], [0.0]

    def snapshot(*args):
        calls.append(True)
        clock[0] = (0.0, 11.0, 12.0)[min(len(calls)-1, 2)]
        return [failed, lower, higher] if higher_arrives and len(calls) >= 3 else [failed, lower]

    monkeypatch.setattr(playback_player.app_core, '_stream_snapshot', snapshot)
    monkeypatch.setattr(playback_player.app_core, 'get_preferred_mirror',
                        lambda *a: {'source_name': 'Failed', 'resolution': '1080p'})
    monkeypatch.setattr(playback_player.streams_mod, '_active_stream_key', key)
    monkeypatch.setattr(playback_player.streams_mod, '_bg_generation', playback_player.streams_mod._streams_generation)
    monkeypatch.setattr(playback_player.streams_mod, '_bg_thread',
                        SimpleNamespace(is_alive=lambda: higher_arrives or len(calls) < 3))
    monkeypatch.setattr(playback_player.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(player_screen, 'run_loading_work', lambda work, **kw: work())
    ready = playback_player._wait_for_player_streams(
        {}, key, exclude_sources=frozenset({'Failed', 'failed-url'}), require_exhausted=True,
    )
    assert ready == ([lower, higher] if higher_arrives else [lower])
    assert len(calls) == 3


@pytest.mark.parametrize('higher_arrives', [True, False])
def test_existing_background_resolution_waits_for_configured_quality(monkeypatch, higher_arrives):
    key = (*KEY[:4], '1080p')
    lower = {'source_name': 'Rumble', 'resolution': '720p'}
    higher = {'source_name': 'Dailymotion', 'resolution': '1080p'}
    clock = [0.0]
    calls = []

    def snapshot(*args):
        calls.append(True)
        clock[0] = (0.0, 4.0, 5.0 if higher_arrives else 10.1)[min(len(calls)-1, 2)]
        return [lower, higher] if higher_arrives and len(calls) >= 3 else [lower]

    monkeypatch.setattr(playback_player.app_core, '_stream_snapshot', snapshot)
    monkeypatch.setattr(playback_player.app_core, 'get_preferred_mirror', lambda *a: {})
    monkeypatch.setattr(playback_player.streams_mod, '_active_stream_key', key)
    monkeypatch.setattr(playback_player.streams_mod, '_bg_generation', playback_player.streams_mod._streams_generation)
    monkeypatch.setattr(playback_player.streams_mod, '_bg_thread', SimpleNamespace(is_alive=lambda: True))
    monkeypatch.setattr(playback_player.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(player_screen, 'run_loading_work', lambda work, **kw: work())
    ready = playback_player._wait_for_player_streams({}, key)
    assert higher in ready if higher_arrives else ready == [lower]
    assert len(calls) == 3


def test_first_stream_wait_uses_player_pump_and_returns_ready_sources(monkeypatch):
    owner = threading.current_thread()
    stream = {"source_name": "Ready"}
    polls = []

    def snapshot(*args):
        assert threading.current_thread() is not owner
        assert args[:5] == KEY
        polls.append(True)
        if len(polls) == 2:
            monkeypatch.setattr(playback_player.streams_mod, '_bg_thread', None)
        return [stream] if len(polls) == 2 else []

    def pump(work, **kwargs):
        assert kwargs == {"poster_manager": "poster", "ui": "ui"}
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(work).result(timeout=1)

    monkeypatch.setattr(playback_player.app_core, "_stream_snapshot", snapshot)
    monkeypatch.setattr(playback_player.streams_mod, '_active_stream_key', KEY)
    monkeypatch.setattr(playback_player.streams_mod, '_bg_generation', playback_player.streams_mod._streams_generation)
    monkeypatch.setattr(playback_player.streams_mod, '_bg_thread', SimpleNamespace(is_alive=lambda: True))
    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    assert playback_player._wait_for_player_streams(
        {}, KEY, poster_manager="poster", ui="ui",
    ) == [stream]
    assert len(polls) == 2


@pytest.mark.parametrize('other_background', [False, True])
def test_empty_cache_returns_without_five_second_preflight(monkeypatch, other_background):
    polls = []
    monkeypatch.setattr(player_screen, "run_loading_work", lambda work, **kw: work())
    monkeypatch.setattr(playback_player.app_core, "_stream_snapshot", lambda *a: polls.append(True) or [])
    monkeypatch.setattr(playback_player.streams_mod, '_active_stream_key', ('other', *KEY[1:]))
    monkeypatch.setattr(playback_player.streams_mod, '_bg_thread',
                        SimpleNamespace(is_alive=lambda: True) if other_background else None)
    assert playback_player._wait_for_player_streams({}, KEY) == []
    assert polls == [True]


def test_cancelled_stream_wait_stops_polling_and_retry_uses_new_scope(monkeypatch):
    started, release = threading.Event(), threading.Event()
    threads, polls = [], []

    def snapshot(*args):
        polls.append(True)
        started.set()
        assert release.wait(2)
        return []

    def cancel(work, **kwargs):
        thread = threading.Thread(target=work)
        threads.append(thread)
        thread.start()
        assert started.wait(1)
        raise PreparationCancelled

    monkeypatch.setattr(playback_player.app_core, "_stream_snapshot", snapshot)
    monkeypatch.setattr(player_screen, "run_loading_work", cancel)
    try:
        with pytest.raises(PreparationCancelled):
            playback_player._wait_for_player_streams({}, KEY)
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=1)
            assert not thread.is_alive()
    assert len(polls) == 1
    monkeypatch.setattr(playback_player.app_core, "_stream_snapshot", lambda *a: [{"source_name": "Retry"}])
    monkeypatch.setattr(player_screen, "run_loading_work", lambda work, **kw: work())
    assert playback_player._wait_for_player_streams({}, KEY) == [{"source_name": "Retry"}]


def test_download_stream_wait_does_not_enter_player_ui(monkeypatch):
    owner = threading.current_thread()

    def snapshot(*args):
        assert threading.current_thread() is owner
        return [{"source_name": "Download"}]

    monkeypatch.setattr(playback_player.app_core, "_stream_snapshot", snapshot)
    monkeypatch.setattr(player_screen, "run_loading_work", lambda *a, **kw: pytest.fail("Download entered player UI"))
    assert playback_player._wait_for_player_streams({}, KEY, download=True) == [{"source_name": "Download"}]


@pytest.mark.parametrize("cancel_stage", ["fetch", "lookup", "wait", "probe", "fallback"])
def test_playback_returns_to_details_without_selecting_stream_after_resolution_cancel(monkeypatch, cancel_stage):
    from media_cli.context import CliFlags, MachineState, UiState

    core = playback_player.app_core
    show = {"_id": "one", "_provider": "example", "name": "Example"}
    ui = UiState(ui_show_ctx=show)
    data = {"episode": {"sourceUrls": []}}
    initial_data = None if cancel_stage == "fetch" else data
    ms = MachineState(current_ep="1", ep_cache_key=("one", "1", "sub", "example"), ep_cache_data=initial_data)
    calls = []
    lookups = []
    owner = threading.current_thread()

    def cancel(*args, **kwargs):
        calls.append("wait")
        if cancel_stage == "probe":
            return [{"source_name": "Example", "link": "stream"}]
        if cancel_stage == "fallback":
            return []
        raise PreparationCancelled

    def cancel_probe(*args, **kwargs):
        calls.append("probe")
        raise PreparationCancelled

    def cancel_fallback(*args, **kwargs):
        calls.append("fallback")
        raise PreparationCancelled

    def lookup(*args, **kwargs):
        lookups.append(True)
        calls.append("lookup")
        if cancel_stage == "lookup":
            raise PreparationCancelled
        return data

    def fetch(*args, **kwargs):
        assert threading.current_thread() is not owner
        assert args == ("one", "1", "sub")
        assert kwargs == {"provider_id": "example"}
        calls.append("fetch")
        return data

    def pump(work, **kwargs):
        calls.append("fetch-pump")
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(work).result(timeout=1) == data
        raise PreparationCancelled

    monkeypatch.setattr(player_screen, "_player_ui_state", {})
    monkeypatch.setattr(player_screen, "reset_loading_cancel", lambda: None)
    cancelled = [False]
    monkeypatch.setattr(player_screen, "request_loading_cancel", lambda: cancelled.__setitem__(0, True))
    monkeypatch.setattr(player_screen, "loading_cancel_requested", lambda: cancelled[0])
    monkeypatch.setattr(core, "with_loading", lambda message, work: work())
    monkeypatch.setattr(core, "prepare_show_display_state", lambda *a: None)
    monkeypatch.setattr(core, "ensure_episode_ids", lambda *a: ["1"])
    monkeypatch.setattr(core, "get_show_anilist_id", lambda *a: None)
    monkeypatch.setattr(core, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(core, "_ipc_player", SimpleNamespace(prefetched_ep=None, prefetched_res=None))
    monkeypatch.setattr(core, "_get_cached_ep_data", lambda *a: None if cancel_stage == "fetch" else data)
    monkeypatch.setattr(core, "get_episode_data", fetch)
    monkeypatch.setattr(core, "is_termux", lambda: False)
    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    monkeypatch.setattr(playback_player, "_lookup_player_sources", lookup)
    monkeypatch.setattr(core, "_exit_player_screen", lambda: calls.append("exit"))
    monkeypatch.setattr(core, "set_action_feedback", lambda *a: calls.append("feedback"))
    monkeypatch.setattr(core, "_set_cached_ep_data", lambda *a: pytest.fail("Cancelled fallback committed cache"))
    monkeypatch.setattr(core, "get_preferred_mirror", lambda *a: {} if cancel_stage == "probe" else pytest.fail("Cancelled wait reached selection"))
    monkeypatch.setattr(playback_player, "_local_stream_for_episode", lambda *a: None)
    monkeypatch.setattr(playback_player.streams_mod, "start_bg_resolve", lambda *a: calls.append("resolve"))
    monkeypatch.setattr(playback_player, "_wait_for_player_streams", cancel)
    monkeypatch.setattr(playback_player, "_probe_player_mirrors", cancel_probe)
    monkeypatch.setattr(playback_player, "_fetch_player_fallback", cancel_fallback)
    assert playback_player.handle_play_state(
        CliFlags(), ui, ms, {}, SimpleNamespace(aniskip=False), "sub", "best", lambda *a: False,
    ) == "DETAILS"
    expected = {
        "fetch": ["fetch-pump", "fetch"],
        "lookup": ["lookup"],
        "wait": ["lookup", "wait"],
        "probe": ["lookup", "wait", "probe"],
        "fallback": ["lookup", "wait", "fallback"],
    }
    assert calls == expected[cancel_stage] + ["exit", "feedback"]
    assert ms.selected_stream is None
    assert ms.ep_cache_data is initial_data
