import pytest

from media_cli.ui.message_choices import MessagePicker, MessageRotation, normalize_messages


@pytest.mark.parametrize("value", ("Wait a moment.", ["Wait a moment."], ("Wait a moment.",)))
def test_one_message_accepts_all_supported_forms(value):
    assert normalize_messages(value, field="loading.message") == ("Wait a moment.",)


def test_choices_are_copied_to_tuple_and_limits_are_inclusive():
    values = [f"Message {i}" for i in range(10)]
    result = normalize_messages(values)
    values.append("changed")
    assert isinstance(result, tuple) and len(result) == 10
    assert normalize_messages("x" * 120) == ("x" * 120,)


@pytest.mark.parametrize("value,fragment", (
    ([], "nonempty"), ((), "nonempty"), (["x"] * 11, "10"),
    ("x" * 121, "120"), (["Fine", "x" * 121], "120"),
    ("", "nonblank"), ("   ", "nonblank"),
    (["Same", "Same"], "duplicate"), (["Same", " Same "], "duplicate"),
    ("x\n", "control"), ("\x1b[31mred", "control"), ("x\x85", "control"),
))
def test_invalid_values_name_the_field_and_relevant_limit(value, fragment):
    with pytest.raises(ValueError, match=fragment) as error:
        normalize_messages(value, field="loading.message")
    assert "loading.message" in str(error.value)


@pytest.mark.parametrize("value", (None, 10, {"x"}, iter(("x",)), ["x", 10], [["x"]]))
def test_wrong_types_fail_instead_of_stringifying(value):
    with pytest.raises(TypeError, match="loading.message"):
        normalize_messages(value, field="loading.message")


def test_picker_avoids_previous_choice_between_tasks_and_current_choice_in_task():
    candidates = []

    def first(options):
        candidates.append(options)
        return options[0]

    picker = MessagePicker(choose=first)
    options = ("One", "Two", "Three")
    assert picker.pick(options) == "One"
    assert picker.pick(options) == "Two"
    assert picker.pick(options, previous="Three") == "One"
    assert candidates == [options, ("Two", "Three")]


def test_rotation_is_stable_between_deadlines_and_changes_once_after_long_gap():
    now, calls = [0.0], []
    picker = MessagePicker(choose=lambda options: options[0])

    def interval():
        calls.append(True)
        return 4.0

    rotation = MessageRotation(("One", "Two"), clock=lambda: now[0], picker=picker, interval=interval)
    for moment in (0, 1, 3.99):
        now[0] = moment
        assert rotation.text() == "One"
    now[0] = 4
    assert rotation.text() == "Two"
    assert rotation.text() == "Two"
    now[0] = 100
    assert rotation.text() == "One"
    assert len(calls) == 3


def test_repeated_status_does_not_postpone_rotation_but_real_progress_does():
    now = [0.0]
    rotation = MessageRotation(
        ("One", "Two"), clock=lambda: now[0],
        picker=MessagePicker(choose=lambda options: options[0]), interval=lambda: 4,
    )
    now[0] = 3
    rotation.update(("One", "Two"))
    now[0] = 4
    assert rotation.text() == "Two"
    now[0] = 7
    rotation.update(("One", "Two"), progress_changed=True)
    now[0] = 8
    assert rotation.text() == "Two"
    now[0] = 11
    assert rotation.text() == "One"


def test_new_task_text_takes_priority_and_resume_restarts_rotation_timer():
    now = [0.0]
    rotation = MessageRotation(
        ("One", "Two"), clock=lambda: now[0],
        picker=MessagePicker(choose=lambda options: options[0]), interval=lambda: 3,
    )
    now[0] = 2
    rotation.update(("Fresh", "New"))
    assert rotation.text() == "Fresh"
    now[0] = 30
    rotation.restart()
    assert rotation.text() == "Fresh"
    now[0] = 33
    assert rotation.text() == "New"


def test_single_message_never_rotates_or_calls_randomness():
    def unwanted(*args):
        pytest.fail("single message must not request randomness")

    rotation = MessageRotation("Only one", picker=MessagePicker(choose=unwanted), interval=unwanted)
    assert rotation.text() == "Only one"
    rotation.restart()
    assert rotation.text() == "Only one"


@pytest.mark.parametrize("interval", (2.9, 5.1, float("nan"), float("inf"), True, "4"))
def test_invalid_injected_rotation_interval_fails(interval):
    with pytest.raises((TypeError, ValueError)):
        MessageRotation(("One", "Two"), interval=lambda: interval)


def test_picker_rejects_invalid_injected_choice():
    with pytest.raises(ValueError, match="choice"):
        MessagePicker(choose=lambda options: "Unknown").pick(("One", "Two"))


def test_existing_episode_and_file_tasks_supply_valid_alternatives():
    from media_cli.ui.loading_messages import episode_indexing, local_file_scan

    for messages in (episode_indexing(), episode_indexing(12), local_file_scan()):
        assert isinstance(messages, tuple) and len(messages) > 1
        assert normalize_messages(messages) == messages
    assert all("12" in message for message in episode_indexing(12))
