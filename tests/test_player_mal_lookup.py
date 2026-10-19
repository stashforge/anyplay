import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace

import pytest

from media_cli.app import playback_player
from media_cli.context import CliFlags, MachineState, UiState
from media_cli.core import anilist
from media_cli.ui import player_screen
from media_cli.ui.transition import PreparationCancelled


@pytest.mark.parametrize("show,expected", [
    ({"malId": "456"}, 456), ({"idMal": 456}, 456),
    ({"anilistMatch": {"idMal": 456}}, 456), ({}, None),
])
def test_known_or_unmatchable_mal_id_does_not_start_loading(monkeypatch, show, expected):
    monkeypatch.setattr(anilist, "fetch_anilist_by_ids", lambda *a, **kw: pytest.fail("Unexpected network lookup"))
    monkeypatch.setattr(player_screen, "run_loading_work", lambda *a, **kw: pytest.fail("Known ID started loading"))
    assert playback_player._load_player_mal_id(show) == expected


def test_core_cached_only_lookup_does_not_fetch_missing_id(monkeypatch):
    show = {"_anilist_id": 123}
    monkeypatch.setattr(anilist, "fetch_anilist_by_ids", lambda *a, **kw: pytest.fail("Cached-only lookup fetched"))
    assert anilist.get_show_mal_id(show, fetch_missing=False) is None
    assert show == {"_anilist_id": 123}


@pytest.mark.parametrize("change", [None, "title", "anilist", "existing_mal", "ui_replaced"])
def test_missing_mal_id_uses_worker_and_only_commits_accepted_current_result(monkeypatch, change):
    owner = threading.current_thread()
    show = {"_id": "one", "_anilist_id": 123}
    ui = SimpleNamespace(ui_show_ctx=show)
    before = deepcopy(show)

    def fetch(token, *, anilist_ids):
        assert threading.current_thread() is not owner
        assert anilist_ids == [123]
        return [{"malId": 456}]

    def pump(work, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(work).result(timeout=1)
        assert show == before
        if change == "title":
            show["_id"] = "two"
        elif change == "anilist":
            show["_anilist_id"] = 789
        elif change == "existing_mal":
            show["malId"] = 999
        elif change == "ui_replaced":
            ui.ui_show_ctx = {"_id": "two"}
        return result

    monkeypatch.setattr(anilist, "fetch_anilist_by_ids", fetch)
    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    if change in {"title", "anilist", "ui_replaced"}:
        with pytest.raises(PreparationCancelled):
            playback_player._load_player_mal_id(show, ui=ui)
        assert "malId" not in show
    else:
        expected = 999 if change else 456
        assert playback_player._load_player_mal_id(show, ui=ui) == expected
        assert show["malId"] == expected


@pytest.mark.parametrize("failure", [PreparationCancelled, KeyboardInterrupt])
def test_cancelled_mal_lookup_cannot_commit_late_results(monkeypatch, failure):
    started, release = threading.Event(), threading.Event()
    show = {"_anilist_id": 123}
    pending = []

    def fetch(*args, **kwargs):
        started.set()
        assert release.wait(2)
        return [{"malId": 456}]

    with ThreadPoolExecutor(max_workers=1) as pool:
        def pump(work, **kwargs):
            pending.append(pool.submit(work))
            assert started.wait(1)
            raise failure

        monkeypatch.setattr(anilist, "fetch_anilist_by_ids", fetch)
        monkeypatch.setattr(player_screen, "run_loading_work", pump)
        try:
            with pytest.raises(failure):
                playback_player._load_player_mal_id(show)
        finally:
            release.set()
        pending[0].result(timeout=1)
    assert show == {"_anilist_id": 123}


@pytest.mark.parametrize("cancel", [False, True])
def test_playback_resolves_mal_once_per_attempt_or_returns_details_on_cancel(monkeypatch, cancel):
    core = playback_player.app_core
    show = {"_id": "one", "_provider": "torrent", "name": "Example"}
    ui = UiState(ui_show_ctx=show)
    ms = MachineState(current_ep="1", selected_stream={"link": "file", "source_name": "Local File"},
                      ep_cache_key=("one", "1", "sub", "torrent"), ep_cache_data={"episode": {"sourceUrls": []}})
    calls = []

    def lookup(*args, **kwargs):
        calls.append("lookup")
        if cancel:
            raise PreparationCancelled
        return None

    monkeypatch.setattr(player_screen, "_player_ui_state", {})
    monkeypatch.setattr(player_screen, "reset_loading_cancel", lambda: None)
    monkeypatch.setattr(player_screen, "loading_cancel_requested", lambda: False)
    monkeypatch.setattr(player_screen, "render", lambda **kw: None)
    monkeypatch.setattr(core, "with_loading", lambda msg, work: work())
    monkeypatch.setattr(core, "prepare_show_display_state", lambda *a: None)
    monkeypatch.setattr(core, "ensure_episode_ids", lambda *a: ["1"])
    monkeypatch.setattr(core, "get_show_anilist_id", lambda *a: None)
    monkeypatch.setattr(core, "get_custom_skips", lambda *a: {})
    monkeypatch.setattr(core, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(core, "_ipc_player", None)
    monkeypatch.setattr(core, "_exit_player_screen", lambda *a, **kw: calls.append("exit"))
    monkeypatch.setattr(core, "set_action_feedback", lambda *a: calls.append("feedback"))
    monkeypatch.setattr(core, "is_termux", lambda: True)
    monkeypatch.setattr(core, "play_android", lambda *a, **kw: calls.append("play") or False)
    monkeypatch.setattr(playback_player, "trigger_background_auto_download", lambda *a, **kw: None)
    monkeypatch.setattr(playback_player, "_load_player_mal_id", lookup)
    args = SimpleNamespace(player="vlc", aniskip=True, download=False, print_url=False, sources=False, binge=False)
    assert playback_player.handle_play_state(
        CliFlags(), ui, ms, {"aniskip_android_hint_dismissed": True}, args, "sub", "best", lambda *a: False,
    ) == "DETAILS"
    assert calls == (["lookup", "exit", "feedback"] if cancel else ["lookup", "play", "exit"])
