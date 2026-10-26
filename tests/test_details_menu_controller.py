from dataclasses import replace
from types import SimpleNamespace

import pytest

from media_cli.app.details_menu_controller import DetailsMenuController, DetailsMenuResult
from media_cli.app.details_menu_view import DetailsMenuOption, build_details_menu_view
from media_cli.context import UiState
from media_cli.ui.transition import PreparationCancelled
from media_cli.ui.view_session import UiEvent


@pytest.fixture(autouse=True)
def metadata(monkeypatch):
    monkeypatch.setattr("media_cli.core.network.is_confirmed_offline", lambda: False)
    monkeypatch.setattr("media_cli.domain.titles.get_cached_title_language", lambda: "romaji")


def make_control(*, switch=lambda: None, info=lambda: None, refresh_enabled=False):
    source = SimpleNamespace(
        options=(DetailsMenuOption("PLAY_CURRENT", "Play"), DetailsMenuOption("PROGRESS", "Progress"))
    )
    ui = UiState(ui_show_ctx={"_id": "one", "name": "Example", "thumbnail": "https://images.test/cover.png"})

    def read():
        return build_details_menu_view(
            ui.ui_show_ctx, source.options, view_id="details:example:one", image_key="example:one", ttype="sub",
            refresh_enabled=refresh_enabled,
        )

    return DetailsMenuController(ui=ui, read_view=read, switch_translation=switch, information=info), source, ui


def send(control, kind, item_id=None, value=None):
    control.session.dispatch(UiEvent(kind, control.session.current.id, control.session.generation, item_id, value))


def publish(control):
    for command in control.updates(force=True):
        control.session.apply(command)


def test_selection_uses_visible_action_id_not_changed_source_order():
    control, source, _ = make_control()
    source.options = tuple(reversed(source.options))
    send(control, "select", "action:PLAY_CURRENT")
    assert control.session.result == DetailsMenuResult("selected", "PLAY_CURRENT")


def test_ready_catalog_exits_menu_without_waiting_for_user_input():
    control, _, ui = make_control()
    ready = [False]
    control = DetailsMenuController(ui=ui, read_view=control._read, switch_translation=lambda: None,
        information=lambda: None, ready=lambda: ready[0])
    assert control.updates(force=True) == ()
    ready[0] = True
    publish(control)
    assert control.session.result == DetailsMenuResult('selected', 'CATALOG_READY')


def test_episode_action_is_focusable_but_can_be_blocked_until_catalog_ready():
    control, _, ui = make_control()
    control = DetailsMenuController(ui=ui, read_view=control._read, switch_translation=lambda: None,
        information=lambda: None, can_activate=lambda action: action != 'PLAY_CURRENT')
    assert control.session.item('action:PLAY_CURRENT').selectable
    send(control, 'select', 'action:PLAY_CURRENT')
    assert not control.session.exited
    send(control, 'select', 'action:PROGRESS')
    assert control.session.result == DetailsMenuResult('selected', 'PROGRESS')


def test_live_relabel_keeps_selection_filter_and_scroll():
    control, source, _ = make_control()
    control.session.position.selected_id = "action:PROGRESS"
    control.session.position.query = "Progress"
    source.options = (replace(source.options[1], hint="local EP 4"), source.options[0])
    publish(control)
    assert control.session.position.selected_id == "action:PROGRESS"
    assert control.session.position.query == "Progress"
    assert control.session.item().hint == "local EP 4"


@pytest.mark.parametrize("kind", ["back", "quit"])
def test_navigation_returns_distinct_outcome(kind):
    control, _, _ = make_control()
    send(control, kind)
    assert control.session.result == DetailsMenuResult(kind)


@pytest.mark.parametrize("cancel", [False, True])
def test_translation_runs_callback_then_refreshes_without_exiting(cancel):
    calls = []

    def switch():
        calls.append(True)
        if cancel:
            raise PreparationCancelled
        ui.ui_show_ctx["name"] = "Updated"

    control, _, ui = make_control(switch=switch)
    control.session.position.query = "Play"
    send(control, "action", "action:PLAY_CURRENT", "translation")
    assert calls == [True]
    assert not control.session.exited
    assert control.session.position.query == "Play"
    assert control.session.current.details.title.parts == (("Example" if cancel else "Updated"),)


def test_information_retains_ready_cover_and_returns_to_same_action():
    seen = []
    control, _, ui = make_control(info=lambda: seen.append(True))
    ui.hovered_show_obj = {
        "_image_key": "example:one",
        "thumbnail": "https://images.test/cover.png",
        "_poster_status": "ready",
        "_poster_raw": "COVER",
    }
    send(control, "action", "action:PROGRESS", "information")
    assert seen == [True]
    assert ui.ui_show_ctx["_poster_raw"] == "COVER"
    assert not control.session.exited


def test_unchanged_live_data_does_not_restart_renderer():
    control, _, _ = make_control()
    assert control.updates(force=True) == ()


def test_refresh_shortcut_returns_domain_action_without_selecting_a_row():
    control, _, _ = make_control(refresh_enabled=True)
    control.session.position.selected_id = "action:PROGRESS"
    control.session.position.query = "Progress"
    send(control, "action", value="refresh")
    assert control.session.result == DetailsMenuResult("selected", "REFRESH_TITLE")
    assert control.session.position.selected_id == "action:PROGRESS"
    assert control.session.position.query == "Progress"


def test_refresh_is_ignored_when_not_declared_by_the_screen():
    control, _, _ = make_control()
    send(control, "action", value="refresh")
    assert not control.session.exited


@pytest.mark.parametrize("kind,value,direction", [
    ("select", None, None), ("action", "increase", 1), ("action", "decrease", -1),
])
def test_inline_activation_refreshes_without_losing_picker_position(kind, value, direction):
    from media_cli.app.playback_settings_view import build_playback_settings_view
    from media_cli.ui.picker_state import PickerState

    ms = SimpleNamespace(auto_delete_buffer=1)
    calls = []

    def activate(action_id, delta):
        calls.append((action_id, delta))
        ms.auto_delete_buffer = 2

    control = DetailsMenuController(
        ui=UiState(), switch_translation=lambda: None, information=lambda: None,
        activate=activate,
        read_view=lambda: build_playback_settings_view(
            {"_id": "one", "name": "Example"}, {}, ms,
            view_id="playback:one", image_key="one", ttype="sub", show_cover=False,
        ),
    )
    control.session.position.selected_id = "action:CYCLE_AUTO_DELETE_BUFFER"
    control.session.position.query = "Buffer"
    state = PickerState(query="Buffer", query_cursor=3, scroll=1)
    frame = control.session.frame(control.session.current.id, control.session.generation)
    frame.picker_state = state
    send(control, kind, "action:CYCLE_AUTO_DELETE_BUFFER", value)
    assert calls == [("CYCLE_AUTO_DELETE_BUFFER", direction)]
    assert not control.session.exited
    assert control.session.position.query == "Buffer"
    assert control.session.position.selected_id == "action:CYCLE_AUTO_DELETE_BUFFER"
    assert control.session.frame(control.session.current.id, control.session.generation).picker_state is state
    assert state.query_cursor == 3
    assert control.session.item().label == "Auto-Delete Buffer: 2 eps"


@pytest.mark.parametrize("kind,action_id", [("selected", None), ("back", "PLAY"), ("invalid", None)])
def test_invalid_outcomes_fail_fast(kind, action_id):
    with pytest.raises((TypeError, ValueError)):
        DetailsMenuResult(kind, action_id)


def test_runner_seeds_initial_selection_before_restoring_position(monkeypatch):
    from media_cli.app import details_menu_controller as module
    from media_cli.context import CliFlags

    control, _, ui = make_control()

    def run(flags, ui, session, **kwargs):
        assert session.position.selected_id == "action:PROGRESS"
        assert kwargs["position_key"] == (session.current.id,)
        return DetailsMenuResult("back")

    monkeypatch.setattr(module, "run_view_session", run)
    assert module.run_details_menu(
        CliFlags(), ui, read_view=lambda: control.session.current,
        switch_translation=lambda: None, information=lambda: None,
        initial_selected_id="action:PROGRESS",
    ) == DetailsMenuResult("back")


def test_runner_rejects_missing_initial_selection(monkeypatch):
    from media_cli.app import details_menu_controller as module
    from media_cli.context import CliFlags

    control, _, ui = make_control()
    with pytest.raises(ValueError, match="selectable item"):
        module.run_details_menu(
            CliFlags(), ui, read_view=lambda: control.session.current,
            switch_translation=lambda: None, information=lambda: None,
            initial_selected_id="missing",
        )
