"""Provider availability must not gate access to title recovery actions."""

from types import SimpleNamespace

import pytest

from media_cli import app_core
from media_cli.app import details, playback_menu
from media_cli.app.details_menu_controller import DetailsMenuResult
from media_cli.context import CliFlags, MachineState, UiState


@pytest.fixture
def recovery(monkeypatch):
    show = dict(_id="one", _provider="animexin", name="Example", description="Synopsis", genres=["Drama"])
    ui = UiState(ui_show_ctx=show, ui_ttype_ctx="sub", search_prev_state="HISTORY", action_prev_state="HISTORY")
    ms = MachineState(show_id="one", current_ep="4", total_eps=12, just_picked_anime=True)
    views = []

    def unavailable(target, translation, **kwargs):
        target.update(_episode_catalog_state="unavailable", _episode_catalog_error="Provider is unreachable")
        return []

    def menu(*args, read_view, **kwargs):
        views.append(read_view())
        return DetailsMenuResult("back")

    monkeypatch.setattr(app_core, "ensure_episode_ids", unavailable)
    monkeypatch.setattr(app_core, "refresh_history_entry_provider_catalog", lambda *a: None)
    monkeypatch.setattr(app_core, "patch_history_entry_show", lambda *a: None)
    monkeypatch.setattr(app_core, "prepare_show_display_state", lambda *a, **kw: None)
    monkeypatch.setattr(app_core, "get_local_progress", lambda *a: 3)
    monkeypatch.setattr(app_core, "get_resume_time", lambda *a: 0)
    monkeypatch.setattr(app_core, "is_confirmed_offline", lambda: False)
    monkeypatch.setattr(app_core, "with_loading", lambda msg, fn, *a, **kw: fn(*a, **kw))
    monkeypatch.setattr(playback_menu, "run_details_menu", menu)
    return SimpleNamespace(show=show, ui=ui, ms=ms, views=views, flags=CliFlags(incognito_mode=True),
                           args=SimpleNamespace(sync=False, no_sync=True))


@pytest.mark.parametrize("source", ["HISTORY", "SEARCH", "ANILIST_BROWSE"])
def test_failed_catalog_still_opens_details_and_safe_actions(recovery, source):
    r = recovery
    r.ui.search_prev_state = source
    assert details.handle_details_state(r.flags, r.ui, r.ms, {}, r.args, "sub", lambda *a: False) == source
    view = r.views[-1]
    items = {item.reference: item for item in view.items}
    assert items["PROGRESS"].selectable
    assert items["ANIME_SONGS"].label == "OP/ED Songs"
    assert items["ANIME_SONGS"].hint == "openings & endings for this title"
    assert items["RETRY_CATALOG"].selectable
    assert items["CHANGE_PROVIDER" if source != "ANILIST_BROWSE" else "CHANGE_MATCH"].selectable
    assert not items["PLAY_CURRENT"].selectable
    assert not items["EPISODES"].selectable
    assert not items["MIRRORS"].selectable
    assert not items["DOWNLOAD_MENU"].selectable
    assert "Provider is unreachable" in view.footer.statuses[0].message
    assert r.ms.current_ep == "4"


@pytest.mark.parametrize("action", ["CHANGE_PROVIDER", "RETRY_CATALOG"])
def test_recovery_actions_do_not_require_episode_ids(recovery, monkeypatch, action):
    r = recovery
    calls = []

    def menu(*args, read_view, **kwargs):
        view = read_view()
        assert next(item for item in view.items if item.reference == action).selectable
        return DetailsMenuResult("selected", action)

    monkeypatch.setattr(playback_menu, "run_details_menu", menu)
    monkeypatch.setattr(app_core, "_run_manual_match_search", lambda *a, **kw: calls.append(kw))
    assert details.handle_details_state(r.flags, r.ui, r.ms, {}, r.args, "sub", lambda *a: False) == "DETAILS"
    if action == "CHANGE_PROVIDER":
        assert calls[0]["allow_provider_change"] is True
    else:
        assert "_episode_catalog_error" not in r.show
        assert "_episode_catalog_state" not in r.show


def test_retry_restores_playable_actions_when_provider_recovers(recovery, monkeypatch):
    r = recovery

    def retry(*args, **kwargs):
        return DetailsMenuResult("selected", "RETRY_CATALOG")

    monkeypatch.setattr(playback_menu, "run_details_menu", retry)
    assert details.handle_details_state(r.flags, r.ui, r.ms, {}, r.args, "sub", lambda *a: False) == "DETAILS"

    def recovered(target, translation, **kwargs):
        target.update(_episode_ids=["3", "4"], _episode_ids_ttype=translation, _episode_catalog_state="loaded")
        target.pop("_episode_catalog_error", None)
        return ["3", "4"]

    def menu(*args, read_view, **kwargs):
        view = read_view()
        assert next(item for item in view.items if item.reference == "PLAY_CURRENT").selectable
        assert not any(item.reference == "RETRY_CATALOG" for item in view.items)
        return DetailsMenuResult("back")

    monkeypatch.setattr(app_core, "ensure_episode_ids", recovered)
    monkeypatch.setattr(playback_menu, "run_details_menu", menu)
    assert details.handle_details_state(r.flags, r.ui, r.ms, {}, r.args, "sub", lambda *a: False) == "HISTORY"


def test_existing_catalog_can_still_be_used_after_error(recovery, monkeypatch):
    r = recovery
    r.show.update(_episode_ids=["4"], _episode_ids_ttype="sub", _episode_catalog_state="unavailable")
    calls = []

    def cached(target, translation):
        calls.append(translation)
        return target["_episode_ids"]

    monkeypatch.setattr(app_core, "ensure_episode_ids", cached)
    assert playback_menu.handle_action_menu_state(r.flags, r.ui, r.ms, {}, r.args, "sub", lambda *a: False) == "HISTORY"
    assert calls == ["sub"]
    assert r.show["_episode_ids"] == ["4"]
    assert next(item for item in r.views[-1].items if item.reference == "PLAY_CURRENT").selectable
