from types import SimpleNamespace

import pytest

from media_cli.ui.actions import ActionAvailability
from media_cli.ui.tui_actions import tui_bindings
from media_cli.ui.view_model import (
    Blank,
    DetailLines,
    Inherit,
    SelectionPolicy,
    ViewAction,
    ViewItem,
    ViewSpec,
    ViewTab,
)
from media_cli.ui.view_presentation import ViewPresentation
from media_cli.ui.view_session import ViewSession
from media_cli.ui.view_tui import TuiViewBackend


def view(*actions, items=(ViewItem("one", "One", details=Inherit),), **kwargs):
    return ViewSpec("test", "Pick", items, details=DetailLines(Blank, Blank, Blank), actions=actions, **kwargs)


def test_defaults_fill_each_unclaimed_key_not_whole_groups():
    spec = view(ViewAction("refresh", "Reload", ("LEFT", "ENTER")))
    bindings = tui_bindings(spec)
    assert bindings.resolve("LEFT").action.id == "refresh"
    assert bindings.resolve("ESC").action.id == "back"
    assert bindings.resolve("ENTER").action.id == "refresh"
    assert bindings.resolve("RIGHT").action.id == "select"
    hints = TuiViewBackend().action_labels(spec)
    assert "[Esc] Back" in hints
    assert "[Right] Select" in hints


def test_protected_quit_is_not_advertised_as_custom_action():
    spec = view(ViewAction("refresh", "Reload", ("CTRL_C", "CTRL_R")))
    assert tui_bindings(spec).resolve("CTRL_C").action.id == "quit"
    hints = TuiViewBackend().action_labels(spec)
    assert "[Ctrl+R] Reload" in hints
    assert "[Ctrl+C] Quit" in hints
    assert not any("Ctrl+C" in label and "Reload" in label for label in hints)


def test_pending_navigation_blocks_declared_actions_and_preserves_cancel_and_quit():
    spec = view(ViewAction("refresh", "Reload", ("CTRL_R", "ESC")))
    bindings = tui_bindings(spec, navigation_pending=True)
    assert bindings.resolve("CTRL_R").availability is ActionAvailability.BLOCKED
    assert bindings.resolve("CTRL_R").blocked_message
    for key, action_id in (("ESC", "back"), ("LEFT", "back"), ("CTRL_C", "quit")):
        assert bindings.resolve(key).availability is ActionAvailability.AVAILABLE
        assert bindings.resolve(key).action.id == action_id
    hints = TuiViewBackend().action_labels(spec, navigation_pending=True)
    assert not any("Reload" in hint or "Select" in hint for hint in hints)
    assert any("Back" in hint for hint in hints)


@pytest.mark.parametrize("items", ((), (ViewItem("heading", "Heading", kind="heading", details=Inherit),)))
def test_no_selectable_rows_blocks_selection_and_hides_hint(items):
    spec = view(items=items)
    assert tui_bindings(spec).resolve("ENTER").availability is ActionAvailability.BLOCKED
    assert not any("Select" in hint for hint in TuiViewBackend().action_labels(spec))


def test_query_submission_remains_available_without_result_rows():
    spec = view(items=(), selection_policy=SelectionPolicy(query_input=True))
    assert tui_bindings(spec).resolve("ENTER").availability is ActionAvailability.AVAILABLE


def test_tabs_have_explicit_availability_and_reverse_alias():
    action = ViewAction("tab", "switch", ("TAB", "BTAB"))
    assert tui_bindings(view(action)).resolve("TAB").availability is ActionAvailability.BLOCKED
    spec = view(action, tabs=(ViewTab("one", "One"),), active_tab_id="one")
    assert tui_bindings(spec).resolve("SHIFT_TAB").availability is ActionAvailability.AVAILABLE
    assert tui_bindings(spec, navigation_pending=True).resolve("TAB").availability is ActionAvailability.BLOCKED


def test_keyless_declarations_do_not_conflict_with_native_defaults():
    bindings = tui_bindings(view(ViewAction("back", "Return")))
    assert bindings.resolve("LEFT").action.id == "back"


def test_cover_or_loading_message_does_not_block_actions_by_itself():
    spec = view(ViewAction("refresh", "Reload", ("CTRL_R",)), message="Loading cover")
    assert all(item.availability is ActionAvailability.AVAILABLE for item in tui_bindings(spec).items)


def test_footer_shows_cancel_bindings_not_blocked_source_actions():
    session = ViewSession(view(ViewAction("select", "play", ("ENTER",)), ViewAction("refresh", "Reload", ("ESC",))))
    session.navigation = SimpleNamespace(message="Preparing")
    footer = ViewPresentation(session, None)._footer()
    assert footer.actions == ("Esc=back",)


def test_empty_selection_is_consumed_instead_of_dispatched():
    spec = view(ViewAction("select", "open", ("F1",)), items=())
    events = []
    context = SimpleNamespace(
        view=spec,
        session=SimpleNamespace(navigation=None, cancel_navigation=lambda: None),
        focus=lambda index: None,
        emit=lambda *args: events.append(args),
        tab=lambda *args: None,
    )
    assert TuiViewBackend().handle_key("F1", 0, context)
    assert events == []
