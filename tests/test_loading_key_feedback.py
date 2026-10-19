import pytest

from media_cli.ui.actions import ActionAvailability, ResolvedAction
from media_cli.ui.loading_key_feedback import LoadingKeyFeedback
from media_cli.ui.message_choices import MessagePicker
from media_cli.ui.view_model import ViewAction


def blocked():
    return ResolvedAction(ViewAction("select", "open", ("ENTER",)), ActionAvailability.BLOCKED, "Preparing")


def test_feedback_only_accepts_a_blocked_action():
    feedback = LoadingKeyFeedback()
    assert not feedback.show(ResolvedAction(ViewAction("back", "back", ("ESC",))), "request", 10.0)
    assert feedback.message("request", 10.1) is None
    assert feedback.show(blocked(), "request", 10.0)
    assert feedback.message("request", 10.1)


def test_feedback_expires_after_one_second_without_saving_old_loading_text():
    feedback = LoadingKeyFeedback()
    feedback.show(blocked(), "request", 10.0)
    assert feedback.message("request", 10.9)
    assert feedback.message("request", 11.0) is None


def test_repeated_keys_do_not_extend_the_message_or_prevent_restoration():
    feedback = LoadingKeyFeedback()
    feedback.show(blocked(), "request", 10.0)
    original = feedback.message("request", 10.1)
    for now in (10.2, 10.5, 10.9, 11.1):
        assert not feedback.show(blocked(), "request", now)
    assert feedback.message("request", 10.9) == original
    assert feedback.message("request", 11.1) is None
    assert feedback.show(blocked(), "request", 11.5)
    assert feedback.message("request", 11.6) != original


@pytest.mark.parametrize("scope", (None, "other request"))
def test_old_feedback_cannot_leak_to_another_request_or_finished_navigation(scope):
    feedback = LoadingKeyFeedback()
    feedback.show(blocked(), "request", 10.0)
    assert feedback.message(scope, 10.1) is None
    assert feedback.message("request", 10.2) is None


def test_supplied_feedback_choices_are_selected_once_without_repetition():
    feedback = LoadingKeyFeedback(picker=MessagePicker(choose=lambda options: options[0]))
    options = ["Already opening it. Just a moment.", "On it. This item is opening."]
    feedback.show(blocked(), "request", 10, messages=options)
    assert feedback.message("request", 10.1) == options[0]
    assert feedback.message("request", 10.9) == options[0]
    assert feedback.message("request", 11) is None
    feedback.show(blocked(), "request", 12, messages=options)
    assert feedback.message("request", 12.1) == options[1]


@pytest.mark.parametrize("messages", ([], ["Same", "Same"], "x" * 121, [f"Message {i}" for i in range(11)]))
def test_feedback_applies_shared_message_contract(messages):
    with pytest.raises(ValueError, match="feedback.messages"):
        LoadingKeyFeedback().show(blocked(), "request", 10, messages=messages)
