"""Row adjustments share picker boundaries and dispatch by stable identity."""

from dataclasses import replace

import pytest
from test_ui_view_picker import ScriptedPicker, run
from test_ui_view_picker import environment as environment

from media_cli.ui.view_model import Blank, DetailLines, ViewAction, ViewItem, ViewSpec
from media_cli.ui.view_session import BeginNavigation, ExitSession, UiEvent, ViewSession


def view():
    return ViewSpec(
        "settings", "Settings",
        (ViewItem("buffer", "Buffer", details=Blank, adjustable=True),
         ViewItem("submenu", "More", kind="submenu", details=Blank)),
        details=DetailLines(Blank, Blank, Blank),
        actions=(ViewAction("increase", "Increase", footer=False),
                 ViewAction("decrease", "Decrease", footer=False)),
    )


def test_adjustment_requires_explicit_boolean_and_selectable_row():
    with pytest.raises(TypeError, match="adjustable"):
        ViewItem("row", "Row", details=Blank, adjustable=1)
    for options in ({"enabled": False}, {"kind": "heading"}):
        with pytest.raises(ValueError, match="selectable"):
            ViewItem("row", "Row", details=Blank, adjustable=True, **options)


def test_adjustable_view_requires_both_declared_actions():
    with pytest.raises(ValueError, match="increase.*decrease"):
        replace(view(), actions=())


@pytest.mark.parametrize("direction,action", [(-1, "decrease"), (1, "increase")])
def test_adjustment_dispatches_after_picker_releases_input(environment, direction, action):
    def adjust(options, hooks):
        assert not hooks["adjust_fn"](1, direction)
        assert hooks["adjust_fn"](0, direction)
        return -6

    picker = ScriptedPicker(adjust)

    def handle(event):
        assert not picker.active
        assert (event.kind, event.item_id, event.value) == ("action", "buffer", action)
        return ExitSession("adjusted")

    assert run(environment, ViewSession(view(), handle), picker) == "adjusted"


@pytest.mark.parametrize("item_id", [None, "missing", "submenu"])
def test_direct_adjustment_rejects_nonadjustable_target(item_id):
    events = []
    session = ViewSession(view(), events.append)
    assert not session.dispatch(UiEvent("action", "settings", session.generation, item_id, "increase"))
    assert events == []


def test_loading_view_rejects_adjustment():
    events = []
    session = ViewSession(replace(view(), state="loading"), events.append)
    assert not session.dispatch(UiEvent("action", "settings", session.generation, "buffer", "decrease"))
    assert events == []


def test_loading_rejects_adjustment_without_cancelling_navigation(environment):
    session = ViewSession(view())
    session.apply(BeginNavigation("settings", session.generation, "request", "Loading"))
    assert not session.dispatch(UiEvent("action", "settings", session.generation, "buffer", "increase"))

    def blocked(options, hooks):
        assert not hooks["adjust_fn"](0, 1)
        assert session.navigation is not None
        return -5

    run(environment, session, ScriptedPicker(blocked))
