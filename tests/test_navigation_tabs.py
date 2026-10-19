"""Tab presentation uses explicit identity and theme-owned styling."""

import importlib
from dataclasses import FrozenInstanceError, replace

import pytest

from media_cli.core.terminal import display_width, strip_ansi
from media_cli.ui.styled_text import render_styled_text
from media_cli.ui.theme import DEFAULT_THEME, TextStyle
from media_cli.ui.view_model import ViewTab


def tabs(items, active_id):
    module = importlib.import_module("media_cli.ui.navigation_tabs")
    return module.NavigationTabs(items=items, active_id=active_id)


def test_roles_follow_ids_not_labels_or_position():
    value = tabs((ViewTab("one", "Same"), ViewTab("two", "Same")), "two")
    assert tuple((run.text, run.role) for run in value.content().runs) == (
        ("Same", "tab_inactive"),
        (" \u00b7 ", "separator"),
        ("Same", "tab_active"),
    )
    assert value.active.label == "Same"
    assert value.active.id == "two"


def test_custom_theme_controls_each_tab_and_restores_background():
    value = tabs((ViewTab("a", "First"), ViewTab("b", "Second")), "b")
    theme = replace(DEFAULT_THEME, tab_active=TextStyle(81, bold=True), tab_inactive=TextStyle(82), background=17)
    result = render_styled_text(value.content(), width=80, theme=theme)
    assert strip_ansi(result) == "First \u00b7 Second"
    assert theme.prefix(theme.tab_inactive) + "First" in result
    assert theme.prefix(theme.tab_active) + "Second" in result
    assert result.endswith(theme.reset)


def test_empty_tabs_require_explicit_no_active_tab():
    value = tabs((), None)
    assert value.content().runs == ()
    assert value.active is None


@pytest.mark.parametrize("width", [1, 2, 5, 10, 40])
def test_shared_renderer_bounds_wide_labels(width):
    value = tabs((ViewTab("a", "\u754ce\u0301"), ViewTab("b", "Second")), "a")
    result = render_styled_text(value.content(), width=width)
    assert display_width(result) <= max(1, width - 1)
    assert result.endswith(DEFAULT_THEME.reset)


@pytest.mark.parametrize(
    "items, active_id, error",
    [
        ([], None, TypeError),
        (("wrong",), None, TypeError),
        ((ViewTab("a", "A"),), 1, TypeError),
        ((ViewTab("a", "A"),), None, ValueError),
        ((), "missing", ValueError),
        ((ViewTab("a", "A"),), "missing", ValueError),
        ((ViewTab("a", "A"), ViewTab("a", "B")), "a", ValueError),
        ((ViewTab("a", "   "),), "a", ValueError),
        ((ViewTab("a", "\033[31mA"),), "a", ValueError),
    ],
)
def test_invalid_or_ambiguous_tab_data_fails_at_construction(items, active_id, error):
    with pytest.raises(error):
        tabs(items, active_id)


def test_tab_state_is_immutable():
    value = tabs((ViewTab("a", "A"),), "a")
    with pytest.raises(FrozenInstanceError):
        value.active_id = "b"
