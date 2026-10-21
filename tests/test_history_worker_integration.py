"""Live History refresh workers publish copies; owner accepts and persists them."""

from copy import deepcopy
from threading import Event, get_ident
from types import SimpleNamespace

import pytest

from media_cli.app import search
from media_cli.app.history_refresh_handoff import HistoryRefreshInbox
from media_cli.context import CliFlags, MachineState, UiState
from media_cli.ui.transition import call_on_ui_thread, run_preparation


@pytest.mark.parametrize("failing_neighbor", [False, True])
@pytest.mark.parametrize("through_preparation", [False, True])
@pytest.mark.parametrize("shared_poll", [False, True])
def test_background_refresh_does_not_touch_rows_until_owner_drain(monkeypatch, failing_neighbor, through_preparation, shared_poll):
    from media_cli.app import history_menu_controller

    owner, done = get_ident(), Event()
    stored = [dict(show=dict(_id="one", _provider="example", name="Example", availableEpisodes={"sub": 10}),
                   translation_type="sub", episode="9", progress=9)]
    if failing_neighbor:
        neighbor = deepcopy(stored[0])
        neighbor["show"]["_id"] = "failed"
        stored.append(neighbor)
    writes, rebuilds, persisted = [], [], []
    controllers = []
    original_controller = history_menu_controller.HistoryMenuController

    class ObservedController(original_controller):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            controllers.append(self)

    monkeypatch.setattr(history_menu_controller, "HistoryMenuController", ObservedController)

    class ObservedInbox(HistoryRefreshInbox):
        def prepare(self, *args):
            try:
                super().prepare(*args)
            finally:
                done.set()

    monkeypatch.setattr(search, "HistoryRefreshInbox", ObservedInbox, raising=False)
    monkeypatch.setattr(search, "history_refresh_status", {})
    monkeypatch.setattr(search.app_core, "load_history", lambda: deepcopy(stored))
    monkeypatch.setattr(search.app_core, "batch_prepare_shows_display_state", lambda *a: rebuilds.append(get_ident()))
    monkeypatch.setattr(search.app_core, "filter_history_entries", lambda entries, mode: list(entries))
    monkeypatch.setattr(search.app_core, "format_history_entry", lambda entry: entry["show"]["name"])
    monkeypatch.setattr(search.app_core, "get_show_anilist_id", lambda show: None)
    monkeypatch.setattr(search.app_core, "refresh_history_anilist_airing_batch", lambda *a, **kw: False)
    monkeypatch.setattr(search.history_domain, "history_entry_should_auto_refresh", lambda *a, **kw: True)
    monkeypatch.setattr(search.history_domain, "history_can_skip_finished_provider_refresh", lambda *a: False)
    monkeypatch.setattr(search.app_core, "_poster_manager", SimpleNamespace())
    monkeypatch.setattr(search.app_core, "_get_poster", lambda show: "")
    monkeypatch.setattr(search.app_core, "make_info_fn", lambda *a: None)
    def persist(entry):
        writes.append(get_ident())
        persisted.append(deepcopy(entry))

    monkeypatch.setattr(search.app_core, "patch_history_entry_state", persist)

    def refresh(entry):
        assert get_ident() != owner
        if entry["show"]["_id"] == "failed":
            entry["show"]["availableEpisodes"]["sub"] = 99
            raise RuntimeError("Provider temporarily unavailable")
        entry["show"]["availableEpisodes"]["sub"] = 11
        entry["show"]["_episode_ids"] = [str(i) for i in range(1, 12)]
        entry["has_new_release"] = True
        return True

    monkeypatch.setattr(search.app_core, "refresh_history_entry_provider_catalog", refresh)

    def picker(*args, **hooks):
        assert get_ident() == owner
        reference = hooks["item_key_fn"](0)
        assert reference.provider_id == "example" and reference.show_id == "one"
        hooks["top_header_fn"](0)
        live = ui.hovered_show_obj
        assert done.wait(2)
        assert live["availableEpisodes"]["sub"] == 10
        assert not writes
        rebuilds.clear()
        stored[0].update(episode="11", progress=11)
        if shared_poll:
            control = controllers[0]
            for command in control.updates(force=True):
                control.session.apply(command)
            assert "11" in control.session.current.items[0].details.metadata.text
        else:
            hooks["tick_fn"]()
        assert live["availableEpisodes"]["sub"] == 11
        assert live["_episode_ids"][-1] == "11"
        assert writes and all(thread == owner for thread in writes + rebuilds)
        accepted = next(entry for entry in persisted if entry["show"]["_id"] == "one")
        assert accepted["episode"] == "11"
        assert accepted["progress"] == 11
        assert accepted["has_new_release"] is False
        if failing_neighbor:
            failed = next(entry for entry in persisted if entry["show"]["_id"] == "failed")
            assert failed["show"]["availableEpisodes"]["sub"] == 10
        assert search.history_refresh_status == {}
        return -2

    monkeypatch.setattr(search, "tui_pick", lambda *a, **kw: call_on_ui_thread(lambda: picker(*a, **kw)))
    ui = UiState()
    def enter_history():
        return search.handle_history_state(CliFlags(), ui, MachineState(), {}, SimpleNamespace(), "sub", None)

    result = run_preparation(enter_history, tick=lambda: None) if through_preparation else enter_history()
    assert result == "QUIT"
