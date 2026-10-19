import os
import re

from test_ui_view_terminal import Terminal

CURSOR_BACKGROUND = "48;2;137;220;235"


def navigation_lines(output, marker):
    rows = re.findall(rb"\x1b\[\d+;1H\x1b\[2K(.*?)(?=\x1b\[\d+;\d+H|\Z)", output, re.S)
    return [row.decode() for row in rows if marker in row]


def test_option_navigation_hides_cursor_and_typing_wakes_it(tmp_path):
    script = """
finish(picker_module.tui_pick(flags, ui, 'CURSOR-ACTIVITY', ['x one', 'x two'],
    header='Primary\\nSecondary\\nDetails\\nFooter', return_on_quit=True))
"""
    with Terminal(tmp_path, script, rows=12) as terminal:
        terminal.until(b"\033[12;100H")
        assert CURSOR_BACKGROUND in terminal.output.decode()
        offset = len(terminal.output)
        os.write(terminal.master, b"\x1b[A")
        terminal.cursor = offset
        terminal.until(b"\033[12;100H")
        navigation = navigation_lines(bytes(terminal.output[offset:]), b"CURSOR-ACTIVITY")
        assert navigation and all(CURSOR_BACKGROUND not in text for text in navigation)
        offset = len(terminal.output)
        os.write(terminal.master, b"x")
        terminal.cursor = offset
        terminal.until(b"\033[12;100H")
        typing = navigation_lines(bytes(terminal.output[offset:]), b"CURSOR-ACTIVITY")
        assert typing and any(CURSOR_BACKGROUND in text for text in typing)
        os.write(terminal.master, b"\x03")
        assert terminal.result() == -5


def test_same_screen_refresh_preserves_filter_cursor_and_active_blink(tmp_path):
    script = """
from media_cli.ui.picker_state import PickerState
state = PickerState()
first = picker_module.tui_pick(flags, ui, 'SETTING-BEFORE', ['buffer 1'],
    state=state, header='Primary\\nSecondary\\nDetails\\nFooter', return_on_quit=True)
second = picker_module.tui_pick(flags, ui, 'SETTING-AFTER', ['buffer 2'],
    state=state, header='Primary\\nSecondary\\nDetails\\nFooter', return_on_quit=True)
finish(dict(first=first, second=second, query=state.query, cursor=state.query_cursor))
"""
    with Terminal(tmp_path, script, rows=12) as terminal:
        terminal.send_after(b"\033[12;100H", b"buffer\x1b[D\r")
        terminal.until(b"SETTING-AFTER")
        terminal.until(b"\033[12;100H")
        rows = navigation_lines(bytes(terminal.output), b"SETTING-AFTER")
        assert rows and any(CURSOR_BACKGROUND in text for text in rows)
        os.write(terminal.master, b"\x03")
        result = terminal.result()
        assert result == {"first": 0, "second": -5, "query": "buffer", "cursor": 5}
