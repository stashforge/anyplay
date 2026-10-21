"""Live skip settings use shared menu data and existing application actions."""

import pytest
from test_provider_details_recovery import recovery  # noqa: F401

from media_cli import app_core
from media_cli.app import playback_menu
from media_cli.app.details_menu_controller import DetailsMenuResult


@pytest.mark.parametrize("action", [
    "TOGGLE_ANISKIP", "TOGGLE_AUTO_SKIP", "TOGGLE_CUSTOM_SKIPS", "CLEAR_SESSION_SKIPS", "CLEAR_SAVED_SKIPS", "EDIT_CUSTOM_SKIPS",
])
def test_skip_actions_run_through_shared_menu(request, monkeypatch, action):
    r = request.getfixturevalue("recovery")
    r.args.aniskip = r.args.auto_skip = True
    r.ms.custom_skip_sessions = {"one": [{"intervals": []}]}
    cleared, screens = [], []
    monkeypatch.setattr("media_cli.media.custom_skips.stable_title_keys", lambda *a: ["one"])
    monkeypatch.setattr(app_core, "get_custom_skips", lambda *a: [])
    monkeypatch.setattr(app_core, "clear_custom_skips", lambda *a: cleared.append(True))
    monkeypatch.setattr("media_cli.media.custom_skips.edit_skip_form", lambda **kw: {
        "mode": "session", "providers": ["all"], "exclude_providers": [],
        "intervals": [{"type": "op", "label": "Opening", "start": 0.0, "end": 90.0}],
    })
    monkeypatch.setattr(playback_menu, "tui_pick", lambda *a, **kw: pytest.fail("Legacy skip picker used"))

    def menu(*args, read_view, activate=None, **kwargs):
        view = read_view()
        screens.append(view.prompt)
        if view.prompt == "Select action":
            return DetailsMenuResult("selected", "PLAYBACK_SETTINGS")
        if view.prompt == "Playback":
            activate("SKIP_SETTINGS", None)
        else:
            assert view.prompt == "Skip Settings"
            assert activate is not None
            assert all(item.details is not None for item in view.items)
            activate(action, None)
            refreshed = read_view()
            if action == "TOGGLE_ANISKIP":
                assert refreshed.items[0].label == "AniSkip: OFF"
            elif action == "TOGGLE_AUTO_SKIP":
                assert refreshed.items[1].label == "Auto-Skip: OFF"
            elif action == "TOGGLE_CUSTOM_SKIPS":
                assert refreshed.items[2].label == "Custom Skips: OFF"
        return DetailsMenuResult("back")

    monkeypatch.setattr(playback_menu, "run_details_menu", menu)
    assert playback_menu.handle_action_menu_state(r.flags, r.ui, r.ms, {}, r.args, "sub", lambda *a: False) == "DETAILS"
    assert screens == ["Select action", "Playback", "Skip Settings"]
    if action == "CLEAR_SESSION_SKIPS":
        assert r.ms.custom_skip_sessions == {}
    if action == "CLEAR_SAVED_SKIPS":
        assert cleared == [True]
    if action == "EDIT_CUSTOM_SKIPS":
        assert r.ms.custom_skip_sessions["one"][0]["intervals"][0]["end"] == 90.0


@pytest.mark.parametrize("has_custom", [False, True])
def test_custom_skip_control_rebuilds_from_real_data_without_fake_on_state(request, monkeypatch, has_custom):
    from media_cli.app import skip_settings_view
    from media_cli.ui.setting_control import toggle_setting

    r = request.getfixturevalue("recovery")
    r.ms.custom_skip_sessions = {"one": [{"intervals": []}]} if has_custom else {}
    r.ms.custom_skip_disabled_titles = {"one"} if has_custom else set()
    feedback = []

    def control(*args, **kwargs):
        return toggle_setting(*args, **{**kwargs, "on_label": "Enabled"})

    monkeypatch.setattr(skip_settings_view, "toggle_setting", control)
    monkeypatch.setattr("media_cli.media.custom_skips.stable_title_keys", lambda *a: ["one"])
    monkeypatch.setattr(app_core, "get_custom_skips", lambda *a: [])
    monkeypatch.setattr(app_core, "set_action_feedback", lambda show, text: feedback.append(text))

    def menu(*args, read_view, activate=None, **kwargs):
        view = read_view()
        if view.prompt == "Select action":
            return DetailsMenuResult("selected", "PLAYBACK_SETTINGS")
        if view.prompt == "Playback":
            activate("SKIP_SETTINGS", None)
        else:
            assert view.items[2].label == "Custom Skips: OFF"
            activate("TOGGLE_CUSTOM_SKIPS", -1)
            assert read_view().items[2].label == f"Custom Skips: {'Enabled' if has_custom else 'OFF'}"
            if has_custom:
                activate("TOGGLE_CUSTOM_SKIPS", -1)
                assert read_view().items[2].label == "Custom Skips: OFF"
        return DetailsMenuResult("back")

    monkeypatch.setattr(playback_menu, "run_details_menu", menu)
    assert playback_menu.handle_action_menu_state(r.flags, r.ui, r.ms, {}, r.args,
                                                  "sub", lambda *a: False) == "DETAILS"
    assert r.ms.custom_skip_disabled_titles == ({"one"} if has_custom else set())
    if not has_custom:
        assert feedback[-1] == "No custom skips yet. Use Edit Custom Skips."
