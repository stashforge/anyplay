"""Counter modes are supplied explicitly rather than inferred from two numbers."""

import importlib
from dataclasses import FrozenInstanceError, replace

import pytest

from media_cli.core.terminal import display_width, strip_ansi
from media_cli.ui.styled_text import render_styled_text
from media_cli.ui.theme import DEFAULT_THEME, TextStyle


def component():
    return importlib.import_module("media_cli.ui.navigation_counter")


def test_component_exists():
    assert importlib.util.find_spec("media_cli.ui.navigation_counter") is not None


@pytest.mark.parametrize(
    "mode,value,total,expected,role",
    [
        ("FILTERED", 3, 10, "3/10", "counter"),
        ("FILTERED", 0, 0, "0/0", "counter"),
        ("POSITION", 3, 10, "3/10", "counter"),
        ("POSITION", 0, 0, "0/0", "counter"),
        ("SELECTED", 2, None, "2 selected", "selected_count"),
        ("SELECTED", 0, None, "0 selected", "selected_count"),
    ],
)
def test_explicit_modes_produce_semantic_text(mode, value, total, expected, role):
    module = component()
    counter = module.NavigationCounter(getattr(module.CounterMode, mode), value, total)
    content = counter.content()
    assert len(content.runs) == 1
    assert (content.runs[0].text, content.runs[0].role) == (expected, role)
    assert strip_ansi(render_styled_text(content, width=80)) == expected


def test_supplied_counts_are_immutable_not_derived_from_results():
    module = component()
    counter = module.NavigationCounter(module.CounterMode.FILTERED, 2, 100)
    assert counter.total == 100
    with pytest.raises(FrozenInstanceError):
        counter.value = 8


@pytest.mark.parametrize(
    "mode,value,total",
    [
        ("FILTERED", 11, 10),
        ("FILTERED", 1, None),
        ("FILTERED", -1, 10),
        ("POSITION", 0, 10),
        ("POSITION", 1, 0),
        ("POSITION", 11, 10),
        ("SELECTED", 1, 10),
        ("SELECTED", -1, None),
        ("FILTERED", True, 10),
        ("FILTERED", 1, True),
        ("FILTERED", "1", 10),
        ("FILTERED", 1, -1),
    ],
)
def test_impossible_counts_and_wrong_types_fail_at_construction(mode, value, total):
    module = component()
    with pytest.raises((TypeError, ValueError)):
        module.NavigationCounter(getattr(module.CounterMode, mode), value, total)


def test_mode_is_required_and_cannot_be_a_guessed_string():
    module = component()
    with pytest.raises(TypeError):
        module.NavigationCounter(value=1, total=10)
    with pytest.raises(TypeError, match="CounterMode"):
        module.NavigationCounter("filtered", 1, 10)


def test_counter_styles_are_owned_by_the_theme_and_keep_existing_defaults():
    module = component()
    filtered = module.NavigationCounter(module.CounterMode.FILTERED, 3, 10).content()
    selected = module.NavigationCounter(module.CounterMode.SELECTED, 2).content()
    assert "\033[38;5;244m3/10" in render_styled_text(filtered, width=80)
    assert "\033[38;2;166;227;161m2 selected" in render_styled_text(selected, width=80)
    custom = replace(DEFAULT_THEME, counter=TextStyle(81), selected_count=TextStyle(82), background=17)
    assert "\033[38;5;81;48;5;17m3/10" in render_styled_text(filtered, width=80, theme=custom)
    assert "\033[38;5;82;48;5;17m2 selected" in render_styled_text(selected, width=80, theme=custom)


@pytest.mark.parametrize("width", [1, 2, 4, 20])
def test_counter_content_uses_shared_width_and_reset_rules(width):
    module = component()
    counter = module.NavigationCounter(module.CounterMode.SELECTED, 12)
    rendered = render_styled_text(counter.content(), width=width)
    assert display_width(rendered) <= max(1, width - 1)
    assert rendered.endswith(DEFAULT_THEME.reset)
