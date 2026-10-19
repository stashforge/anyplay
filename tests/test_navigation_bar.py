"""Compose supplied navigation pieces without application or input logic."""

import importlib
from dataclasses import FrozenInstanceError, replace

import pytest

from media_cli.core.terminal import display_width, strip_ansi
from media_cli.ui.navigation_counter import CounterMode, NavigationCounter
from media_cli.ui.styled_text import StyledText, TextRun
from media_cli.ui.theme import DEFAULT_THEME, TextStyle


def component():
    return importlib.import_module("media_cli.ui.navigation_bar")


def text(value, role):
    return StyledText((TextRun(value, role),))


def test_component_exists():
    assert importlib.util.find_spec("media_cli.ui.navigation_bar") is not None


def test_prompt_query_and_multiple_counters_keep_their_own_roles():
    module = component()
    bar = module.NavigationBar(
        prompt=text("Select song \u276f", "prompt"),
        query=text("idol", "query"),
        counters=(NavigationCounter(CounterMode.SELECTED, 2), NavigationCounter(CounterMode.FILTERED, 3, 10)),
        rule=False,
    )
    result = module.render_navigation_bar(bar, width=80)
    assert strip_ansi(result) == "Select song \u276f idol  2 selected | 3/10"
    assert DEFAULT_THEME.prefix(DEFAULT_THEME.prompt) + "Select song" in result
    assert DEFAULT_THEME.prefix(DEFAULT_THEME.query) + "idol" in result
    assert DEFAULT_THEME.prefix(DEFAULT_THEME.selected_count) + "2 selected" in result
    assert DEFAULT_THEME.prefix(DEFAULT_THEME.counter) + "3/10" in result


def test_rule_fills_only_remaining_display_columns():
    module = component()
    bar = module.NavigationBar(prompt=text("\u754ce\u0301", "prompt"))
    result = module.render_navigation_bar(bar, width=12)
    assert strip_ansi(result) == "\u754ce\u0301 " + "\u2500" * 7
    assert display_width(result) == 11


@pytest.mark.parametrize("width", [1, 2, 4, 8, 20])
def test_rule_yields_to_content_and_shared_truncation_handles_narrow_widths(width):
    module = component()
    prompt = text("Long prompt", "prompt")
    with_rule = module.render_navigation_bar(module.NavigationBar(prompt=prompt), width=width)
    without_rule = module.render_navigation_bar(module.NavigationBar(prompt=prompt, rule=False), width=width)
    assert display_width(with_rule) <= max(1, width - 1)
    if width <= 12:
        assert strip_ansi(with_rule) == strip_ansi(without_rule)
    assert with_rule.endswith(DEFAULT_THEME.reset)


def test_optional_query_and_counters_do_not_add_empty_separators():
    module = component()
    bar = module.NavigationBar(
        prompt=StyledText(()),
        query=StyledText(()),
        counters=(NavigationCounter(CounterMode.FILTERED, 0, 0),),
        rule=False,
    )
    assert strip_ansi(module.render_navigation_bar(bar, width=80)) == "0/0"
    empty = module.NavigationBar(prompt=StyledText(()))
    assert strip_ansi(module.render_navigation_bar(empty, width=5)) == "\u2500" * 4


def test_custom_theme_applies_to_segments_and_restores_background():
    module = component()
    theme = replace(DEFAULT_THEME, prompt=TextStyle(81), query=TextStyle(82), rule=TextStyle(83), background=17)
    bar = module.NavigationBar(prompt=text("Prompt", "prompt"), query=text("Query", "query"))
    result = module.render_navigation_bar(bar, width=30, theme=theme)
    assert "\033[38;5;81;48;5;17mPrompt" in result
    assert "\033[38;5;82;48;5;17mQuery" in result
    assert "\033[38;5;83;48;5;17m\u2500" in result
    assert result.endswith(theme.reset)


def test_unknown_roles_are_not_hidden_by_narrow_clipping():
    module = component()
    bar = module.NavigationBar(prompt=text("Long prompt", "prompt"), query=text("Query", "unknown"))
    with pytest.raises(ValueError, match="unknown"):
        module.render_navigation_bar(bar, width=2)


def test_values_are_immutable_and_reject_unstructured_pieces():
    module = component()
    bar = module.NavigationBar(prompt=text("Prompt", "prompt"))
    with pytest.raises(FrozenInstanceError):
        bar.rule = False
    for kwargs in ({"prompt": "Prompt"}, {"query": "Query"}, {"counters": []}, {"counters": ("1/2",)}, {"rule": "yes"}):
        fields = dict(prompt=text("Prompt", "prompt"))
        fields.update(kwargs)
        with pytest.raises(TypeError):
            module.NavigationBar(**fields)


@pytest.mark.parametrize("width", [None, True, "80", 0, -1])
def test_invalid_width_is_rejected(width):
    module = component()
    with pytest.raises((TypeError, ValueError)):
        module.render_navigation_bar(module.NavigationBar(prompt=StyledText(())), width=width)


def test_wrong_bar_and_theme_types_are_rejected():
    module = component()
    with pytest.raises(TypeError, match="NavigationBar"):
        module.render_navigation_bar("Prompt", width=80)
    with pytest.raises(TypeError, match="theme"):
        module.render_navigation_bar(module.NavigationBar(prompt=StyledText(())), width=80, theme=None)
