"""Shared row text keeps semantic styling separate from filtering content."""

from dataclasses import replace

import pytest

from media_cli.core.terminal import display_width, strip_ansi
from media_cli.ui.row_text import plain_row_text, render_row_text
from media_cli.ui.styled_text import StyledText, TextRun
from media_cli.ui.theme import DEFAULT_THEME, TextStyle


def content():
    return StyledText((TextRun("Episode 12", "muted"), TextRun("  downloaded", "success")))


def test_plain_content_is_identical_for_filtering_and_non_styled_output():
    assert plain_row_text(content()) == "Episode 12  downloaded"
    assert plain_row_text("Episode 12") == "Episode 12"
    assert "\033" not in plain_row_text(content())


def test_roles_come_from_the_supplied_theme():
    theme = replace(DEFAULT_THEME, muted=TextStyle(10), success=TextStyle(11))
    rendered = render_row_text(content(), columns=40, theme=theme)
    assert strip_ansi(rendered) == plain_row_text(content())
    assert theme.prefix(theme.muted) in rendered
    assert theme.prefix(theme.success) in rendered


def test_selected_and_matching_styles_come_from_the_theme():
    theme = replace(DEFAULT_THEME, row_selected=TextStyle(12), row_match=TextStyle(13))
    selected = render_row_text(content(), columns=40, selected=True, theme=theme)
    assert theme.prefix(theme.row_selected) in selected
    assert theme.prefix(theme.success) not in selected
    matching = render_row_text(content(), columns=40, query="ed", theme=theme)
    assert theme.prefix(theme.row_match) in matching
    selected_match = render_row_text(content(), columns=40, query="ed", selected=True, theme=theme)
    assert theme.prefix(replace(theme.row_selected, underline=True)) in selected_match
    assert strip_ansi(selected_match) == plain_row_text(content())


@pytest.mark.parametrize("columns", [0, 1, 8, 12, 80])
def test_clipping_respects_cell_budget_without_mutating_content(columns):
    source = content()
    rendered = render_row_text(source, columns=columns, query="e1")
    assert display_width(strip_ansi(rendered)) <= columns
    assert source == content()


def test_unknown_roles_fail_even_when_hidden_by_clipping():
    source = StyledText((TextRun("visible", "details"), TextRun("hidden", "unknown_role")))
    with pytest.raises(ValueError):
        render_row_text(source, columns=1)


def test_fuzzy_match_can_cross_role_boundaries():
    source = content()
    rendered = render_row_text(source, columns=40, query="2d")
    assert rendered.count(DEFAULT_THEME.prefix(DEFAULT_THEME.row_match)) == 2
    assert strip_ansi(rendered) == source.text


def test_wide_unicode_text_is_clipped_by_cells_not_character_count():
    source = StyledText((TextRun("\u65e5\u672c\u8a9e", "details"), TextRun(" ready", "success")))
    assert display_width(strip_ansi(render_row_text(source, columns=5))) <= 5


@pytest.mark.parametrize("source", ["\033[31munsafe", "line\nbreak", 42])
def test_plain_text_rejects_raw_colors_controls_and_wrong_types(source):
    with pytest.raises((ValueError, TypeError)):
        plain_row_text(source)


@pytest.mark.parametrize("columns", [-1, True, "40"])
def test_invalid_width_fails_early(columns):
    with pytest.raises((ValueError, TypeError)):
        render_row_text(content(), columns=columns)
