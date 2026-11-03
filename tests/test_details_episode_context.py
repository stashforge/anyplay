"""Marking progress must not silently select the next episode."""
from types import SimpleNamespace

import pytest
from test_provider_details_recovery import recovery  # noqa: F401

from media_cli import app_core
from media_cli.app import details, playback_menu
from media_cli.app.details_menu_controller import DetailsMenuResult
from media_cli.core import storage


@pytest.fixture
def episode_context(recovery, monkeypatch):
    r = recovery
    ids = ["1", "2", "3"]
    r.show.update(_episode_ids=ids, _episode_ids_ttype="sub", _episode_catalog_state="loaded",
                  _local_progress=0, availableEpisodes={"sub": 3}, episodeCount=3, status="FINISHED")
    r.ms.current_ep, r.ms.current_ep_index, r.ms.just_picked_anime = "1", 0, False
    monkeypatch.setattr(app_core, "ensure_episode_ids", lambda *a, **kw: ids)
    monkeypatch.setattr(app_core, "get_local_progress", storage.get_local_progress)
    monkeypatch.setattr(storage, "is_incognito", lambda: False)
    monkeypatch.setattr(storage, "load_config", lambda: {"auto_merge_history": False})
    monkeypatch.setattr(storage, "get_title_sync", lambda *a: False)
    monkeypatch.setattr(app_core, "save_resume_time", lambda *a: None)

    def display(show, *a, **kw):
        show["_local_progress"] = storage.get_local_progress(show) or 0
        show["_local_episode_label"] = str(show["_local_progress"])

    monkeypatch.setattr(app_core, "prepare_show_display_state", display)
    selected = ["TRACK_ONLY"]
    views = []

    def menu(*a, read_view, **kw):
        views.append(read_view())
        return DetailsMenuResult("selected", selected[0]) if selected[0] else DetailsMenuResult("back")

    monkeypatch.setattr(playback_menu, "run_details_menu", menu)
    return SimpleNamespace(r=r, selected=selected, views=views)


def run_menu(ctx):
    r = ctx.r
    return playback_menu.handle_action_menu_state(r.flags, r.ui, r.ms, {}, r.args, "sub", lambda *a: False)


def reopen_details(ctx):
    r = ctx.r
    action = ctx.selected[0]
    ctx.selected[0] = None
    try:
        return details.handle_details_state(r.flags, r.ui, r.ms, {}, r.args, "sub", lambda *a: False)
    finally:
        ctx.selected[0] = action


def test_mark_and_unmark_stay_on_same_episode_across_details_reentry(episode_context):
    c = episode_context
    assert run_menu(c) == "DETAILS"
    assert c.r.ms.current_ep == "1"
    assert str(storage.get_local_progress(c.r.show)) == "1"
    reopen_details(c)
    assert c.r.ms.current_ep == "1"
    c.selected[0] = "UNTRACK"
    assert run_menu(c) == "DETAILS"
    items = {item.reference: item for item in c.views[-1].items}
    assert "TRACK_ONLY" not in items
    assert items["UNTRACK"].label == "Mark Unwatched"
    assert "EP 1" in items["UNTRACK"].hint
    assert "EP 2" in items["NEXT"].hint
    assert "PREV" not in items
    assert str(storage.get_local_progress(c.r.show)) == "0"
    assert c.r.ms.current_ep == "1"
    reopen_details(c)
    assert c.r.ms.current_ep == "1"
    c.selected[0] = None
    run_menu(c)
    assert "EP 1" in next(i for i in c.views[-1].items if i.reference == "TRACK_ONLY").hint


def test_returning_to_details_keeps_explicit_selection_but_fresh_open_uses_progress(episode_context):
    c = episode_context
    storage.write_history_progress(c.r.show, 2, "sub")
    reopen_details(c)
    assert c.r.ms.current_ep == "1"
    c.r.ms.just_picked_anime = True
    reopen_details(c)
    assert c.r.ms.current_ep == "3"


@pytest.mark.parametrize("episode", ["2", "3"])
def test_middle_and_final_episode_marking_keeps_target_and_clear_rollback_hint(episode_context, episode):
    c = episode_context
    c.r.ms.current_ep = episode
    c.r.ms.current_ep_index = int(episode) - 1
    assert run_menu(c) == "DETAILS"
    reopen_details(c)
    assert c.r.ms.current_ep == episode
    c.selected[0] = "UNTRACK"
    assert run_menu(c) == "DETAILS"
    items = {item.reference: item for item in c.views[-1].items}
    assert items["UNTRACK"].label == "Mark Unwatched"
    assert f"EP {episode}" in items["UNTRACK"].hint
    assert f"progress to EP {int(episode) - 1}" in items["UNTRACK"].hint
    if episode == "2":
        assert "EP 1" in items["PREV"].hint
        assert "EP 3" in items["NEXT"].hint
    assert str(storage.get_local_progress(c.r.show)) == str(int(episode) - 1)
    reopen_details(c)
    assert c.r.ms.current_ep == episode


def test_progress_picker_anchors_details_to_the_chosen_watched_episode(episode_context, monkeypatch):
    from media_cli.app import details_modals

    c = episode_context
    c.r.ms.current_ep, c.r.ms.current_ep_index = "3", 2
    monkeypatch.setattr(details_modals, "tui_pick", lambda flags, ui, title, opts, **kw:
                        next(i for i, label in enumerate(opts) if "Episode 2" in label))
    assert details_modals.handle_update_progress_state(
        c.r.flags, c.r.ui, c.r.ms, {}, c.r.args, "sub", lambda *a: False
    ) == "DETAILS"
    assert c.r.ms.current_ep == "2" and c.r.ms.current_ep_index == 1
    reopen_details(c)
    items = {item.reference: item for item in c.views[-1].items}
    assert c.r.ms.current_ep == "2"
    assert "EP 3" in items["NEXT"].hint
    assert "EP 2" in items["REPLAY"].hint
    assert "EP 1" in items["PREV"].hint
    assert "EP 2" in items["UNTRACK"].hint
    assert "TRACK_ONLY" not in items


@pytest.mark.parametrize("save_succeeds", [False, True])
def test_cancel_or_reset_progress_has_explicit_episode_context(episode_context, monkeypatch, save_succeeds):
    from media_cli.app import details_modals

    c = episode_context
    c.r.ms.current_ep, c.r.ms.current_ep_index = "2", 1
    monkeypatch.setattr(details_modals, "tui_pick", lambda flags, ui, title, opts, **kw:
                        next(i for i, label in enumerate(opts) if "0 / Unwatched" in label)
                        if save_succeeds else -1)
    details_modals.handle_update_progress_state(
        c.r.flags, c.r.ui, c.r.ms, {}, c.r.args, "sub", lambda *a: False
    )
    assert c.r.ms.current_ep == ("1" if save_succeeds else "2")


@pytest.mark.parametrize("saved", [False, True])
def test_synced_progress_changes_context_only_after_success(episode_context, monkeypatch, saved):
    from media_cli.app import details_modals

    c = episode_context
    c.r.flags.incognito_mode = False
    c.r.ms.current_ep, c.r.ms.current_ep_index = "3", 2
    monkeypatch.setattr(app_core, "get_show_anilist_id", lambda *a: "42")
    monkeypatch.setattr(app_core, "update_anilist_entry", lambda *a, **kw: saved)
    monkeypatch.setattr(app_core, "err", lambda *a: None)
    monkeypatch.setattr(details_modals, "tui_pick", lambda flags, ui, title, opts, **kw:
                        next(i for i, label in enumerate(opts) if "Episode 2" in label))
    details_modals.handle_update_progress_state(
        c.r.flags, c.r.ui, c.r.ms, {"anilist_token": "test-only"}, c.r.args, "sub", lambda *a: True
    )
    assert c.r.ms.current_ep == ("2" if saved else "3")
    assert c.r.ms.current_ep_index == (1 if saved else 2)
