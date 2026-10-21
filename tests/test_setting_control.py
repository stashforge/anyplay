"""Controlled settings request changes; caller-owned state controls rendering."""

import pytest

from media_cli.ui.setting_control import ChoiceSetting, toggle_setting
from media_cli.ui.view_session import UiEvent


def event(kind="select", identity="auto-next", value=None):
    return UiEvent(kind, "settings", 1, identity, value)


def test_toggle_renders_supplied_value_and_rebuilds_only_from_owner_state():
    state = {"enabled": False}
    changes = []

    def changed(value):
        changes.append(value)
        state["enabled"] = value

    def build():
        return toggle_setting("auto-next", "Auto-Play Next", state["enabled"], changed,
                              hint="play the next song when this one ends")

    control = build()
    row = control.item()
    assert (row.id, row.label, row.hint, row.adjustable) == (
        "auto-next", "Auto-Play Next: Off", "play the next song when this one ends", True)
    assert control.handle_event(event())
    assert changes == [True]
    assert control.item().label == "Auto-Play Next: Off"
    assert build().item().label == "Auto-Play Next: On"
    assert build().handle_event(event("action", value="decrease"))
    assert changes == [True, False]


@pytest.mark.parametrize("kind,value", [("select", None), ("action", "increase"), ("action", "decrease")])
def test_toggle_events_share_cycling_in_both_directions(kind, value):
    changes = []
    for current, expected in ((False, True), (True, False)):
        control = toggle_setting("auto-next", "Next", current, changes.append)
        assert control.handle_event(event(kind, value=value))
        assert changes[-1] is expected


def test_choices_use_declared_labels_and_backward_wraparound():
    changes = []
    control = ChoiceSetting("auto-next", "Repeat", "off",
                            (("off", "Off"), ("once", "Once"), ("song", "Song")), changes.append)
    assert control.item().label == "Repeat: Off"
    assert control.handle_event(event("action", value="decrease"))
    assert changes == ["song"]
    assert toggle_setting("auto-next", "Download", True, changes.append,
                          on_label="Adaptive").item().label == "Download: Adaptive"


def test_semantic_direction_can_be_applied_without_constructing_a_ui_event():
    changes = []
    control = toggle_setting("next", "Next", False, changes.append)
    assert control.change(-1)
    assert changes == [True]
    assert control.item().label == "Next: Off"


@pytest.mark.parametrize("incoming", [event(identity="other"), event("back"), event("quit"),
                                      event("query", value="filter"), event("action", value="refresh")])
def test_unrelated_events_do_not_call_owner(incoming):
    changes = []
    control = toggle_setting("auto-next", "Next", False, changes.append)
    assert not control.handle_event(incoming)
    assert changes == []


def test_disabled_and_single_choice_controls_do_not_request_changes():
    changes = []
    disabled = toggle_setting("auto-next", "Next", False, changes.append, enabled=False)
    assert not disabled.item().selectable
    assert not disabled.item().adjustable
    assert not disabled.handle_event(event())
    only = ChoiceSetting("auto-next", "Only", "one", (("one", "One"),), changes.append)
    assert not only.handle_event(event())
    assert changes == []


def test_failed_or_declined_change_never_changes_visual_value():
    def fail(value):
        raise RuntimeError("change failed")
    control = toggle_setting("auto-next", "Next", False, fail)
    with pytest.raises(RuntimeError, match="change failed"):
        control.handle_event(event())
    assert control.item().label == "Next: Off"
    declined = toggle_setting("auto-next", "Next", False, lambda value: False)
    assert declined.handle_event(event())
    assert declined.item().label == "Next: Off"


@pytest.mark.parametrize("choices,value", [((), "off"), ((("on", "On"),), "off"),
                                            ((("off", "Off"), ("off", "Again")), "off")])
def test_invalid_choices_are_rejected(choices, value):
    with pytest.raises(ValueError):
        ChoiceSetting("auto-next", "Next", value, choices, lambda value: None)


def test_toggle_requires_boolean_state_and_callback():
    with pytest.raises(TypeError):
        toggle_setting("auto-next", "Next", "off", lambda value: None)
    with pytest.raises(TypeError):
        toggle_setting("auto-next", "Next", False, None)


def test_choices_snapshot_and_owner_session_validation():
    from media_cli.ui.view_model import Blank, DetailLines, ViewAction, ViewSpec
    from media_cli.ui.view_session import ReplaceView, ViewSession

    state = {"enabled": False}
    choices = [(False, "Off"), (True, "On")]
    changes = []

    def change(value):
        changes.append(value)
        state["enabled"] = value

    def control():
        return ChoiceSetting("auto-next", "Next", state["enabled"], choices, change)

    snapshot = control()
    choices[0] = (False, "Disabled")
    assert snapshot.item().label == "Next: Off"

    def view():
        return ViewSpec("settings", "Settings", (control().item(),),
                        details=DetailLines(Blank, Blank, Blank),
                        actions=(ViewAction("increase", "Next", footer=False),
                                 ViewAction("decrease", "Previous", footer=False)))

    def handle(incoming):
        if control().handle_event(incoming):
            return ReplaceView("settings", session.generation, view())
        return None

    session = ViewSession(view(), handle)
    session.position.query = "Next"
    assert not session.dispatch(UiEvent("select", "wrong-screen", session.generation, "auto-next"))
    assert changes == []
    session.dispatch(UiEvent("select", "settings", session.generation, "auto-next"))
    assert changes == [True]
    assert session.current.items[0].label == "Next: On"
    assert session.position.query == "Next"
