"""Navigation keys follow visual ordering in the shared terminal picker."""

import os

import pytest
from test_ui_view_terminal import Terminal

from media_cli.ui.help import picker_help


def test_help_describes_visual_edges_query_editing_and_paging():
    help_items = picker_help()
    assert help_items["Home / End"] == "Top/bottom item; query start/end when filtering"
    assert help_items["Page Up / Down"] == "Move one visible page up/down"


def picker_script(reverse, *, query="", search=False):
    return f"""
finish(picker_module.tui_pick(
    flags, ui, 'NAVIGATION', [f'match item {{index:02}}' for index in range(30)],
    header_fn=lambda index: f'Focused {{index}}\\nSecondary\\nDetails\\nFooter',
    initial_selected=12, reverse_items={reverse!r}, initial_query={query!r},
    disabled_indices={{0, 5, 19, 29}}, heading_indices={{1, 4, 20, 28}},
    return_query_on_enter={search!r}, return_on_quit=True,
))
"""


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("key,top", [(b"\x1b[H", True), (b"\x1b[F", False)])
def test_home_and_end_choose_visual_edges_and_skip_nonselectable_rows(tmp_path, reverse, key, top):
    with Terminal(tmp_path, picker_script(reverse), rows=12) as terminal:
        terminal.send_after(b"\x1b[12;100H", key + b"\r")
        assert terminal.result() == (27 if top == reverse else 2)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("query", ["", "match"])
@pytest.mark.parametrize("key,up", [(b"\x1b[5~", True), (b"\x1b[6~", False)])
def test_page_keys_move_one_visible_page_even_with_filter_text(tmp_path, reverse, query, key, up):
    with Terminal(tmp_path, picker_script(reverse, query=query), rows=12) as terminal:
        terminal.send_after(b"\x1b[12;100H", key + b"\r")
        # Seven row slots per page; skip blocked rows beyond the target.
        assert terminal.result() == (21 if up == reverse else 3)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("key,up", [(b"\x1b[5~", True), (b"\x1b[6~", False)])
def test_paging_clamps_at_visual_edges_instead_of_wrapping(tmp_path, reverse, key, up):
    with Terminal(tmp_path, picker_script(reverse), rows=12) as terminal:
        terminal.send_after(b"\x1b[12;100H", key * 8 + b"\r")
        assert terminal.result() == (27 if up == reverse else 2)


@pytest.mark.parametrize("key,expected", [(b"\x1b[H", "Xmatch"), (b"\x1b[F", "matchX")])
def test_home_and_end_still_edit_query_cursor(tmp_path, key, expected):
    with Terminal(tmp_path, picker_script(True, query="match", search=True), rows=12) as terminal:
        terminal.send_after(b"\x1b[12;100H", key + b"X\r")
        assert terminal.result() == expected


def test_page_distance_updates_after_resize(tmp_path):
    with Terminal(tmp_path, picker_script(True), rows=12) as terminal:
        terminal.until(b"\x1b[12;100H")
        terminal.resize(16, 100)
        os.write(terminal.master, b"\x1b[5~\r")
        assert terminal.result() == 23  # 16 - four detail lines - prompt = 11.


@pytest.mark.parametrize("confirm", [b"\r", b"\x1b[C"])
def test_right_arrow_hint_names_both_confirmation_choices(tmp_path, confirm):
    script = """
finish(picker_module.tui_pick(
    flags, ui, 'CONFIRM', ['match title'], initial_query='match',
    header='Primary\\nSecondary\\nDetails\\nFooter', return_on_quit=True,
))
"""
    with Terminal(tmp_path, script, rows=12) as terminal:
        terminal.send_after(b"\x1b[12;100H", b"\x1b[C")
        terminal.send_after(b"Press Right Arrow again or Enter to select.", confirm)
        assert terminal.result() == 0


@pytest.mark.parametrize("key", [b"\x12", b"\x1b[15~", b"\x1br"])
@pytest.mark.parametrize("change,expected", [
    ("unchanged", 2), ("renamed", 2), ("reordered", 1), ("removed", 2), ("disabled", 1),
])
def test_refresh_keeps_selection_and_filter(tmp_path, key, change, expected):
    script = f"""
from media_cli.ui.picker_state import PickerState
state = PickerState(query='match', selected_index=2)
rows = ['match first', 'match second', 'match selected', 'match last']
def refresh(selected):
    assert selected == 2
    updated = list(rows)
    if {change!r} == 'renamed':
        updated[2] = 'match selected updated'
    elif {change!r} == 'reordered':
        updated[1], updated[2] = updated[2], updated[1]
    elif {change!r} == 'removed':
        updated.pop(2)
    disabled = {{2}} if {change!r} == 'disabled' else set()
    return updated, 'REFRESHED\\nSecondary\\nDetails\\nFooter', disabled
picked = picker_module.tui_pick(
    flags, ui, 'REFRESH-POSITION', rows, initial_selected=2, initial_query='match',
    state=state, header='Primary\\nSecondary\\nDetails\\nFooter',
    reverse_fn=refresh, alt_reverse_fn=refresh, return_on_quit=True,
)
finish(dict(picked=picked, query=state.query))
"""
    with Terminal(tmp_path, script, rows=12) as terminal:
        terminal.send_after(b"\x1b[12;100H", key)
        terminal.send_after(b"REFRESHED", b"\r")
        assert terminal.result() == {"picked": expected, "query": "match"}


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("reorder", [False, True, "duplicates", "renamed"])
def test_duplicate_titles_keep_provider_identity_on_update(tmp_path, live, reorder):
    script = f"""
from media_cli.ui.picker_state import PickerState
state = PickerState(query='match', selected_index=2)
rows = [('provider-a', 'match same'), ('other', 'match other'), ('provider-b', 'match same')]
header = 'Primary\\nSecondary\\nDetails\\nFooter'
def refresh(selected):
    global header
    assert rows[selected][0] == 'provider-b'
    if {reorder!r} == 'duplicates':
        rows[0], rows[2] = rows[2], rows[0]
    elif {reorder!r} == 'renamed':
        rows[2] = ('provider-b', 'match same updated')
    elif {reorder!r}:
        rows[1], rows[2] = rows[2], rows[1]
    header = 'UPDATED\\nSecondary\\nDetails\\nFooter'
    return None if {live!r} else ([label for _, label in rows], header)
picked = picker_module.tui_pick(
    flags, ui, 'IDENTITY', [label for _, label in rows], state=state,
    header=header, reverse_fn=refresh, return_on_quit=True,
    item_key_fn=lambda index: rows[index][0],
    live_fn=(lambda query: ([label for _, label in rows], header, True)) if {live!r} else None,
)
finish(dict(provider=rows[picked][0], picked=picked, query=state.query))
"""
    with Terminal(tmp_path, script, rows=12) as terminal:
        terminal.send_after(b"\x1b[12;100H", b"\x12")
        terminal.send_after(b"UPDATED", b"\r")
        expected = 0 if reorder == "duplicates" else 1 if reorder is True else 2
        assert terminal.result() == {"provider": "provider-b", "picked": expected, "query": "match"}
