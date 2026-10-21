from dataclasses import replace

import pytest

from media_cli.core.terminal import strip_ansi
from media_cli.ui.footer import Footer, FooterStatus, StatusKind
from media_cli.ui.view_model import Blank, DetailLines, ImageSpec, Inherit, ViewAction, ViewItem, ViewSpec
from media_cli.ui.view_presentation import ViewPresentation
from media_cli.ui.view_session import ReplaceView, ViewSession


class NoImages:
    def enabled(self):
        return False


def test_background_status_overlays_keep_selected_context_actions_and_ready_state():
    source = [FooterStatus("Refreshing this title...", StatusKind.LOADING, "~")]
    view = ViewSpec(
        "menu", "Prompt", (ViewItem("a", "A", details=Inherit, footer_context=("Selected provider",)),),
        details=DetailLines(Blank, Blank, Blank), context=("History",),
        actions=(ViewAction("select", "open", ("ENTER",)),), footer_statuses=source,
    )
    source.clear()
    session = ViewSession(view)
    presentation = ViewPresentation(session, NoImages())
    footer = presentation._footer(item_id="a")
    assert footer.statuses == (FooterStatus("Refreshing this title...", StatusKind.LOADING, "~"),)
    assert footer.context == ("History", "Selected provider") and "Enter=open" in footer.actions
    assert view.state == "ready" and not session.input_blocked
    assert "Refreshing this title..." in presentation.plain_details("a")[-1]
    session.apply(ReplaceView(view.id, session.generation, replace(view, footer_statuses=())))
    assert "Refreshing" not in presentation.details("a")
    assert "Selected provider" in presentation.plain_details("a")[-1]


def test_background_status_overlays_augment_explicit_footer_too():
    status = FooterStatus("Refreshing...", StatusKind.LOADING, "~")
    view = ViewSpec("menu", "Prompt", details=DetailLines(Blank, Blank, Blank),
                    footer=Footer(context=("Custom",)), footer_statuses=(status,))
    presentation = ViewPresentation(ViewSession(view), NoImages())
    assert presentation._footer().statuses == (status,)
    assert presentation.plain_details()[-1] == "Refreshing... • Custom"


def test_background_status_overlay_rejects_untyped_values():
    with pytest.raises(TypeError, match="footer_statuses"):
        ViewSpec("menu", "Prompt", details=DetailLines(Blank, Blank, Blank), footer_statuses=("raw",))


def test_explicit_footer_replaces_navigation_and_restores_without_losing_bindings():
    view = ViewSpec(
        "menu",
        "Prompt",
        (ViewItem("a", "A", details=Inherit),),
        details=DetailLines(Blank, Blank, Blank),
        context=("Provider",),
        actions=(ViewAction("select", "select", ("ENTER",)),),
    )
    session = ViewSession(view)
    presentation = ViewPresentation(session, NoImages())
    footer = Footer(statuses=(FooterStatus("Saved", StatusKind.WARNING),), include_cover_status=False)
    session.apply(ReplaceView(view.id, session.generation, replace(view, footer=footer)))
    assert strip_ansi(presentation.details()).splitlines()[-1] == "Saved"
    assert presentation.plain_details()[-1] == "Saved"
    assert session.current.actions == view.actions
    session.apply(ReplaceView(view.id, session.generation, view))
    assert "Provider" in strip_ansi(presentation.details()).splitlines()[-1]
    assert "Enter=select" in strip_ansi(presentation.details()).splitlines()[-1]


def test_invalid_footer_is_rejected_at_view_boundary():
    with pytest.raises(TypeError, match="footer"):
        ViewSpec("menu", "Prompt", details=DetailLines(Blank, Blank, Blank), footer="untyped")


@pytest.mark.parametrize("state", ["unavailable", "failed", "resolving"])
def test_explicit_feedback_can_suppress_cover_diagnostics(state):
    class ImagesEnabled:
        def enabled(self):
            return True

    view = ViewSpec(
        "menu",
        "Prompt",
        (ViewItem("a", "A", details=Inherit),),
        details=DetailLines(Blank, Blank, Blank),
        image=ImageSpec("cover", state),
        footer=Footer(statuses=(FooterStatus("Saved", StatusKind.WARNING),), include_cover_status=False),
    )
    presentation = ViewPresentation(ViewSession(view), ImagesEnabled())
    assert strip_ansi(presentation.details()).splitlines()[-1] == "Saved"
