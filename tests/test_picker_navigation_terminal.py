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
