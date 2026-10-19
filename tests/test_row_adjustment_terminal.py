import pytest
from test_ui_view_terminal import Terminal


@pytest.mark.parametrize(
    "query,cursor,keys,direction,final_cursor",
    (
        ("", 0, b"\x1b[D", -1, 0),
        ("", 0, b"\x1b[C", 1, 0),
        ("buffer", 0, b"\x1b[D", -1, 0),
        ("buffer", 6, b"\x1b[C", 1, 6),
        ("buffer", 3, b"\x1b[D\x1b[H\x1b[D", -1, 0),
        ("buffer", 3, b"\x1b[C\x1b[F\x1b[C", 1, 6),
    ),
)
def test_boundary_adjustment_retains_filter_and_caret(tmp_path, query, cursor, keys, direction, final_cursor):
    script = f"""
from media_cli.ui.picker_state import PickerState
state = PickerState(query={query!r}, query_cursor={cursor}, selected_index=1)
adjustments = []
def adjust(index, direction):
    adjustments.append((index, direction))
    return True
result = picker_module.tui_pick(flags, ui, 'ADJUST-ROW', ['other', 'buffer'],
    state=state, adjust_fn=adjust, return_on_quit=True,
    header='Primary\\nSecondary\\nDetails\\nFooter')
finish(dict(result=result, adjustments=adjustments, query=state.query, cursor=state.query_cursor,
    selected=state.selected_index, quiet=state.cursor_activity.active_since is None))
"""
    with Terminal(tmp_path, script, rows=12) as terminal:
        terminal.send_after(b"\033[12;100H", keys)
        assert terminal.result() == {
            "result": -6,
            "adjustments": [[1, direction]],
            "query": query,
            "cursor": final_cursor,
            "selected": 1,
            "quiet": not bool(query),
        }


def test_left_at_filter_start_never_falls_back_to_back(tmp_path):
    script = """
from media_cli.ui.picker_state import PickerState
state = PickerState(query='buffer', query_cursor=0)
result = picker_module.tui_pick(flags, ui, 'ORDINARY-ROW', ['buffer'], state=state,
    adjust_fn=lambda index, direction: False, return_on_quit=True,
    header='Primary\\nSecondary\\nDetails\\nFooter')
finish(dict(result=result, query=state.query))
"""
    with Terminal(tmp_path, script, rows=12) as terminal:
        terminal.send_after(b"\033[12;100H", b"\x1b[D" * 3 + b"\x1b")
        assert terminal.result() == {"result": -2, "query": "buffer"}


def test_nonadjustable_right_still_opens_without_filter(tmp_path):
    script = """
finish(picker_module.tui_pick(flags, ui, 'ORDINARY-ROW', ['Open submenu'],
    adjust_fn=lambda index, direction: False, return_on_quit=True,
    header='Primary\\nSecondary\\nDetails\\nFooter'))
"""
    with Terminal(tmp_path, script, rows=12) as terminal:
        terminal.send_after(b"\033[12;100H", b"\x1b[C")
        assert terminal.result() == 0


def test_query_entry_never_invokes_row_adjustment(tmp_path):
    script = """
def adjust(index, direction):
    raise AssertionError('Query entry must own arrows')
finish(picker_module.tui_pick(flags, ui, 'QUERY-ENTRY', ['ignored'],
    adjust_fn=adjust, return_query_on_enter=True, return_on_quit=True))
"""
    with Terminal(tmp_path, script, rows=12) as terminal:
        terminal.send_after(b"\033[12;100H", b"\x1b[DX\x1b[C\r")
        assert terminal.result() == "X"


def test_disabled_rows_never_invoke_adjustment(tmp_path):
    script = """
def adjust(index, direction):
    raise AssertionError('Disabled rows cannot adjust')
finish(picker_module.tui_pick(flags, ui, 'DISABLED', ['Heading'], disabled_indices={0},
    adjust_fn=adjust, return_on_quit=True))
"""
    with Terminal(tmp_path, script, rows=12) as terminal:
        terminal.send_after(b"\033[12;100H", b"\x1b[C\x1b")
        assert terminal.result() == -2
