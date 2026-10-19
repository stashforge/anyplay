"""Query layout keeps the caret visible within its allocated cell budget."""

import importlib
from dataclasses import FrozenInstanceError, replace

import pytest

from media_cli.core.terminal import display_width, strip_ansi
from media_cli.ui.styled_text import render_styled_text
from media_cli.ui.theme import DEFAULT_THEME, TextStyle


def component():
    return importlib.import_module("media_cli.ui.query_viewport")


def layout(text, cursor, columns, *, start=0, visible=True):
    module = component()
    return module.layout_query(
        module.QueryField(text=text, cursor=cursor, cursor_visible=visible), columns=columns, start=start
    )


def plain(result):
    return "".join(run.text for run in result.content.runs)


def test_empty_query_has_one_cursor_cell():
    result = layout("", 0, 8)
    assert plain(result) == " "
    assert result.cursor_column == 0
    assert result.start == result.end == 0
    assert not result.hidden_before and not result.hidden_after
    assert result.content.runs[0].role == "query_cursor"


def test_cursor_styles_the_character_under_it():
    result = layout("hello", 2, 10)
    assert tuple((run.text, run.role) for run in result.content.runs) == (
        ("he", "query"),
        ("l", "query_cursor"),
        ("lo", "query"),
    )
    assert result.cursor_column == 2
    assert (result.start, result.end) == (0, 5)


def test_end_cursor_scrolls_query_and_reserves_its_own_cell():
    result = layout("abcdef", 6, 4)
    assert plain(result) == "def "
    assert result.cursor_column == 3
    assert (result.start, result.end) == (3, 6)
    assert result.hidden_before and not result.hidden_after


def test_viewport_scrolls_only_when_cursor_leaves_it():
    result = layout("abcdefghij", 5, 5, start=3)
    assert plain(result) == "defgh"
    assert result.start == 3 and result.cursor_column == 2
    assert result.hidden_before and result.hidden_after
    moved = layout("abcdefghij", 1, 5, start=result.start)
    assert moved.start == 1 and moved.cursor_column == 0
    assert plain(moved) == "bcdef"


def test_blink_off_keeps_the_same_cells_and_window():
    on, off = layout("abcdefgh", 6, 4), layout("abcdefgh", 6, 4, visible=False)
    assert plain(on) == plain(off)
    assert (on.start, on.end, on.cursor_column) == (off.start, off.end, off.cursor_column)
    assert "query_cursor" not in [run.role for run in off.content.runs]


def test_wide_character_and_combining_marks_are_not_split():
    result = layout("A\u754ce\u0301Z", 2, 3)
    assert plain(result) == "\u754ce\u0301"
    assert result.cursor_column == 2
    assert result.content.runs[-1].text == "e\u0301"
    inside_mark = layout("e\u0301x", 1, 2, start=1)
    assert plain(inside_mark) == "e\u0301x"
    assert inside_mark.start == 0 and inside_mark.cursor_column == 0
    assert inside_mark.content.runs[0].text == "e\u0301"


def test_one_column_wide_cursor_uses_space_without_partial_glyph():
    result = layout("\u754cx", 0, 1)
    assert plain(result) == " "
    assert result.cursor_column == 0
    assert result.start == result.end == 0
    assert result.hidden_after


def test_initial_combining_mark_has_an_independent_display_base():
    result = layout("\u0301x", 0, 3)
    assert plain(result) == "\u25cc\u0301x"
    assert result.cursor_column == 0


@pytest.mark.parametrize("columns", [1, 2, 3, 4, 8, 40])
@pytest.mark.parametrize("text", ["abcdefghijk", "a\u754ce\u0301z", ""])
def test_every_cursor_position_fits_and_remains_visible(columns, text):
    for cursor in range(len(text) + 1):
        result = layout(text, cursor, columns)
        assert display_width(plain(result)) <= columns
        assert 0 <= result.cursor_column < columns
        assert 0 <= result.start <= result.end <= len(text)
        assert len([run for run in result.content.runs if run.role == "query_cursor"]) == 1


def test_resizing_with_previous_offset_reflows_around_cursor():
    small = layout("abcdefghij", 8, 3)
    large = layout("abcdefghij", 8, 8, start=small.start)
    assert large.start == small.start
    narrow = layout("abcdefghij", 8, 1, start=large.start)
    assert plain(narrow) == "i" and narrow.cursor_column == 0


def test_cursor_theme_is_customizable_and_restores_canvas():
    result = layout("abc", 1, 5)
    theme = replace(DEFAULT_THEME, query_cursor=TextStyle(81, background=82, bold=True), background=17)
    rendered = render_styled_text(result.content, width=6, theme=theme)
    assert strip_ansi(rendered) == "abc"
    assert theme.prefix(theme.query_cursor) + "b" in rendered
    assert rendered.endswith(theme.reset)


@pytest.mark.parametrize(
    "text, cursor, visible, error",
    [
        (None, 0, True, TypeError),
        ("a", True, True, TypeError),
        ("a", -1, True, ValueError),
        ("a", 2, True, ValueError),
        ("a", 0, 1, TypeError),
        ("a\n", 0, True, ValueError),
        ("\033[31ma", 0, True, ValueError),
    ],
)
def test_invalid_query_data_fails_at_construction(text, cursor, visible, error):
    with pytest.raises(error):
        component().QueryField(text=text, cursor=cursor, cursor_visible=visible)


@pytest.mark.parametrize(
    "columns, start, error",
    [(0, 0, ValueError), (True, 0, TypeError), (4, -1, ValueError), (4, 5, ValueError), (4, True, TypeError)],
)
def test_invalid_layout_inputs_fail(columns, start, error):
    with pytest.raises(error):
        layout("abc", 0, columns, start=start)


def test_query_and_layout_are_immutable():
    module = component()
    query = module.QueryField(text="abc", cursor=1, cursor_visible=True)
    result = module.layout_query(query, columns=3)
    with pytest.raises(FrozenInstanceError):
        query.cursor = 2
    with pytest.raises(FrozenInstanceError):
        result.start = 1
