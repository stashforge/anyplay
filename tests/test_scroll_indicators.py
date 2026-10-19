"""Scroll hints describe visual hidden rows without guessing list order."""

import importlib
from dataclasses import FrozenInstanceError, replace

import pytest

from media_cli.core.terminal import display_width, strip_ansi
from media_cli.ui.styled_text import render_styled_text
from media_cli.ui.theme import DEFAULT_THEME, TextStyle


def indicators(above, below):
    module = importlib.import_module("media_cli.ui.scroll_indicators")
    return module.ScrollIndicators(hidden_above=above, hidden_below=below)


@pytest.mark.parametrize(
    "above,below,expected",
    [
        (0, 0, ""),
        (3, 0, "\u25b23"),
        (0, 4, "\u25bc4"),
        (3, 4, "\u25b23 \u25bc4"),
    ],
)
def test_only_nonzero_supplied_counts_are_shown(above, below, expected):
    content = indicators(above, below).content()
    assert "".join(run.text for run in content.runs) == expected
    assert strip_ansi(render_styled_text(content, width=80)) == expected
    if not expected:
        assert content.runs == ()


def test_counts_and_separator_have_semantic_roles():
    content = indicators(2, 5).content()
    assert tuple((run.text, run.role) for run in content.runs) == (
        ("\u25b22", "scroll_indicator"),
        (" ", "separator"),
        ("\u25bc5", "scroll_indicator"),
    )


def test_default_style_matches_existing_picker_and_custom_theme_owns_colors():
    content = indicators(2, 1).content()
    default = render_styled_text(content, width=30)
    assert "\033[38;2;243;139;168m\u25b22" in default
    theme = replace(DEFAULT_THEME, scroll_indicator=TextStyle(81, bold=True), background=17)
    result = render_styled_text(content, width=30, theme=theme)
    assert theme.prefix(theme.scroll_indicator) + "\u25b22" in result
    assert theme.prefix(theme.scroll_indicator) + "\u25bc1" in result
    assert result.endswith(theme.reset)


@pytest.mark.parametrize("width", [1, 2, 4, 12, 80])
def test_shared_renderer_bounds_content_and_restores_style(width):
    result = render_styled_text(indicators(123, 456).content(), width=width)
    assert display_width(result) <= max(1, width - 1)
    assert result.endswith(DEFAULT_THEME.reset)


@pytest.mark.parametrize("field", ["hidden_above", "hidden_below"])
@pytest.mark.parametrize(
    "value,error", [(-1, ValueError), (True, TypeError), (None, TypeError), ("3", TypeError), (1.5, TypeError)]
)
def test_invalid_counts_fail_at_construction(field, value, error):
    module = importlib.import_module("media_cli.ui.scroll_indicators")
    kwargs = dict(hidden_above=0, hidden_below=0)
    kwargs[field] = value
    with pytest.raises(error, match=field):
        module.ScrollIndicators(**kwargs)


def test_both_counts_are_explicit_and_immutable():
    module = importlib.import_module("media_cli.ui.scroll_indicators")
    with pytest.raises(TypeError):
        module.ScrollIndicators(hidden_above=1)
    value = indicators(1, 2)
    with pytest.raises(FrozenInstanceError):
        value.hidden_above = 5
