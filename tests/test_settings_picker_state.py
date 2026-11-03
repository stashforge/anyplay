from types import SimpleNamespace

import pytest

from media_cli.context import CliFlags, MachineState, UiState


@pytest.fixture(autouse=True)
def select_playback_settings(monkeypatch):
    from media_cli.app import playback_menu
    from media_cli.app.details_menu_controller import DetailsMenuController, DetailsMenuResult
    from media_cli.ui.view_picker import run_view_session

    def run_menu(flags, ui, *, read_view, switch_translation, information, activate=None, ready=None, can_activate=None):
        view = read_view()
        if view.prompt in {"Playback", "Skip Settings"}:
            controller = DetailsMenuController(
                ui=ui, read_view=read_view, switch_translation=switch_translation,
                information=information, activate=activate, ready=ready, can_activate=can_activate,
            )
            return run_view_session(
                flags, ui, controller.session, picker=playback_menu.tui_pick, updates_fn=controller.updates,
            )
        item = next(item for item in view.items if item.reference == "PLAYBACK_SETTINGS")
        return DetailsMenuResult("selected", item.reference)

    monkeypatch.setattr(playback_menu, "run_details_menu", run_menu)


@pytest.mark.parametrize("screen", ("Playback", "Skip Settings"))
def test_setting_refresh_reuses_the_same_ui_state(monkeypatch, screen):
    from media_cli import app_core
    from media_cli.app import playback_menu

    show = {"_id": "one", "name": "One", "_episode_ids": ["1"], "availableEpisodes": {"sub": 1}}
    ui = UiState(ui_show_ctx=show, search_prev_state="SEARCH", action_prev_state="SEARCH")
    ms = MachineState(current_ep="1")
    states = []
    playback_entries = 0

    def pick(flags, ui, title, options, **kwargs):
        nonlocal playback_entries
        if title == "Select action":
            return options.index("Playback")
        if title == "Playback" and screen == "Skip Settings":
            playback_entries += 1
            return options.index("Skip Settings") if playback_entries == 1 else -2
        assert title == screen
        state = kwargs["state"]
        if not states:
            states.append(state)
            state.query, state.query_cursor, state.scroll = "auto", 2, 1
            return 0
        assert state is states[0]
        assert (state.query, state.query_cursor, state.scroll) == ("auto", 2, 1)
        states.append(state)
        return -2

    monkeypatch.setattr(playback_menu, "tui_pick", pick)
    monkeypatch.setattr(app_core, "ensure_episode_ids", lambda *args, **kwargs: ["1"])
    monkeypatch.setattr(app_core, "get_resume_time", lambda *args: 0)
    result = playback_menu.handle_action_menu_state(
        CliFlags(), ui, ms, {}, SimpleNamespace(), "sub", lambda *args: False
    )
    assert result == "DETAILS"
    assert len(states) == 2


@pytest.mark.parametrize("apply_change", [True, False])
def test_live_playback_uses_control_labels_and_actual_callback_state(monkeypatch, apply_change):
    from media_cli import app_core
    from media_cli.app import playback_menu, playback_player, playback_settings_view
    from media_cli.ui.setting_control import toggle_setting

    show = {"_id": "one", "name": "One", "_episode_ids": ["1"], "availableEpisodes": {"sub": 1}}
    ui = UiState(ui_show_ctx=show, search_prev_state="SEARCH", action_prev_state="SEARCH")
    ms = MachineState(current_ep="1", auto_download_next="off")
    requested = []

    def control(*args, **kwargs):
        return toggle_setting(*args, **{**kwargs, "on_label": "Enabled"})

    def changed(state, value):
        requested.append(value)
        if apply_change:
            state.auto_download_next = "adaptive" if value else "off"

    def pick(flags, ui, title, options, **kwargs):
        assert title == "Playback"
        if not requested:
            assert options[0] == "Auto-Download Next: Off"
            return 0
        assert options[0] == f"Auto-Download Next: {'Enabled' if apply_change else 'Off'}"
        return -2

    monkeypatch.setattr(playback_settings_view, "toggle_setting", control)
    monkeypatch.setattr(playback_player, "set_auto_download_enabled", changed)
    monkeypatch.setattr(playback_menu, "tui_pick", pick)
    monkeypatch.setattr(app_core, "ensure_episode_ids", lambda *args, **kwargs: ["1"])
    monkeypatch.setattr(app_core, "get_resume_time", lambda *args: 0)
    assert playback_menu.handle_action_menu_state(CliFlags(), ui, ms, {}, SimpleNamespace(),
                                                  "sub", lambda *args: False) == "DETAILS"
    assert requested == [True]


@pytest.mark.parametrize(
    "screen,index,field,initial,direction,expected",
    (
        ("Playback", 0, "auto_download_next", "off", 1, "adaptive"),
        ("Playback", 0, "auto_download_next", "adaptive", 1, "off"),
        ("Playback", 0, "auto_download_next", "adaptive", -1, "off"),
        ("Playback", 0, "auto_download_next", "off", -1, "adaptive"),
        ("Playback", 1, "auto_delete_watched", True, 1, False),
        ("Playback", 1, "auto_delete_watched", True, -1, False),
        ("Playback", 1, "auto_delete_watched", False, -1, True),
        ("Playback", 2, "auto_delete_buffer", 1, -1, 0),
        ("Playback", 2, "auto_delete_buffer", 3, 1, 5),
        ("Playback", 2, "auto_delete_buffer", 5, 1, 5),
        ("Playback", 2, "auto_delete_buffer", 0, -1, 0),
        ("Skip Settings", 0, "aniskip", True, 1, False),
        ("Skip Settings", 0, "aniskip", True, -1, False),
        ("Skip Settings", 0, "aniskip", False, -1, True),
        ("Skip Settings", 1, "auto_skip", False, 1, True),
        ("Skip Settings", 1, "auto_skip", False, -1, True),
        ("Playback", 2, "auto_delete_buffer", 5, None, 0),
        ("Playback", 1, "auto_delete_watched", True, None, False),
        ("Skip Settings", 2, "custom_skip_disabled_titles", {"one"}, 1, set()),
        ("Skip Settings", 2, "custom_skip_disabled_titles", {"one"}, -1, set()),
        ("Skip Settings", 2, "custom_skip_disabled_titles", set(), 1, {"one"}),
    ),
)
def test_settings_cycle_migrated_choices_and_keep_other_directional_rules(
    monkeypatch,
    screen,
    index,
    field,
    initial,
    direction,
    expected,
):
    from media_cli import app_core
    from media_cli.app import playback_menu

    show = {"_id": "one", "name": "One", "_episode_ids": ["1"], "availableEpisodes": {"sub": 1}}
    ui = UiState(ui_show_ctx=show, search_prev_state="SEARCH", action_prev_state="SEARCH")
    ms, args = MachineState(current_ep="1"), SimpleNamespace()
    target = ms if screen == "Playback" or field == "custom_skip_disabled_titles" else args
    setattr(target, field, initial)
    ms.custom_skip_sessions = {"one": [{}]}
    monkeypatch.setattr("media_cli.media.custom_skips.stable_title_keys", lambda *args: ["one"])
    changed = False
    playback_entries = 0

    def pick(flags, ui, title, options, **kwargs):
        nonlocal changed, playback_entries
        if title == "Select action":
            return options.index("Playback")
        if title == "Playback" and screen == "Skip Settings":
            playback_entries += 1
            return options.index("Skip Settings") if playback_entries == 1 else -2
        assert title == screen
        if changed:
            assert getattr(target, field) == expected
            return -2
        changed = True
        if direction is None:
            return index
        assert kwargs["adjust_fn"](index, direction)
        return -6

    monkeypatch.setattr(playback_menu, "tui_pick", pick)
    monkeypatch.setattr(app_core, "ensure_episode_ids", lambda *args, **kwargs: ["1"])
    monkeypatch.setattr(app_core, "get_resume_time", lambda *args: 0)
    assert playback_menu.handle_action_menu_state(CliFlags(), ui, ms, {}, args, "sub", lambda *args: False) == "DETAILS"
    assert changed
