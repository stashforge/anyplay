"""Manual title refresh uses detached work and commits only accepted results."""

from copy import deepcopy
from threading import Event

import pytest
from test_provider_details_recovery import recovery  # noqa: F401

from media_cli import app_core
from media_cli.app import details_preparation, playback_menu
from media_cli.app.details_menu_controller import DetailsMenuResult
from media_cli.ui.transition import PreparationCancelled, run_preparation


@pytest.fixture
def refresh_menu(request, monkeypatch):
    r = request.getfixturevalue("recovery")
    r.show.update(_episode_ids=["3", "4"], _episode_ids_ttype="sub",
                  _episode_catalog_state="loaded", _provider_catalog_checked_at=9999999999)
    monkeypatch.setattr(app_core, "ensure_episode_ids", lambda show, ttype: show["_episode_ids"])
    monkeypatch.setattr(playback_menu, "run_details_menu", lambda *a, **kw: DetailsMenuResult("selected", "REFRESH_TITLE"))
    r.saved = []
    monkeypatch.setattr(app_core, "patch_history_entry_state", lambda entry: r.saved.append(deepcopy(entry)))
    r.run = lambda: playback_menu.handle_action_menu_state(r.flags, r.ui, r.ms, {}, r.args, "sub", lambda *a: False)
    return r


def test_refresh_is_offered_for_linked_streaming_titles(refresh_menu, monkeypatch):
    r = refresh_menu

    def menu(*args, read_view, **kwargs):
        view = read_view()
        assert all(item.reference != "REFRESH_TITLE" for item in view.items)
        action = next(action for action in view.actions if action.id == "refresh")
        assert action.keys == ("CTRL_R",) and not action.footer
        assert dict(view.help)["Ctrl+R"] == "Refresh title"
        return DetailsMenuResult("back")

    monkeypatch.setattr(playback_menu, "run_details_menu", menu)
    assert r.run() == "HISTORY"


@pytest.mark.parametrize("local", [True, False])
def test_refresh_not_offered_for_downloads_or_unlinked_titles(refresh_menu, monkeypatch, local):
    r = refresh_menu
    if local:
        r.ms._is_downloads = True
    else:
        r.show["_has_provider_link"] = False

    def menu(*args, read_view, **kwargs):
        view = read_view()
        assert all(item.reference != "REFRESH_TITLE" for item in view.items)
        assert all(action.id != "refresh" for action in view.actions)
        return DetailsMenuResult("back")

    monkeypatch.setattr(playback_menu, "run_details_menu", menu)
    r.run()


def test_manual_refresh_bypasses_cooldown_and_preserves_progress(refresh_menu, monkeypatch):
    r = refresh_menu
    r.flags.incognito_mode = False
    calls = []

    def refresh(entry, *, enrich):
        assert enrich is False
        assert entry["show"] is not r.show
        assert r.show["_episode_ids"] == ["3", "4"]
        calls.append(True)
        entry["show"].update(_episode_ids=["3", "4", "5"], availableEpisodes={"sub": 5})
        return True

    monkeypatch.setattr(app_core, "refresh_history_entry_provider_catalog", refresh)
    assert r.run() == "DETAILS"
    assert calls == [True]
    assert r.show["_episode_ids"] == ["3", "4", "5"]
    assert r.ms.current_ep == "4"
    assert len(r.saved) == 1 and "episode" not in r.saved[0]
    assert r.saved[0]["translation_type"] == "sub"


def test_cancelled_refresh_does_not_commit_or_persist(refresh_menu, monkeypatch):
    r = refresh_menu
    original = deepcopy(r.show)

    def refresh(entry, **kwargs):
        entry["show"]["_episode_ids"].append("99")

    def cancel(message, fn, *args, **kwargs):
        fn(*args, **kwargs)
        raise PreparationCancelled

    monkeypatch.setattr(app_core, "refresh_history_entry_provider_catalog", refresh)
    monkeypatch.setattr(app_core, "with_loading", cancel)
    assert r.run() == "DETAILS"
    assert r.show == original and r.saved == []


def test_failed_refresh_keeps_episodes_and_reports_failure(refresh_menu, monkeypatch):
    r = refresh_menu

    def failed(entry, **kwargs):
        entry["show"].update(_provider_catalog_failed_at=123,
                             _provider_catalog_refresh_error="Provider unreachable")
        return True

    monkeypatch.setattr(app_core, "refresh_history_entry_provider_catalog", failed)
    assert r.run() == "DETAILS"
    assert r.show["_episode_ids"] == ["3", "4"]
    assert "Provider unreachable" in r.show["_action_feedback"]
    assert r.saved == []  # Incognito refresh stays in the session.


def test_refresh_preparation_preserves_concurrent_updates(monkeypatch):
    show = dict(_id="one", _provider="example", _episode_ids=["1"])
    monkeypatch.setattr(app_core, "refresh_history_entry_provider_catalog",
                        lambda entry, **kw: entry["show"].update(_episode_ids=["1", "2"]))
    prepared = details_preparation.prepare_title_refresh(show, "sub")
    show["thumbnail"] = "new-cover"
    details_preparation.commit_details_transition(show, prepared)
    assert show["thumbnail"] == "new-cover" and show["_episode_ids"] == ["1", "2"]


def test_late_cancelled_worker_cannot_change_title_or_history(refresh_menu, monkeypatch):
    r = refresh_menu
    r.flags.incognito_mode = False
    original = deepcopy(r.show)
    started, release, finished = Event(), Event(), Event()

    def refresh(entry, **kwargs):
        started.set()
        assert release.wait(2)
        entry["show"]["_episode_ids"].append("99")
        finished.set()

    def loading(message, fn, *args, **kwargs):
        return run_preparation(lambda: fn(*args, **kwargs), tick=lambda: None,
                               cancelled=started.is_set)

    monkeypatch.setattr(app_core, "refresh_history_entry_provider_catalog", refresh)
    monkeypatch.setattr(app_core, "with_loading", loading)
    try:
        assert r.run() == "DETAILS"
        release.set()
        assert finished.wait(1)
        assert r.show == original and r.saved == []
    finally:
        release.set()


def test_manual_provider_check_can_skip_persisting_enrichment(monkeypatch):
    from types import SimpleNamespace

    from media_cli.domain.history import refresh_history_entry_provider_catalog

    show = dict(_id="one", _provider="example", _episode_catalog_error="Old failure")
    monkeypatch.setattr("media_cli.services.catalog._provider_for_title", lambda *a: SimpleNamespace(
        episode_catalog=lambda *a, **kw: {"state": "loaded", "ids": ["1"], "labels": {}},
    ))
    monkeypatch.setattr("media_cli.core.enrichment.enrich_show_if_missing",
                        lambda *a: pytest.fail("Cancelled work must not persist enrichment"))
    assert refresh_history_entry_provider_catalog({"show": show}, enrich=False)
    assert show["_episode_ids"] == ["1"]
    assert show["_episode_catalog_error"] == ""
