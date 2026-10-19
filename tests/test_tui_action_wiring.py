"""Shared binding resolution must preserve native picker ownership and plain input."""

from types import SimpleNamespace

import pytest

from media_cli.ui.view_model import Blank, DetailLines, Inherit, ViewAction, ViewItem, ViewSpec, ViewTab
from media_cli.ui.view_presentation import ViewPresentation
from media_cli.ui.view_session import ViewSession
from media_cli.ui.view_tui import TuiViewBackend


def make_context(*actions, pending=False, tabs=False):
    spec = ViewSpec(
        "test",
        "Pick",
        (ViewItem("one", "One", details=Inherit),),
        details=DetailLines(Blank, Blank, Blank),
        actions=actions,
        tabs=(ViewTab("one", "One"),) if tabs else (),
        active_tab_id="one" if tabs else None,
    )
    events, tab_changes, focuses = [], [], []
    context = SimpleNamespace(
        view=spec,
        session=SimpleNamespace(
            navigation=object() if pending else None, cancel_navigation=lambda: None, position=SimpleNamespace(query="")
        ),
        emit=lambda *args: events.append(args),
        tab=lambda *args: tab_changes.append(args),
        focus=focuses.append,
    )
    return context, events, tab_changes, focuses


def test_dispatch_and_help_share_page_key_aliases():
    context, events, _, _ = make_context(ViewAction("jump", "Jump", ("PAGEUP",)))
    backend = TuiViewBackend()
    assert "[Page Up] Jump" in backend.action_labels(context.view)
    assert backend.handle_key("PAGE_UP", 2, context)
    assert events == [("action", 2, "jump")]


def test_help_and_footer_deduplicate_equivalent_keys():
    context, _, _, _ = make_context(ViewAction("tab", "switch", ("BTAB", "SHIFT_TAB")), tabs=True)
    assert "[Shift+Tab] switch" in TuiViewBackend().action_labels(context.view)
    presentation = ViewPresentation(ViewSession(context.view), None)
    assert presentation._footer().actions == ("Shift+Tab=switch", "Esc=back")


@pytest.mark.parametrize("key", ("ESC", "LEFT", "esc", "left"))
def test_pending_navigation_uses_shared_names_and_keeps_back_available(key):
    context, events, _, focuses = make_context(pending=True)
    assert TuiViewBackend().handle_key(key, 1, context)
    assert events == [("back", 1, None)]
    assert focuses == []


def test_ctrl_c_cannot_be_rebound_by_a_declared_action():
    context, events, _, _ = make_context(ViewAction("refresh", "Reload", ("CTRL_C",)))
    assert TuiViewBackend().handle_key("CTRL_C", 0, context)
    assert events == [("quit", 0, None)]


@pytest.mark.parametrize("key", ("q", "?", " "))
def test_declared_printable_keys_still_belong_to_filter(key):
    context, events, _, focuses = make_context(
        ViewAction("refresh", "Reload", (key,)) if key != " " else ViewAction("refresh", "Reload")
    )
    assert not TuiViewBackend().handle_key(key, 0, context)
    assert events == focuses == []


@pytest.mark.parametrize("key", ("ENTER", "RIGHT"))
def test_native_selection_keeps_picker_confirmation_behavior(key):
    context, events, _, _ = make_context(ViewAction("select", "open", ("ENTER", "RIGHT")))
    assert not TuiViewBackend().handle_key(key, 0, context)
    assert events == []


def test_info_key_remains_owned_by_picker():
    context, events, _, _ = make_context(ViewAction("info", "Details", ("CTRL_O",)))
    assert not TuiViewBackend().handle_key("CTRL_O", 0, context)
    assert events == []


@pytest.mark.parametrize("key", ("BTAB", "SHIFT_TAB"))
def test_reverse_tab_aliases_keep_direction(key):
    context, events, tabs, _ = make_context(ViewAction("tab", "switch", ("SHIFT_TAB",)), tabs=True)
    assert TuiViewBackend().handle_key(key, 3, context)
    assert tabs == [(3, -1)]
    assert events == []


def test_auxiliary_back_label_is_only_advertised_in_help():
    context, _, _, _ = make_context(
        ViewAction("back", "search", ("LEFT",)),
        ViewAction("back", "back", ("ESC",)),
    )
    presentation = ViewPresentation(ViewSession(context.view), None)
    assert presentation._footer().actions == ("Esc=back",)
    assert "[Left] search" in TuiViewBackend().action_labels(context.view)
