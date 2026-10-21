"""Confirmations reuse shared view presentation and return only a decision."""

import os

import pytest
from test_ui_view_terminal import Terminal

from media_cli.context import CliFlags, UiState
from media_cli.ui.view_model import Content, DetailLines, Inherit, ViewItem, ViewSpec


def view():
    return ViewSpec(
        "example", "Choose", (ViewItem("one", "First", details=Inherit),),
        details=DetailLines(Content("TITLE"), Content("SECONDARY"), Content("METADATA")),
    )


@pytest.mark.parametrize("key,expected", [(b"y", True), (b"n", False), (b"\r", False), (b"\x1b", False)])
def test_shared_confirmation_uses_view_rows_and_detail_lines(tmp_path, key, expected):
    source = """
from media_cli.ui.confirmation import confirm_view_action
from media_cli.ui.styled_text import StyledText, TextRun
view = ViewSpec('example', 'Choose', (
    ViewItem('one', 'First', details=Inherit),
    ViewItem('two', StyledText((TextRun('Second', 'muted'),)),
             hint=StyledText((TextRun('ready to watch', 'muted'),)), details=Inherit),
), details=DetailLines(Content('TITLE'), Content('SECONDARY'), Content('METADATA')))
ui._view_positions = {'sentinel': 'unchanged'}
decision = confirm_view_action(flags, ui, view, item_id='two', question='Delete second?')
finish(dict(confirmed=decision, memory=ui._view_positions))
"""
    with Terminal(tmp_path, source) as terminal:
        output = terminal.until(b"Delete second? y/N")
        assert all(text in output for text in (b"TITLE", b"SECONDARY", b"METADATA", b"ready to watch"))
        os.write(terminal.master, key)
        assert terminal.result() == {"confirmed": expected, "memory": {"sentinel": "unchanged"}}


def test_shared_confirmation_preserves_interrupt(tmp_path):
    source = """
from media_cli.ui.confirmation import confirm_view_action
view = ViewSpec('example', 'Choose', (ViewItem('one', 'First', details=Inherit),),
                details=DetailLines(Blank, Blank, Blank))
try:
    confirm_view_action(flags, ui, view, item_id='one', question='Delete first?')
except KeyboardInterrupt:
    finish(dict(interrupted=True))
else:
    finish(dict(interrupted=False))
"""
    with Terminal(tmp_path, source) as terminal:
        terminal.until(b"Delete first? y/N")
        os.write(terminal.master, b"\x03")
        assert terminal.result() == {"interrupted": True}


def test_shared_plain_confirmation_stays_plain():
    from media_cli.ui.confirmation import confirm_view_action

    prompts = []

    def read(prompt):
        prompts.append(prompt)
        return "y"

    assert confirm_view_action(
        CliFlags(plain_mode=True), UiState(), view(), item_id="one", question="Delete first?", input_fn=read,
    ) is True
    assert prompts == ["Delete first? y/N: "]


@pytest.mark.parametrize("item_id,question", [("missing", "Delete?"), ("one", ""), ("one", "\033[31mDelete?")])
def test_invalid_confirmation_fails_before_input(item_id, question):
    from media_cli.ui.confirmation import confirm_view_action

    with pytest.raises((TypeError, ValueError)):
        confirm_view_action(
            CliFlags(plain_mode=True), UiState(), view(), item_id=item_id, question=question,
            input_fn=lambda *args: pytest.fail("Read invalid confirmation"),
        )


@pytest.mark.parametrize("key", [b"\x04", b"\x1b[3~"])
def test_confirmation_inherits_filter_and_keeps_source_position(tmp_path, key):
    source = """
import media_cli.ui.confirmation as confirmation
original = confirmation.confirm_picker_action
captured = {}
def inspect(*args, **kwargs):
    state = kwargs['state']
    captured.update(query=state.query, cursor=state.query_cursor, scroll=state.scroll,
                    selected=state.selected_index)
    return original(*args, **kwargs)
confirmation.confirm_picker_action = inspect
view = ViewSpec('example', 'Choose', (
    ViewItem('one', 'First', details=Inherit),
    ViewItem('two', 'Second', details=Inherit),
), details=DetailLines(Content('TITLE'), Blank, Blank),
   actions=(ViewAction('delete', 'Delete', ('CTRL_D', 'DELETE')),))
def handle(event):
    if event.kind == 'action' and event.value == 'delete':
        before = (session.position.query, session.position.selected_id, session.position.scroll)
        captured['confirmed'] = confirmation.confirm_view_action(
            flags, ui, view, item_id=event.item_id, question='Delete second?')
        captured['unchanged'] = before == (session.position.query, session.position.selected_id, session.position.scroll)
        return None
    if event.kind == 'select':
        return ExitSession(event.item_id)
session = ViewSession(view, handle)
selected = run_view_session(flags, ui, session, poster_manager=manager)
finish(dict(captured, final_query=session.position.query, final_selected=selected))
"""
    with Terminal(tmp_path, source) as terminal:
        terminal.send_after(b"TITLE", b"sec" + key)
        terminal.send_after(b"Delete second? y/N", b"n\r")
        assert terminal.result() == {
            "query": "sec", "cursor": 3, "scroll": 0, "selected": 1,
            "confirmed": False, "unchanged": True, "final_query": "sec", "final_selected": "two",
        }
