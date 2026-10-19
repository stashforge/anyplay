"""A soft width guard preserves picker state and does not replay blocked input."""

import os
import time

import pytest
from test_ui_view_terminal import Terminal, row_writes


def state_script():
    return """
from media_cli.ui.picker_state import PickerState
from media_cli.ui.query_cursor import QueryCursor
state = PickerState(query='match', selected_index=12, scroll=8, marked_indices={12},
                    cursor_activity=QueryCursor(None))
value = picker_module.tui_pick(flags, ui, 'Select title', [f'match title {i}' for i in range(20)],
    state=state, multi_select=True, return_on_quit=True,
    header='Primary\\nSecondary\\nDetails\\nFooter')
finish({'value': value, 'state': asdict(state)})
"""


def test_narrow_notice_blocks_actions_and_restores_filter_selection_and_marks(tmp_path):
    with Terminal(tmp_path, state_script(), rows=12, columns=31) as terminal:
        frame = terminal.until(b"\x1b[12;31H")
        lines = [line for _, line in row_writes(frame)]
        assert any("Resize to 32 columns" in line for line in lines)
        assert not any("match title" in line for line in lines)
        os.write(terminal.master, b"X\x1b[A\t\r" * 10)
        terminal.resize(12, 80)
        os.write(terminal.master, b"\r")
        result = terminal.result()
        assert result["value"] == [12]
        assert result["state"] == {
            "query": "match", "selected_index": 12, "scroll": 8, "marked_indices": [12],
            "query_cursor": 5, "cursor_activity": {"active_since": None},
        }


def test_shrinking_an_open_picker_replaces_and_restores_the_view(tmp_path):
    with Terminal(tmp_path, state_script(), rows=12, columns=80) as terminal:
        terminal.until(b"\x1b[12;80H")
        frame = terminal.resize(12, 20)
        assert any("Resize to 32" in line for _, line in row_writes(frame))
        terminal.resize(12, 80)
        os.write(terminal.master, b"\r")
        assert terminal.result()["value"] == [12]


@pytest.mark.parametrize("key,expected", [(b"\x1b", -2), (b"\x03", -5)])
def test_cancel_and_quit_work_below_minimum_width(tmp_path, key, expected):
    with Terminal(tmp_path, state_script(), rows=12, columns=20) as terminal:
        terminal.send_after(b"\x1b[12;20H", key)
        result = terminal.result()
        assert result["value"] == expected
        assert result["state"]["query"] == "match"


def test_background_updates_continue_but_auto_entry_waits_for_resize(tmp_path):
    script = """
started = time.monotonic()
calls = 0
def live(query):
    global calls
    calls += 1
    ready = time.monotonic() - started > 0.15
    return (['Ready title'] if ready else []), ('Ready' if ready else 'Loading'), ready
value = picker_module.tui_pick(flags, ui, 'Search', [], live_fn=live,
    auto_select_single_when_done=True, return_on_quit=True)
finish({'value': value, 'calls': calls})
"""
    with Terminal(tmp_path, script, rows=12, columns=31) as terminal:
        terminal.until(b"\x1b[12;31H")
        time.sleep(0.3)
        assert terminal.proc.poll() is None
        terminal.resize(12, 80)
        result = terminal.result()
        assert result["value"] == 0
        assert result["calls"] >= 3


def test_plain_mode_does_not_enforce_tui_width(tmp_path):
    script = """
flags.plain_mode = True
finish(picker_module.tui_pick(flags, ui, 'Pick plain', ['Only title'], return_on_quit=True))
"""
    with Terminal(tmp_path, script, rows=12, columns=20) as terminal:
        terminal.send_after(b"Enter choice:", b"1\n")
        assert terminal.result() == 0
        assert b"Resize to" not in terminal.output


def test_guard_clears_native_cover_and_restores_it_after_resize(tmp_path):
    script = """
from media_cli.ui import terminal_images
flags.show_image = True
picker_module._poster_uses_native_protocol = lambda raw: True
picker_module._poster_symbol_lines = lambda raw, height, width: [''] * height
original_clear = terminal_images.clear_if_active
def observed_clear():
    active = terminal_images._active
    return ('COVER_CLEARED' if active else '') + original_clear()
terminal_images.clear_if_active = observed_clear
finish(picker_module.tui_pick(flags, ui, 'Pick', ['Title'],
    top_header_fn=lambda index: 'NATIVE_COVER',
    header='Primary\\nSecondary\\nDetails\\nFooter', return_on_quit=True))
"""
    with Terminal(tmp_path, script, rows=24, columns=80) as terminal:
        initial = terminal.until(b"\x1b[24;80H")
        assert b"NATIVE_COVER" in initial
        narrow = terminal.resize(24, 31)
        assert b"COVER_CLEARED" in narrow
        assert b"NATIVE_COVER" not in narrow
        restored = terminal.resize(24, 80)
        assert b"NATIVE_COVER" in restored
        os.write(terminal.master, b"\x1b")
        assert terminal.result() == -2
