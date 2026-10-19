"""Structured text carries semantic roles, never terminal styles."""

import importlib
from dataclasses import FrozenInstanceError, replace

import pytest

from media_cli.core.terminal import display_width, strip_ansi, truncate_display
from media_cli.ui.theme import DEFAULT_THEME, TextStyle


def component():
    return importlib.import_module("media_cli.ui.styled_text")


def test_component_exists():
    assert importlib.util.find_spec("media_cli.ui.styled_text") is not None


def test_roles_choose_theme_styles_without_changing_content():
    module = component()
    text = module.StyledText((module.TextRun("Watched", "muted"), module.TextRun(" 4/12", "details")))
    theme = replace(DEFAULT_THEME, muted=TextStyle(81), details=TextStyle(82), background=17)
    result = module.render_styled_text(text, width=80, theme=theme)
    assert strip_ansi(result) == "Watched 4/12"
    assert "\033[38;5;81;48;5;17mWatched" in result
    assert "\033[38;5;82;48;5;17m 4/12" in result
    assert result.endswith(theme.reset)


def test_application_roles_are_resolved_only_from_theme_extensions():
    module = component()
    text = module.StyledText((module.TextRun("AL WATCHING", "anilist.watching"),))
    with pytest.raises(ValueError, match="anilist.watching"):
        module.render_styled_text(text, width=80)
    green = replace(DEFAULT_THEME, extensions=(("anilist.watching", TextStyle(82)),))
    blue = replace(green, extensions=(("anilist.watching", TextStyle(81)),))
    assert "\033[38;5;82mAL WATCHING" in module.render_styled_text(text, width=80, theme=green)
    assert "\033[38;5;81mAL WATCHING" in module.render_styled_text(text, width=80, theme=blue)


@pytest.mark.parametrize("width", [1, 2, 3, 5, 8, 80])
def test_truncation_measures_the_whole_line_across_run_boundaries(width):
    module = component()
    runs = (
        module.TextRun("A\u754c", "primary"),
        module.TextRun("e\u0301", "secondary"),
        module.TextRun(" rest", "details"),
    )
    result = module.render_styled_text(module.StyledText(runs), width=width)
    assert strip_ansi(result) == truncate_display("A\u754ce\u0301 rest", max(1, width - 1))
    assert display_width(result) <= max(1, width - 1)


def test_ellipsis_uses_the_role_of_the_first_omitted_character():
    module = component()
    text = module.StyledText((module.TextRun("AB", "primary"), module.TextRun("CDEF", "error")))
    result = module.render_styled_text(text, width=4)
    assert strip_ansi(result) == "AB\u2026"
    assert DEFAULT_THEME.prefix(DEFAULT_THEME.error) + "\u2026" in result


def test_unknown_roles_fail_even_when_their_content_is_truncated_away():
    module = component()
    text = module.StyledText((module.TextRun("A long line", "primary"), module.TextRun("x", "typo")))
    with pytest.raises(ValueError, match="typo"):
        module.render_styled_text(text, width=2)


def test_runs_and_collection_are_immutable_and_empty_content_is_explicit():
    module = component()
    run = module.TextRun("Text", "primary")
    text = module.StyledText((run,))
    with pytest.raises(FrozenInstanceError):
        run.role = "error"
    with pytest.raises(FrozenInstanceError):
        text.runs = ()
    assert strip_ansi(module.render_styled_text(module.StyledText(()), width=80)) == ""
    with pytest.raises(TypeError):
        module.StyledText()
    with pytest.raises(TypeError):
        module.TextRun("Text", color="red")


@pytest.mark.parametrize("text", ["\033[31mRed", "A\nB", "A\tB", "\x9b31mRed", None, 42])
def test_raw_terminal_controls_and_wrong_text_types_are_rejected(text):
    with pytest.raises((TypeError, ValueError)):
        component().TextRun(text, "primary")


@pytest.mark.parametrize("role", ["", " primary", "\033[31m", "#ff0000", None, TextStyle(81)])
def test_role_must_be_a_valid_name_not_a_style_or_color(role):
    with pytest.raises((TypeError, ValueError)):
        component().TextRun("Text", role)


@pytest.mark.parametrize("runs", [[("Text", "primary")], "Text", ("Text",), (TextStyle(81),)])
def test_run_collection_rejects_mutable_or_unstructured_values(runs):
    with pytest.raises(TypeError):
        component().StyledText(runs)


@pytest.mark.parametrize(
    "extensions",
    [
        (("primary", TextStyle(81)),),
        (("custom", TextStyle(81)), ("custom", TextStyle(82))),
        (("custom", "red"),),
        [("custom", TextStyle(81))],
    ],
)
def test_theme_rejects_invalid_or_ambiguous_role_extensions(extensions):
    with pytest.raises((TypeError, ValueError)):
        replace(DEFAULT_THEME, extensions=extensions)


@pytest.mark.parametrize("width", [None, True, "80", 0, -1])
def test_invalid_widths_fail_instead_of_guessing(width):
    module = component()
    with pytest.raises((TypeError, ValueError)):
        module.render_styled_text(module.StyledText(()), width=width)


def test_invalid_theme_and_content_types_are_rejected():
    module = component()
    with pytest.raises(TypeError, match="theme"):
        module.render_styled_text(module.StyledText(()), width=80, theme=None)
    with pytest.raises(TypeError, match="StyledText"):
        module.render_styled_text("raw text", width=80)
