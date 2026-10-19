from copy import deepcopy
from threading import Event, current_thread
from types import SimpleNamespace

import pytest

from media_cli import app_core
from media_cli.app import details, playback_menu
from media_cli.context import CliFlags, MachineState, UiState
from media_cli.ui.transition import PreparationCancelled, in_preparation, run_preparation


@pytest.fixture
def entry(monkeypatch):
    show = {
        "_id": "demo",
        "_provider": "animexin",
        "name": "Demo",
        "availableEpisodes": {"sub": 0, "dub": 0},
    }
    flags = CliFlags()
    ui = UiState(ui_show_ctx=show, ui_ttype_ctx="dub", search_prev_state="SEARCH")
    ms = MachineState(just_picked_anime=True, total_eps=9, current_ep="1")
    args = SimpleNamespace(sync=True, no_sync=False)
    cfg = {"anilist_token": "test-token"}
    history, jobs, observations, ticks, pending = [], [], [], [], []

    def observe(phase, prepared):
        observations.append((phase, in_preparation(), prepared is not show))

    def loading(message, fn, *args, **kwargs):
        if in_preparation():
            return fn(*args, **kwargs)
        jobs.append(message)
        result = run_preparation(lambda: fn(*args, **kwargs), tick=lambda: ticks.append(current_thread()))
        pending.append((deepcopy(show), deepcopy(vars(ui)), deepcopy(vars(ms))))
        return result

    def refresh(history_entry):
        prepared = history_entry["show"]
        observe("refresh", prepared)
        prepared["availableEpisodes"]["sub"] = 2

    def enrich(prepared):
        observe("enrich", prepared)
        prepared.update(description="Prepared", genres=["Drama"])

    def ensure(prepared, ttype):
        observe("episodes-" + ttype, prepared)
        ids = ["1", "2"] if ttype == "sub" else []
        prepared["_episode_ids"] = ids
        return ids

    def match(flags, ui, prepared, token, manual_on_fail):
        observe("match", prepared)
        return {**prepared, "aniListId": "42"}

    def display(prepared, ttype, use_anilist, **kwargs):
        observe("display", prepared)
        prepared["_local_progress"] = 1
        prepared["_sync_enabled"] = use_anilist

    def resolve(context, args, cfg, prepared):
        observe("tracking", prepared)
        return bool(prepared.get("aniListId"))

    monkeypatch.setattr(app_core, "with_loading", loading)
    monkeypatch.setattr(app_core, "refresh_history_entry_provider_catalog", refresh)
    monkeypatch.setattr(app_core, "enrich_show_if_missing", enrich)
    monkeypatch.setattr(app_core, "ensure_episode_ids", ensure)
    monkeypatch.setattr(app_core, "match_provider_show_to_anilist", match)
    monkeypatch.setattr(app_core, "_run_manual_anilist_match", lambda *args: pytest.fail("Unexpected manual match"))
    monkeypatch.setattr(app_core, "get_show_anilist_id", lambda prepared: prepared.get("aniListId"))
    monkeypatch.setattr(
        app_core, "fetch_anilist_media", lambda *args: pytest.fail("Already synced match fetched again")
    )
    monkeypatch.setattr(app_core, "prepare_show_display_state", display)
    monkeypatch.setattr(app_core, "get_resume_time", lambda *args: 0)
    monkeypatch.setattr(
        app_core, "patch_history_entry_show", lambda *args: history.append((current_thread(), deepcopy(args)))
    )
    monkeypatch.setattr(playback_menu, "handle_action_menu_state", lambda *args: "ACTION_MENU")
    return SimpleNamespace(
        show=show,
        flags=flags,
        ui=ui,
        ms=ms,
        args=args,
        cfg=cfg,
        history=history,
        jobs=jobs,
        observations=observations,
        ticks=ticks,
        pending=pending,
        resolve=resolve,
        run=lambda: details.handle_details_state(flags, ui, ms, cfg, args, "dub", resolve),
    )


def test_complete_preparation_stays_under_loading_until_atomic_commit(entry):
    original = (deepcopy(entry.show), deepcopy(vars(entry.ui)), deepcopy(vars(entry.ms)))
    assert entry.run() == "ACTION_MENU"

    assert entry.observations
    assert all(loading and detached for _, loading, detached in entry.observations), entry.observations
    assert len(entry.jobs) == 1
    assert entry.pending == [original]
    assert entry.ticks and all(thread is current_thread() for thread in entry.ticks)
    assert entry.ui.ui_ttype_ctx == "sub"
    assert entry.ui.ui_show_ctx["availableEpisodes"] == {"sub": 2, "dub": 0}
    assert entry.ui.ui_show_ctx["_sync_enabled"] is True
    assert "Switched to SUB" in entry.ui.ui_show_ctx["_action_feedback"]
    assert entry.ms.total_eps == 2
    assert entry.ms.current_ep == "2"
    assert entry.ms.just_picked_anime is False
    assert len(entry.history) == 1
    assert entry.history[0][0] is current_thread()
    assert entry.history[0][1][:2] == ("demo", "sub")


def test_abandoned_controller_cannot_persist_history_after_preparation(entry, monkeypatch):
    started, release, finished = Event(), Event(), Event()
    original_ensure = app_core.ensure_episode_ids

    def ensure(*args, **kwargs):
        started.set()
        assert release.wait(3)
        return original_ensure(*args, **kwargs)

    def controller():
        try:
            return entry.run()
        finally:
            finished.set()

    monkeypatch.setattr(app_core, "ensure_episode_ids", ensure)
    try:
        with pytest.raises(PreparationCancelled):
            run_preparation(controller, tick=lambda: None, cancelled=started.is_set)
        release.set()
        assert finished.wait(1)
        assert entry.history == []
    finally:
        release.set()


def test_cancelled_late_worker_cannot_mutate_show_or_contexts(entry, monkeypatch):
    started, release, finished = Event(), Event(), Event()
    original_show = deepcopy(entry.show)
    original_ui = deepcopy(vars(entry.ui))
    original_ms = deepcopy(vars(entry.ms))

    def ensure(prepared, ttype):
        started.set()
        assert release.wait(3)
        prepared["availableEpisodes"][ttype] = 99
        return ["1", "2"]

    def match(flags, ui, prepared, token, manual_on_fail):
        flags.incognito_mode = True
        ui.ui_ttype_ctx = "stale"
        ui.ui_show_ctx["late"] = True
        return {**prepared, "aniListId": "42"}

    def loading(message, fn, *args, **kwargs):
        if in_preparation():
            return fn(*args, **kwargs)

        def work():
            try:
                return fn(*args, **kwargs)
            finally:
                if started.is_set():
                    finished.set()

        return run_preparation(work, tick=lambda: None, cancelled=started.is_set)

    monkeypatch.setattr(app_core, "ensure_episode_ids", ensure)
    monkeypatch.setattr(app_core, "match_provider_show_to_anilist", match)
    monkeypatch.setattr(app_core, "with_loading", loading)
    try:
        try:
            result = entry.run()
        except PreparationCancelled:
            result = "escaped cancellation"
    finally:
        release.set()
        assert finished.wait(3)

    assert entry.show == original_show
    assert vars(entry.ui) == original_ui
    assert vars(entry.ms) == original_ms
    assert entry.flags.incognito_mode is False
    assert entry.history == []
    assert result == "SEARCH"


@pytest.mark.parametrize("matched_id", ["77", None])
def test_manual_match_runs_on_ui_thread_before_detached_finalization(entry, monkeypatch, matched_id):
    manual_calls = []
    original_show = deepcopy(entry.show)
    original = (original_show, deepcopy(vars(entry.ui)), deepcopy(vars(entry.ms)))

    def automatic(flags, ui, prepared, token, manual_on_fail):
        assert in_preparation()
        assert flags is not entry.flags and ui is not entry.ui
        assert manual_on_fail is False
        return None

    def manual(flags, ui, prepared, token):
        assert not in_preparation()
        assert current_thread() is owner
        assert flags is entry.flags and ui is entry.ui
        assert prepared is not entry.show
        assert entry.show == original_show
        assert ui.ui_ttype_ctx == "dub"
        manual_calls.append(True)
        return {**prepared, "aniListId": matched_id} if matched_id else None

    owner = current_thread()
    monkeypatch.setattr(app_core, "match_provider_show_to_anilist", automatic)
    monkeypatch.setattr(app_core, "_run_manual_anilist_match", manual)
    assert entry.run() == "ACTION_MENU"
    assert manual_calls == [True]
    assert len(entry.jobs) == 2
    assert entry.pending == [original, original]
    assert entry.ui.ui_show_ctx.get("aniListId") == matched_id
    assert bool(entry.ui.ui_show_ctx.get("_anilist_media_synced")) is bool(matched_id)
    assert entry.ui.ui_show_ctx["_sync_enabled"] is bool(matched_id)
    assert all(loading and detached for _, loading, detached in entry.observations)


def test_cancelled_display_after_manual_match_discards_all_preparation(entry, monkeypatch):
    original_show = deepcopy(entry.show)
    original_ui = deepcopy(vars(entry.ui))
    original_ms = deepcopy(vars(entry.ms))
    completed = Event()
    monkeypatch.setattr(app_core, "match_provider_show_to_anilist", lambda *args: None)
    monkeypatch.setattr(
        app_core, "_run_manual_anilist_match", lambda flags, ui, show, token: {**show, "aniListId": "77"}
    )

    def display(prepared, *args, **kwargs):
        prepared["availableEpisodes"]["sub"] = 99
        completed.set()

    def loading(message, fn, *args, **kwargs):
        return run_preparation(lambda: fn(*args, **kwargs), tick=lambda: None, cancelled=completed.is_set)

    monkeypatch.setattr(app_core, "prepare_show_display_state", display)
    monkeypatch.setattr(app_core, "with_loading", loading)
    assert entry.run() == "SEARCH"
    assert entry.show == original_show
    assert vars(entry.ui) == original_ui
    assert vars(entry.ms) == original_ms
    assert entry.history == []


def test_anilist_context_sync_and_display_remain_under_loading(entry, monkeypatch):
    entry.ui.search_prev_state = "ANILIST_BROWSE"
    entry.show.update(aniListId="42", _sync_conflict=True)

    def fetch(token, media_id):
        entry.observations.append(("fetch", in_preparation(), True))
        return {"id": 42}

    def update(prepared, media):
        entry.observations.append(("update", in_preparation(), prepared is not entry.show))
        prepared["_anilist_progress"] = 1

    monkeypatch.setattr(app_core, "fetch_anilist_media", fetch)
    monkeypatch.setattr(app_core, "update_anime_from_anilist_media", update)
    assert entry.run() == "ACTION_MENU"
    assert len(entry.jobs) == 1
    assert all(loading and detached for _, loading, detached in entry.observations)
    assert "_sync_conflict" not in entry.ui.ui_show_ctx
    assert entry.ui.ui_show_ctx["_progress_authority"] == "AL"
    assert entry.ui.ui_show_ctx["_anilist_media_synced"] is True


def test_downloads_fallback_keeps_mode_and_prefers_watched_progress(entry, monkeypatch):
    entry.ms._is_downloads = True
    entry.ui.search_prev_state = "DOWNLOADS"
    entry.cfg.clear()
    entry.show["_downloaded_episodes"] = ["3", "4"]

    def display(prepared, ttype, use_anilist, *, prefer_watched_episodes):
        assert in_preparation()
        assert prefer_watched_episodes is True
        assert ttype == "dub" and use_anilist is False
        prepared["_local_progress"] = 3

    monkeypatch.setattr(app_core, "ensure_episode_ids", lambda *args: [])
    monkeypatch.setattr(app_core, "prepare_show_display_state", display)
    assert entry.run() == "ACTION_MENU"
    assert entry.ui.ui_ttype_ctx == "dub"
    assert entry.ms.total_eps == 2
    assert entry.ms.current_ep == "4"


def test_loading_keeps_pumping_ui_while_display_preparation_is_blocked(entry, monkeypatch):
    started, release = Event(), Event()
    ticks = []
    original = (deepcopy(entry.show), deepcopy(vars(entry.ui)), deepcopy(vars(entry.ms)))

    def display(prepared, *args, **kwargs):
        started.set()
        assert release.wait(3)
        prepared["_local_progress"] = 1

    def tick():
        assert current_thread() is owner
        assert (entry.show, vars(entry.ui), vars(entry.ms)) == original
        if started.is_set():
            ticks.append(True)
            if len(ticks) >= 3:
                release.set()

    def loading(message, fn, *args, **kwargs):
        return run_preparation(lambda: fn(*args, **kwargs), tick=tick)

    owner = current_thread()
    monkeypatch.setattr(app_core, "prepare_show_display_state", display)
    monkeypatch.setattr(app_core, "with_loading", loading)
    try:
        assert entry.run() == "ACTION_MENU"
    finally:
        release.set()
    assert len(ticks) >= 3
    assert entry.ms.current_ep == "2"


def test_failed_preparation_does_not_commit_details_or_history(entry, monkeypatch):
    original = (deepcopy(entry.show), deepcopy(vars(entry.ui)), deepcopy(vars(entry.ms)))

    def display(prepared, *args, **kwargs):
        prepared["availableEpisodes"]["sub"] = 99
        raise ValueError("Display preparation failed")

    monkeypatch.setattr(app_core, "prepare_show_display_state", display)
    with pytest.raises(ValueError, match="Display preparation failed"):
        entry.run()
    assert (entry.show, vars(entry.ui), vars(entry.ms)) == original
    assert entry.history == []


@pytest.mark.parametrize("disabled", ["incognito", "no_sync", "no_token"])
def test_disabled_tracking_never_prompts_for_match(entry, monkeypatch, disabled):
    if disabled == "incognito":
        entry.flags.incognito_mode = True
    elif disabled == "no_sync":
        entry.args.no_sync = True
    else:
        entry.cfg.clear()
    monkeypatch.setattr(app_core, "match_provider_show_to_anilist", lambda *args: pytest.fail("Tracking is disabled"))
    assert entry.run() == "ACTION_MENU"
    assert entry.ui.ui_show_ctx["_sync_enabled"] is False
