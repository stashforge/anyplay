"""Inline confirmation returns a decision; application code owns side effects."""

import os
from types import SimpleNamespace

import pytest
from test_ui_view_terminal import Terminal

from media_cli.ui.confirmation import confirm_picker_action


@pytest.mark.parametrize("key,expected", [(b"y", True), (b"n", False), (b"\r", False), (b"\x1b", False)])
def test_confirmation_uses_existing_picker_layout_and_default_no(tmp_path, key, expected):
    source = """
from media_cli.ui.confirmation import confirm_picker_action
decision = confirm_picker_action(flags, ui, 'Select episode', ['Episode 1', 'Episode 2'],
    selected_index=1, question='Delete downloaded Episode 2?',
    header_fn=lambda index: 'TITLE\nALT\nMETADATA\nFOOTER')
finish(dict(confirmed=decision))
""".replace("'TITLE\nALT\nMETADATA\nFOOTER'", "'TITLE\\nALT\\nMETADATA\\nFOOTER'")
    with Terminal(tmp_path, source) as terminal:
        output = terminal.until(b"Delete downloaded Episode 2? y/N")
        assert b"Episode 1" in output and b"METADATA" in output
        os.write(terminal.master, key)
        assert terminal.result() == {"confirmed": expected}


@pytest.mark.parametrize("structured", [False, True])
def test_confirmation_does_not_turn_ctrl_c_into_no(tmp_path, structured):
    source = """
from media_cli.ui.confirmation import confirm_picker_action
try:
    confirm_picker_action(flags, ui, 'Pick', ['Row'], selected_index=0, question='Delete row?',
                          return_on_quit=STRUCTURED)
except KeyboardInterrupt:
    finish(dict(interrupted=True))
else:
    finish(dict(interrupted=False))
""".replace("STRUCTURED", repr(structured))
    with Terminal(tmp_path, source) as terminal:
        terminal.until(b"Delete row? y/N")
        os.write(terminal.master, b"\x03")
        assert terminal.result() == {"interrupted": True}


@pytest.mark.parametrize("answer,expected", [("y", True), ("Y", True), ("", False), ("n", False)])
def test_plain_confirmation_is_an_explicit_default_no_decision(answer, expected):
    prompts = []

    def read(prompt):
        prompts.append(prompt)
        return answer

    assert confirm_picker_action(
        SimpleNamespace(plain_mode=True), SimpleNamespace(), "Pick", ["Row"],
        selected_index=0, question="Delete row?", input_fn=read,
    ) is expected
    assert prompts == ["Delete row? y/N: "]


@pytest.mark.parametrize("kwargs", [
    {"selected_index": -1}, {"selected_index": True}, {"question": "\033[31mDelete?"},
    {"question": ""}, {"disabled_indices": {0}}, {"delete_fn": lambda index: None},
])
def test_invalid_or_side_effecting_confirmation_data_fails_before_reading_input(kwargs):
    with pytest.raises((ValueError, TypeError)):
        confirm_picker_action(
            SimpleNamespace(plain_mode=True), SimpleNamespace(), "Pick", ["Row"],
            input_fn=lambda *a: pytest.fail("Read before validation"),
            **({"selected_index": 0, "question": "Delete row?"} | kwargs),
        )
