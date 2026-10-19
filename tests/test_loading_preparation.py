from dataclasses import replace

import pytest

from media_cli.ui import loading_preparation
from media_cli.ui.actions import ActionAvailability, ActionBindings, ResolvedAction
from media_cli.ui.loading_messages import preparation_feedback_messages
from media_cli.ui.loading_model import LoadingInteraction, LoadingOwner, LoadingPlacement, LoadingTaskSpec
from media_cli.ui.picker_actions import picker_bindings
from media_cli.ui.transition import PreparationCancelled
from media_cli.ui.view_model import ViewAction


class Keys:
    def __init__(self, fd, *, on_key, cancel_keys=()):
        self.on_key = on_key
        self.action = None
        self.has_pending_sequence = False

    def poll(self):
        action, self.action = self.action, None
        return action


@pytest.fixture
def driver(monkeypatch):
    monkeypatch.setattr(loading_preparation, "LoadingInput", Keys)
    now, frames, active = [0.0], [], [True]
    source = [picker_bindings()]
    spec = LoadingTaskSpec(
        "prepare", LoadingOwner("preparation", 1), "Fetching",
        LoadingInteraction.BLOCKING, LoadingPlacement.FOOTER, source[0],
    )
    ui = loading_preparation.LoadingPreparation(
        spec, fd=10, render=frames.append, active=lambda: active[0],
        bindings=lambda: source[0], clock=lambda: now[0],
    )
    return ui, now, frames, active, source


def test_preparation_renders_shared_snapshot_and_retires_on_success(driver, monkeypatch):
    ui, now, frames, _, _ = driver

    def run(prepare, **hooks):
        assert prepare() == 42
        hooks["tick"]()
        hooks["on_wait"]()
        assert frames == []
        now[0] = 0.15
        hooks["on_wait"]()
        assert frames[-1].message == "Newest status"
        assert frames[-1].actions.resolve("ENTER").availability is ActionAvailability.BLOCKED
        return 42

    monkeypatch.setattr(loading_preparation, "run_preparation", run)
    assert ui.run(lambda status: (status("Newest status"), 42)[1]) == 42
    assert ui.lifecycle.snapshots(ui.token.owner) == ()


def test_feedback_restores_latest_status_and_ignores_unbound_keys(driver, monkeypatch):
    ui, now, frames, _, _ = driver

    def run(prepare, **hooks):
        now[0] = 0.2
        ui.keys.on_key("UP")
        hooks["on_wait"]()
        assert frames[-1].message in preparation_feedback_messages("move", ui._snapshot().actions)
        ui.update("Latest task")
        hooks["tick"]()
        now[0] = 1.3
        ui.keys.on_key("x")
        hooks["on_wait"]()
        assert frames[-1].message == "Latest task"

    monkeypatch.setattr(loading_preparation, "run_preparation", run)
    ui.run(lambda status: None)


def test_child_ui_suspends_parent_and_resets_delay_and_feedback(driver, monkeypatch):
    ui, now, frames, _, source = driver

    def run(prepare, **hooks):
        now[0] = 1
        ui.keys.on_key("UP")
        hooks["on_ui_enter"]()
        assert ui.lifecycle.snapshots(ui.token.owner) == ()
        ui.update("Newest parent")
        hooks["tick"]()
        source[0] = picker_bindings(refresh=True)
        now[0] = 20
        hooks["on_ui_return"]()
        hooks["on_wait"]()
        assert frames == []
        now[0] = 20.16
        hooks["on_wait"]()
        assert frames[-1].message == "Newest parent"
        assert frames[-1].actions.resolve("F5").availability is ActionAvailability.BLOCKED

    monkeypatch.setattr(loading_preparation, "run_preparation", run)
    ui.run(lambda status: None)


@pytest.mark.parametrize("action_id,key", [("select", "F6"), ("move", "F7")])
def test_preparation_feedback_follows_action_not_key_and_restores_progress(driver, monkeypatch, action_id, key):
    ui, now, frames, _, source = driver
    source[0] = ActionBindings((ResolvedAction(ViewAction(action_id, "Custom control", (key,))),))

    def run(prepare, **hooks):
        hooks["on_ui_enter"]()
        hooks["on_ui_return"]()
        now[0] = 0.2
        ui.keys.on_key(key)
        hooks["on_wait"]()
        message = frames[-1].message
        assert ("selection" in message.lower()) if action_id == "select" else (
            "navigate" in message.lower() or "navigation" in message.lower()
        )
        assert message != "Hang on, still loading."
        ui.update("Latest progress")
        hooks["tick"]()
        now[0] = 1.3
        hooks["on_wait"]()
        assert frames[-1].message == "Latest progress"
        return 42

    monkeypatch.setattr(loading_preparation, "run_preparation", run)
    assert ui.run(lambda status: None) == 42


def test_preparation_feedback_choices_are_valid_and_action_specific():
    from media_cli.ui.message_choices import normalize_messages

    selection = preparation_feedback_messages("select")
    navigation = preparation_feedback_messages("move")
    assert normalize_messages(selection) == selection
    assert normalize_messages(navigation) == navigation
    assert set(selection).isdisjoint(navigation)
    assert preparation_feedback_messages("unregistered-action") is None


@pytest.mark.parametrize("keys", [("ESC", "LEFT"), ("k",), ()])
@pytest.mark.parametrize("available", [True, False])
def test_navigation_feedback_advertises_only_available_cancel_actions(keys, available):
    availability = ActionAvailability.AVAILABLE if available else ActionAvailability.BLOCKED
    binding = ResolvedAction(ViewAction("back", "Cancel", keys), availability,
                             None if available else "Cannot cancel")
    messages = preparation_feedback_messages("move", ActionBindings((binding,)))
    from media_cli.ui.message_choices import normalize_messages

    assert normalize_messages(messages) == messages
    assert max(map(len, messages)) <= 80
    if available and keys:
        assert any("cancel" in message.lower() for message in messages)
        if keys == ("ESC", "LEFT"):
            assert "Loading. Esc or Left Arrow to cancel." in messages
        else:
            assert "Loading. k to cancel." in messages
            assert all("Esc" not in message and "Left" not in message for message in messages)
    else:
        assert all("cancel" not in message.lower() for message in messages)


def test_driver_passes_current_cancellation_actions_to_feedback(driver, monkeypatch):
    ui, now, frames, _, source = driver
    source[0] = ActionBindings((
        ResolvedAction(ViewAction("move", "Move", ("UP",))),
        ResolvedAction(ViewAction("back", "Cancel", ("k",))),
    ))
    received = []

    def wording(action_id, actions):
        received.append(actions)
        return "Loading. k to cancel."

    monkeypatch.setattr(loading_preparation, "preparation_feedback_messages", wording)

    def run(prepare, **hooks):
        hooks["on_ui_enter"]()
        hooks["on_ui_return"]()
        now[0] = 0.2
        ui.keys.on_key("UP")
        hooks["on_wait"]()
        assert frames[-1].message == "Loading. k to cancel."
        assert received[0].resolve("k").availability is ActionAvailability.AVAILABLE

    monkeypatch.setattr(loading_preparation, "run_preparation", run)
    ui.run(lambda status: None)


def test_navigation_hint_excludes_blocked_back_and_global_quit():
    actions = ActionBindings((
        ResolvedAction(ViewAction("back", "Cancel", ("ESC",))),
        ResolvedAction(ViewAction("back", "Back", ("LEFT",)), ActionAvailability.BLOCKED, "Busy"),
        ResolvedAction(ViewAction("quit", "Quit", ("CTRL_C",))),
    ))
    messages = preparation_feedback_messages("move", actions)
    assert "Loading. Esc to cancel." in messages
    assert all("Left" not in message and "Ctrl" not in message for message in messages)


def test_long_custom_cancel_label_does_not_overflow_message_contract():
    from media_cli.ui.message_choices import normalize_messages

    actions = ActionBindings((ResolvedAction(ViewAction("back", "Cancel", ("K" * 130,))),))
    messages = preparation_feedback_messages("move", actions)
    assert normalize_messages(messages) == messages
    assert all("cancel" in message.lower() for message in messages)


@pytest.mark.parametrize("outcome", ("cancel", "quit", "error"))
def test_preparation_retires_on_cancel_quit_or_error(driver, monkeypatch, outcome):
    ui, _, _, _, _ = driver
    expected = {"cancel": PreparationCancelled, "quit": KeyboardInterrupt, "error": ValueError}[outcome]

    def run(prepare, **hooks):
        if outcome == "error":
            raise ValueError("task failed")
        ui.keys.action = outcome
        hooks["tick"]()
        assert hooks["cancelled"]()
        raise PreparationCancelled

    monkeypatch.setattr(loading_preparation, "run_preparation", run)
    with pytest.raises(expected):
        ui.run(lambda status: None)
    ui.update("Late worker")
    ui.lifecycle.drain()
    assert ui.lifecycle.snapshots(ui.token.owner) == ()


def test_shell_and_partial_input_are_not_overwritten(driver, monkeypatch):
    ui, now, frames, active, _ = driver

    def run(prepare, **hooks):
        now[0] = 1
        active[0] = False
        hooks["on_wait"]()
        assert frames == []
        ui.keys.has_pending_sequence = True
        assert not hooks["ready_for_ui"]()
        ui.keys.has_pending_sequence = False
        assert hooks["ready_for_ui"]()

    monkeypatch.setattr(loading_preparation, "run_preparation", run)
    ui.run(lambda status: None)


def test_cancel_is_not_enabled_when_source_explicitly_blocks_it(driver, monkeypatch):
    from media_cli.ui.actions import ActionBindings, ResolvedAction

    ui, _, _, _, source = driver
    source[0] = ActionBindings(tuple(
        ResolvedAction(b.action, ActionAvailability.BLOCKED, "Cannot cancel")
        if b.action.id == "back" else b for b in picker_bindings().items
    ))

    def run(prepare, **hooks):
        hooks["on_ui_enter"]()
        hooks["on_ui_return"]()
        ui.keys.action = "cancel"
        hooks["tick"]()
        assert not hooks["cancelled"]()

    monkeypatch.setattr(loading_preparation, "run_preparation", run)
    ui.run(lambda status: None)


def test_driver_refuses_unsupported_transition_or_background_contract(driver):
    ui, _, _, _, _ = driver
    for changes in ({"transition": True}, {"interaction": LoadingInteraction.BACKGROUND}):
        with pytest.raises(ValueError):
            loading_preparation.LoadingPreparation(
                replace(ui.spec, **changes), fd=10, render=lambda s: None,
                active=lambda: True, bindings=picker_bindings,
            )


def test_finished_driver_cannot_be_reused(driver, monkeypatch):
    ui, _, _, _, _ = driver
    monkeypatch.setattr(loading_preparation, "run_preparation", lambda work, **hooks: 42)
    assert ui.run(lambda status: 42) == 42
    with pytest.raises(RuntimeError, match="once"):
        ui.run(lambda status: 42)


@pytest.mark.parametrize("action_id", ["back", "quit"])
@pytest.mark.parametrize("blocked", [False, True])
def test_preparation_honors_registered_cancel_and_quit_keys(driver, monkeypatch, action_id, blocked):
    ui, _, _, _, source = driver
    action = ViewAction(action_id, "Cancel" if action_id == "back" else "Quit", ("q",))
    binding = ResolvedAction(action, ActionAvailability.BLOCKED, "Unavailable") if blocked else ResolvedAction(action)
    source[0] = ActionBindings((binding,))

    def run(prepare, **hooks):
        hooks["on_ui_enter"]()
        hooks["on_ui_return"]()
        ui.keys.on_key("q")
        assert hooks["cancelled"]() is (not blocked and action_id == "back")
        if hooks["cancelled"]():
            raise PreparationCancelled

    monkeypatch.setattr(loading_preparation, "run_preparation", run)
    if blocked:
        ui.run(lambda status: None)
    else:
        expected = PreparationCancelled if action_id == "back" else KeyboardInterrupt
        with pytest.raises(expected):
            ui.run(lambda status: None)
    assert ui.lifecycle.snapshots(ui.token.owner) == ()


def test_preparation_cancel_key_is_supplied_by_bindings_not_hardcoded(driver, monkeypatch):
    ui, _, _, _, source = driver
    source[0] = ActionBindings((ResolvedAction(ViewAction("back", "Cancel", ("k",))),))

    def run(prepare, **hooks):
        hooks["on_ui_enter"]()
        hooks["on_ui_return"]()
        ui.keys.on_key("q")
        assert not hooks["cancelled"]()
        ui.keys.on_key("k")
        assert hooks["cancelled"]()
        raise PreparationCancelled

    monkeypatch.setattr(loading_preparation, "run_preparation", run)
    with pytest.raises(PreparationCancelled):
        ui.run(lambda status: None)


def test_decoder_respects_exact_cancel_bindings(monkeypatch):
    from media_cli.ui.transition import LoadingInput

    bindings = ActionBindings((ResolvedAction(ViewAction("back", "Cancel", ("ESC", "q"))),))
    ui = loading_preparation.LoadingPreparation(
        LoadingTaskSpec("work", LoadingOwner("player", 1), "Loading",
                        LoadingInteraction.BLOCKING, LoadingPlacement.PLAYER_STATUS, bindings),
        fd=10, render=lambda s: None, active=lambda: True, bindings=lambda: bindings,
    )
    monkeypatch.setattr(loading_preparation, "run_preparation", lambda *a, **kw: None)
    # Feed the production decoder without reading a real terminal.
    assert isinstance(ui.keys, LoadingInput)
    action = ui.keys.feed(b"\x1b[D")
    monkeypatch.setattr(ui.keys, "poll", lambda: action)
    ui._tick()
    assert not ui._cancelled
    ui.keys.feed(b"q")
    assert ui._cancelled
