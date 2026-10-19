"""Navigation pieces share a simple bounded layout, not responsive screen modes."""

from dataclasses import replace

import pytest

from media_cli.core.terminal import display_width, strip_ansi
from media_cli.ui.navigation_bar import NavigationBar, render_navigation_bar
from media_cli.ui.navigation_counter import CounterMode, NavigationCounter
from media_cli.ui.navigation_tabs import NavigationTabs
from media_cli.ui.query_viewport import QueryField
from media_cli.ui.scroll_indicators import ScrollIndicators
from media_cli.ui.styled_text import StyledText, TextRun
from media_cli.ui.theme import DEFAULT_THEME
from media_cli.ui.view_model import ViewTab


def text(value, role="prompt"):
    return StyledText((TextRun(value, role),))


def bar(**kwargs):
    fields = dict(
        prompt=text("Search AnimeThemes"),
        tabs=NavigationTabs((ViewTab("anime", "Anime"), ViewTab("artist", "Artist")), "artist"),
        marker=text("\u276f"),
        query=QueryField("mi", 2, True),
        counters=(NavigationCounter(CounterMode.SELECTED, 2), NavigationCounter(CounterMode.FILTERED, 1, 10)),
        scroll=ScrollIndicators(3, 4),
        rule=False,
    )
    fields.update(kwargs)
    return NavigationBar(**fields)


def test_normal_layout_preserves_active_tab_position_and_piece_order():
    result = render_navigation_bar(bar(), width=100)
    assert strip_ansi(result) == "Search AnimeThemes \u00b7 Artist \u276f mi   2 selected | 1/10 \u25b23 \u25bc4"
    assert DEFAULT_THEME.prefix(DEFAULT_THEME.tab_active) + "Artist" in result
    assert DEFAULT_THEME.prefix(DEFAULT_THEME.query_cursor) + " " in result


def test_long_query_scrolls_without_swallowing_core_counter_and_active_tab():
    result = render_navigation_bar(bar(query=QueryField("x" * 80 + "TAIL", 84, True)), width=32)
    assert "Artist" in strip_ansi(result)
    assert "1/10" in strip_ansi(result)
    assert DEFAULT_THEME.prefix(DEFAULT_THEME.query_cursor) in result
    assert display_width(result) <= 31


def test_rule_uses_only_spare_space():
    value = bar()
    with_rule = render_navigation_bar(replace(value, rule=True), width=100)
    without_rule = render_navigation_bar(value, width=100)
    assert strip_ansi(with_rule).startswith(strip_ansi(without_rule) + " ")
    assert display_width(with_rule) == 99
    assert "\u2500" not in strip_ansi(render_navigation_bar(replace(value, rule=True), width=32))


@pytest.mark.parametrize("width", [1, 2, 8, 16, 31, 32, 40, 80])
def test_even_unsupported_widths_use_normal_bounded_truncation(width):
    result = render_navigation_bar(bar(), width=width)
    assert display_width(result) <= max(1, width - 1)
    assert "Terminal" not in strip_ansi(result)
    assert result.endswith(DEFAULT_THEME.reset)


def test_hidden_prompt_role_is_still_validated_before_shortening():
    with pytest.raises(ValueError, match="unknown"):
        render_navigation_bar(bar(prompt=text("Long label", "unknown")), width=2)


def test_unknown_types_and_meaningless_query_offsets_fail():
    for kwargs in (
        {"tabs": ()},
        {"marker": "x"},
        {"scroll": "x"},
        {"query_start": True},
        {"query_start": -1},
        {"query_start": 10},
    ):
        with pytest.raises((TypeError, ValueError)):
            bar(**kwargs)
    with pytest.raises(ValueError):
        bar(query=text("x", "query"), query_start=1)


def test_layout_returns_query_scroll_state_without_mutating_input():
    from media_cli.ui.navigation_bar import layout_navigation_bar

    value = bar(query=QueryField("abcdefghij" * 8, 80, True))
    result = layout_navigation_bar(value, width=40)
    assert result.query_start > 0
    assert result.cursor_column is not None and result.cursor_column < 39
    assert value.query_start == 0
    repeated = layout_navigation_bar(replace(value, query_start=result.query_start), width=40)
    assert repeated.query_start == result.query_start


def test_styled_text_fitting_is_a_shared_content_operation():
    from media_cli.ui.styled_text import fit_styled_text

    value = StyledText((TextRun("ab", "prompt"), TextRun("cd", "query")))
    assert fit_styled_text(value, columns=0).runs == ()
    result = fit_styled_text(value, columns=3)
    assert tuple((run.text, run.role) for run in result.runs) == (("ab", "prompt"), ("\u2026", "query"))
    assert fit_styled_text(value, columns=10) is value


@pytest.mark.parametrize("columns", [True, "4", -1])
def test_shared_text_fitting_rejects_invalid_cell_budgets(columns):
    from media_cli.ui.styled_text import fit_styled_text

    with pytest.raises((TypeError, ValueError)):
        fit_styled_text(text("abc"), columns=columns)


def test_tiny_width_does_not_report_an_offscreen_cursor_as_visible():
    from media_cli.ui.navigation_bar import layout_navigation_bar

    result = layout_navigation_bar(bar(), width=2)
    assert result.cursor_column is None


def test_download_manager_uses_shared_width_reference():
    from media_cli.ui import download_manager
    from media_cli.ui.layout import MIN_TUI_COLUMNS

    assert MIN_TUI_COLUMNS == 32
    assert download_manager.MIN_TUI_COLUMNS == MIN_TUI_COLUMNS
