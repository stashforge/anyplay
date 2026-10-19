
import pytest
from test_ui_view_presentation import setup

from media_cli.core.terminal import strip_ansi
from media_cli.ui.loading_model import UpdateLoading
from media_cli.ui.tui_actions import tui_bindings
from media_cli.ui.view_session import BeginNavigation, FailNavigation


def pending_feedback(monkeypatch):
    clock = [10.0]
    presentation, session, _ = setup(clock=lambda: clock[0])
    session.apply(BeginNavigation("root", session.generation, "request", "Fetching original"))
    presentation.blocked_action(tui_bindings(session.current, navigation_pending=True).resolve("ENTER"))
    return presentation, session, clock


def test_feedback_replaces_only_loading_text_then_restores_latest_message(monkeypatch):
    presentation, session, clock = pending_feedback(monkeypatch)
    active = strip_ansi(presentation.details(width=160))
    assert "Hang on, still loading." in active
    assert "Fetching original" not in active
    assert "~" in active
    assert presentation.needs_tick()
    assert "Fetching original" in presentation.plain_details()[-1]
    session.loading.apply(UpdateLoading(session.navigation_token, "Fetching newest"))
    clock[0] = 11.0
    restored = strip_ansi(presentation.details(width=160))
    assert "Fetching newest" in restored and "Hang on" not in restored
    assert presentation.needs_tick()


@pytest.mark.parametrize("finish", ("cancel", "fail", "new_request"))
def test_feedback_does_not_survive_cancel_failure_or_request_replacement(monkeypatch, finish):
    presentation, session, _ = pending_feedback(monkeypatch)
    if finish == "cancel":
        session.cancel_navigation()
    elif finish == "fail":
        session.apply(FailNavigation("root", session.generation, "request", "FETCH-FAILED"))
    else:
        session.cancel_navigation()
        session.apply(BeginNavigation("root", session.generation, "next", "Fetching another"))
    text = strip_ansi(presentation.details(width=160))
    assert "Hang on" not in text
    if finish == "fail":
        assert "FETCH-FAILED" in text
    elif finish == "new_request":
        assert "Fetching another" in text
