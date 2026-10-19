"""Theme values and segment styling without wiring a live screen."""

import importlib
import re
from dataclasses import FrozenInstanceError, replace

import pytest

from media_cli.core.terminal import display_width, strip_ansi
from media_cli.ui.detail_panel import render_detail_panel
from media_cli.ui.footer import Footer, FooterStatus, StatusKind, render_footer


def theme_module():
    return importlib.import_module("media_cli.ui.theme")


def panel(theme, width=120):
    return render_detail_panel(
        primary="Primary",
        secondary="Secondary",
        details="Details",
        footer=Footer(
            statuses=(FooterStatus("Loading", StatusKind.LOADING, "*"),), context=("Context",), actions=("Enter=open",)
        ),
        width=width,
        theme=theme,
    )


def test_theme_component_exists():
    assert importlib.util.find_spec("media_cli.ui.theme") is not None


def test_default_theme_preserves_existing_output_exactly():
    theme = theme_module().DEFAULT_THEME
    footer = Footer(
        statuses=(FooterStatus("Loading", StatusKind.LOADING, "*"),), context=("Context",), actions=("Enter=open",)
    )
    assert panel(theme) == (
        "\033[1;97mPrimary\033[0m",
        "\033[38;5;248mSecondary\033[0m",
        "\033[38;5;248mDetails\033[0m",
        "\033[38;5;244m\033[36m* Loading\033[0m"
        "\033[38;5;244m  \u2502  \033[0m\033[38;5;244mContext\033[0m"
        "\033[38;5;244m \u2022 \033[0m\033[38;5;244mEnter=open\033[0m\033[0m",
    )
    assert render_footer(footer, 120, theme=theme) == render_footer(footer, 120)


def test_styles_are_immutable_and_optional_background_defaults_to_terminal():
    module = theme_module()
    assert module.DEFAULT_THEME.background is None
    with pytest.raises(FrozenInstanceError):
        module.DEFAULT_THEME.background = 17
    with pytest.raises(FrozenInstanceError):
        module.DEFAULT_THEME.primary.bold = False


def test_each_segment_uses_its_role_and_resets_restore_canvas_background():
    module = theme_module()
    theme = replace(
        module.DEFAULT_THEME,
        background=(12, 24, 36),
        primary=module.TextStyle(foreground=81, background=22, bold=True),
        secondary=module.TextStyle(foreground=82, italic=True),
        details=module.TextStyle(foreground=83, underline=True),
        loading=module.TextStyle(foreground=84),
        context=module.TextStyle(foreground=85),
        actions=module.TextStyle(foreground=86),
        separator=module.TextStyle(foreground=87),
    )
    lines = panel(theme)
    assert "\033[1;38;5;81;48;5;22mPrimary" in lines[0]
    assert "\033[3;38;5;82;48;2;12;24;36mSecondary" in lines[1]
    assert "\033[4;38;5;83;48;2;12;24;36mDetails" in lines[2]
    assert "\033[38;5;84;48;2;12;24;36m* Loading" in lines[3]
    assert "\033[38;5;85;48;2;12;24;36mContext" in lines[3]
    assert "\033[38;5;86;48;2;12;24;36mEnter=open" in lines[3]
    assert "\033[38;5;87;48;2;12;24;36m \u2022 " in lines[3]
    assert all(line.endswith("\033[0m\033[48;2;12;24;36m") for line in lines)
    # Every attribute reset restores the active canvas, not the terminal default.
    assert all(not re.search(r"\x1b\[0m(?!\x1b\[48;2;12;24;36m)", line) for line in lines)


@pytest.mark.parametrize("kind", list(StatusKind))
def test_all_status_roles_are_independent_from_context_and_actions(kind):
    module = theme_module()
    theme = replace(module.DEFAULT_THEME, **{kind.value: module.TextStyle(foreground=(1, 2, 3))})
    footer = Footer(statuses=(FooterStatus("Status", kind),), context=("Context",), actions=("Action",))
    result = render_footer(footer, 120, theme=theme)
    assert "\033[38;2;1;2;3mStatus\033[0m" in result
    assert "\033[38;5;244mContext\033[0m" in result
    assert "\033[38;5;244mAction\033[0m" in result


@pytest.mark.parametrize("width", [1, 2, 8, 80])
def test_theme_does_not_change_content_width_or_background_padding(width):
    module = theme_module()
    default = panel(module.DEFAULT_THEME, width)
    themed = panel(replace(module.DEFAULT_THEME, background=17), width)
    assert tuple(map(strip_ansi, default)) == tuple(map(strip_ansi, themed))
    assert all(display_width(line) <= max(1, width - 1) for line in themed)


@pytest.mark.parametrize("color", [True, -1, 256, "\033[31m", "red", (1, 2), (1, 2, 300), [1, 2, 3]])
def test_invalid_colors_are_rejected_at_construction(color):
    module = theme_module()
    with pytest.raises((TypeError, ValueError)):
        module.TextStyle(foreground=color)
    with pytest.raises((TypeError, ValueError)):
        replace(module.DEFAULT_THEME, background=color)


def test_invalid_attributes_roles_and_renderer_themes_fail_early():
    module = theme_module()
    with pytest.raises(TypeError, match="bold"):
        module.TextStyle(bold="yes")
    with pytest.raises(TypeError, match="primary"):
        replace(module.DEFAULT_THEME, primary="red")
    with pytest.raises(TypeError, match="theme"):
        panel(None)
    with pytest.raises(TypeError, match="theme"):
        render_footer(Footer(), 80, theme=None)
