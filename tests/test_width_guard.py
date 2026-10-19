"""Width notices use shared dimensions, bounded text and semantic theme roles."""

from dataclasses import replace

import pytest

from media_cli.core.terminal import display_width, strip_ansi
from media_cli.ui.theme import DEFAULT_THEME, TextStyle
from media_cli.ui.width_guard import render_width_notice


@pytest.mark.parametrize("columns", [1, 8, 20, 31])
def test_notice_is_bounded_even_when_the_hint_cannot_fit(columns):
    lines = render_width_notice(columns=columns)
    assert len(lines) == 2
    assert all(display_width(line) <= max(1, columns - 1) for line in lines)
    assert all(line.endswith(DEFAULT_THEME.reset) for line in lines)


def test_notice_uses_shared_minimum_and_theme_owned_styles():
    theme = replace(DEFAULT_THEME, warning=TextStyle(81), actions=TextStyle(82), background=17)
    title, actions = render_width_notice(columns=31, theme=theme)
    assert strip_ansi(title) == "Resize to 32 columns"
    assert "Esc: back" in strip_ansi(actions)
    assert theme.prefix(theme.warning) in title
    assert theme.prefix(theme.actions) in actions
    assert title.endswith(theme.reset) and actions.endswith(theme.reset)


@pytest.mark.parametrize("columns", [0, -1, True, "31"])
def test_invalid_notice_widths_fail(columns):
    with pytest.raises((TypeError, ValueError)):
        render_width_notice(columns=columns)
