import os

import pytest
from test_ui_view_terminal import Terminal


@pytest.mark.parametrize("key,action", [(b"\r", "select"), (b"\x1b[C", "select"), (b"\x1b[A", "move")])
def test_empty_live_results_feedback_restores_and_does_not_replay_selection(tmp_path, key, action):
    gate = tmp_path / "ready"
    script = f"""
from pathlib import Path
picker_module.result_wait_messages = lambda action: 'WAITING-' + action
def live(query):
    if Path({str(gate)!r}).exists():
        return ['One', 'Two'], 'Title\\nSecondary\\nMetadata\\nREADY', True
    return [], 'Title\\nSecondary\\nMetadata\\nSEARCHING', False
finish(picker_module.tui_pick(flags, ui, 'Search', [], live_fn=live, return_on_quit=True))
"""
    with Terminal(tmp_path, script, rows=12) as terminal:
        terminal.send_after(b"SEARCHING", key)
        terminal.until(f"WAITING-{action}".encode())
        terminal.until(b"SEARCHING")
        gate.touch()
        terminal.until(b"READY")
        os.write(terminal.master, b"\x1b")
        assert terminal.result() == -2


def test_results_remain_selectable_while_background_search_continues(tmp_path):
    script = """
def live(query):
    return ['First result', 'Second result'], 'Title\\nSecondary\\nMetadata\\nSTILL-SEARCHING', False
finish(picker_module.tui_pick(flags, ui, 'Search', [], live_fn=live, return_on_quit=True))
"""
    with Terminal(tmp_path, script, rows=12) as terminal:
        terminal.send_after(b"STILL-SEARCHING", b"\r")
        assert terminal.result() in (0, 1)
        assert b"Nothing to pick" not in terminal.output


def test_search_input_still_accepts_typing_and_cursor_editing(tmp_path):
    script = """
seen = ''
def live(query):
    global seen
    seen = query
    return [], 'Title\\nSecondary\\nMetadata\\nSEARCHING', False
picker_module.tui_pick(flags, ui, 'Search', [], live_fn=live,
    return_query_on_enter=True, return_on_quit=True, exit_fn=lambda: seen == 'XabcY')
finish(seen)
"""
    with Terminal(tmp_path, script, rows=12) as terminal:
        terminal.send_after(b"SEARCHING", b"abc\x1b[HX\x1b[FY")
        assert terminal.result() == "XabcY"


@pytest.mark.parametrize("key", [b"\x1b[D", b"\x1b", b"\x03"])
def test_back_and_quit_are_not_replaced_by_wait_feedback(tmp_path, key):
    script = """
try:
    finish(picker_module.tui_pick(flags, ui, 'Search', [],
        live_fn=lambda q: ([], 'Title\\nSecondary\\nMetadata\\nSEARCHING', False), return_on_quit=True))
except KeyboardInterrupt:
    finish('interrupted')
"""
    with Terminal(tmp_path, script, rows=12) as terminal:
        terminal.send_after(b"SEARCHING", key)
        assert terminal.result() == {b"\x1b[D": -3, b"\x1b": -2, b"\x03": -5}[key]
