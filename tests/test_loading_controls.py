import pytest

from media_cli.ui.loading_controls import LoadingControls
from media_cli.ui.picker_actions import picker_bindings


def test_loading_feedback_uses_available_source_capabilities():
    clock = [10.0]
    controls = LoadingControls(picker_bindings(), clock=lambda: clock[0])
    controls.on_key("CTRL_R")
    assert controls.message("Fetching") == "Fetching"
    controls.on_key("UP")
    assert controls.message("Fetching") == "Hang on, still loading."
    clock[0] = 11.0
    assert controls.message("Preparing newest") == "Preparing newest"


def test_optional_action_is_recognized_only_if_registered_by_source():
    controls = LoadingControls(picker_bindings(reverse=True))
    controls.on_key("CTRL_R")
    assert controls.message("Fetching") == "Hang on, still loading."


def test_cancel_quit_and_unrelated_typing_do_not_show_wait_messages():
    controls = LoadingControls(picker_bindings())
    for key in ("ESC", "LEFT", "CTRL_C", "q", "UNKNOWN"):
        controls.on_key(key)
        assert controls.message("Fetching") == "Fetching"


def test_returning_from_child_ui_resets_feedback_and_bindings():
    controls = LoadingControls(picker_bindings(reverse=True))
    controls.on_key("CTRL_R")
    controls.reset(picker_bindings())
    assert controls.message("Preparing") == "Preparing"
    controls.on_key("CTRL_R")
    assert controls.message("Preparing") == "Preparing"


def test_caller_supplies_feedback_for_only_the_blocked_action():
    clock = [10.0]
    controls = LoadingControls(picker_bindings(), clock=lambda: clock[0])
    assert controls.on_key("ENTER", messages="Still finding results. Nothing to pick just yet.")
    assert controls.message("Searching") == "Still finding results. Nothing to pick just yet."
    # Repeated input remains consumed without extending feedback.
    clock[0] = 10.5
    assert controls.on_key("RIGHT", messages="Another message")
    clock[0] = 11.0
    assert controls.message("Searching latest") == "Searching latest"
    assert not controls.on_key("ESC", messages="Do not show")
    assert not controls.on_key("x", messages="Do not show")


@pytest.mark.parametrize("action_id", ["select", "move"])
def test_result_wait_wording_uses_the_shared_message_contract(action_id):
    from media_cli.ui.loading_messages import result_wait_messages
    from media_cli.ui.message_choices import normalize_messages

    messages = result_wait_messages(action_id)
    assert isinstance(messages, tuple)
    assert normalize_messages(messages) == messages
    assert messages != result_wait_messages("move" if action_id == "select" else "select")


@pytest.mark.parametrize(
    "capability,key",
    (
        ("tabs", "CTRL_N"),
        ("info", "CTRL_O"),
        ("delete", "DELETE"),
        ("reverse", "CTRL_R"),
        ("refresh", "F5"),
        ("multiple", " "),
        ("help_available", "?"),
    ),
)
def test_each_optional_control_is_driven_by_registered_capabilities(capability, key):
    controls = LoadingControls(picker_bindings())
    controls.on_key(key)
    assert controls.message("Fetching") == "Fetching"
    controls.reset(picker_bindings(**{capability: True}))
    controls.on_key(key)
    assert controls.message("Fetching") == "Hang on, still loading."
