"""Typed details retain semantic roles through TUI and plain presentation."""

from dataclasses import replace

import pytest

from media_cli.state.theme import APPLICATION_THEME
from media_cli.ui import view_model as vm
from media_cli.ui.styled_text import StyledText, TextRun
from media_cli.ui.theme import TextStyle
from media_cli.ui.view_presentation import ViewPresentation
from media_cli.ui.view_session import ViewSession


def metadata():
    return StyledText(
        (
            TextRun("FINISHED", "metadata.status"),
            TextRun(" • ", "metadata.separator"),
            TextRun("Watched", "muted"),
            TextRun(" 3/12", "details"),
        )
    )


def test_role_content_resolves_without_flattening_inherit_or_blank():
    common = vm.DetailLines(vm.Content("Title"), vm.Blank, metadata())
    assert vm.resolve_details(common).metadata == metadata()
    assert vm.resolve_details(common, vm.DetailLines(vm.Inherit, vm.Blank, vm.Inherit)).metadata == metadata()
    assert vm.resolve_details(common, vm.Blank).metadata == ""


def test_role_content_renders_from_supplied_theme_and_plain_mode_has_no_colors():
    class NoImages:
        def enabled(self):
            return False

    view = vm.ViewSpec("results", "Search", details=vm.DetailLines(vm.Content("Title"), vm.Blank, metadata()))
    session = ViewSession(view)
    theme = replace(
        APPLICATION_THEME,
        extensions=tuple(
            (name, TextStyle(123) if name == "metadata.status" else style)
            for name, style in APPLICATION_THEME.extensions
        ),
    )
    presenter = ViewPresentation(session, NoImages(), theme=theme)
    assert "\033[38;5;123mFINISHED" in presenter.details(width=120)
    assert presenter.plain_details()[:3] == ["Title", "", "FINISHED • Watched 3/12"]


def test_unknown_theme_role_is_not_silently_discarded():
    class NoImages:
        def enabled(self):
            return False

    view = vm.ViewSpec(
        "results",
        "Search",
        details=vm.DetailLines(vm.Blank, vm.Blank, StyledText((TextRun("Wrong", "undeclared.role"),))),
    )
    with pytest.raises(ValueError, match="Unknown theme role"):
        ViewPresentation(ViewSession(view), NoImages()).details()
