from dataclasses import FrozenInstanceError

import pytest

from media_cli.ui.actions import (
    ActionAvailability,
    ActionBindings,
    ResolvedAction,
    key_label,
    normalize_key,
)
from media_cli.ui.view_model import ViewAction


@pytest.mark.parametrize(
    ("source", "canonical", "label"),
    (
        ("enter", "ENTER", "Enter"),
        ("shift_tab", "BTAB", "Shift+Tab"),
        ("BTAB", "BTAB", "Shift+Tab"),
        ("pageup", "PAGE_UP", "Page Up"),
        ("PAGEDOWN", "PAGE_DOWN", "Page Down"),
        ("ctrl_c", "CTRL_C", "Ctrl+C"),
        ("RIGHT", "RIGHT", "Right"),
        ("i", "I", "i"),
    ),
)
def test_key_names_and_help_labels(source, canonical, label):
    assert normalize_key(source) == canonical
    assert key_label(source) == label


def test_lookup_reuses_action_and_preserves_declared_order():
    action = ViewAction("select", "open", ("ENTER", "RIGHT"))
    resolved = ResolvedAction(action)
    bindings = ActionBindings((resolved,))
    assert resolved.action is action
    assert resolved.availability is ActionAvailability.AVAILABLE
    assert resolved.key_labels == ("Enter", "Right")
    assert bindings.resolve("right") is resolved
    assert bindings.resolve("UNKNOWN") is None
    assert bindings.items == (resolved,)


def test_blocked_action_is_still_resolved_for_feedback():
    resolved = ResolvedAction(
        ViewAction("select", "open", ("ENTER",)),
        ActionAvailability.BLOCKED,
        "Still preparing this selection",
    )
    assert ActionBindings((resolved,)).resolve("enter") is resolved
    assert resolved.blocked_message == "Still preparing this selection"


def test_availability_is_not_inferred_from_action_text():
    resolved = ResolvedAction(ViewAction("loading", "Loading...", ("ENTER",)))
    assert resolved.availability is ActionAvailability.AVAILABLE


@pytest.mark.parametrize("message", (None, "", "  ", "one\ntwo", "\x1b[31mred"))
def test_blocked_action_requires_nonblank_plain_single_line_message(message):
    with pytest.raises((ValueError, TypeError)):
        ResolvedAction(ViewAction("select", "open"), ActionAvailability.BLOCKED, message)


def test_available_action_rejects_stale_blocked_message():
    with pytest.raises(ValueError):
        ResolvedAction(ViewAction("select", "open"), blocked_message="Wait")


def test_string_availability_is_not_silently_accepted():
    with pytest.raises(TypeError):
        ResolvedAction(ViewAction("select", "open"), "blocked")


def test_wrong_action_type_is_rejected():
    with pytest.raises(TypeError):
        ResolvedAction("select")


def test_same_action_id_can_have_separate_key_groups_and_help_labels():
    search = ResolvedAction(ViewAction("back", "search", ("LEFT",)))
    back = ResolvedAction(ViewAction("back", "back", ("ESC",)))
    bindings = ActionBindings((search, back))
    assert bindings.resolve("left") is search
    assert bindings.resolve("esc") is back


@pytest.mark.parametrize("keys", (("TAB", "tab"), ("BTAB", "SHIFT_TAB")))
def test_conflicting_keys_across_groups_fail(keys):
    with pytest.raises(ValueError, match="key"):
        ActionBindings(tuple(ResolvedAction(ViewAction(str(i), "", (key,))) for i, key in enumerate(keys)))


def test_aliases_within_one_group_are_deduplicated():
    action = ResolvedAction(ViewAction("tab", "switch", ("BTAB", "SHIFT_TAB")))
    assert action.key_labels == ("Shift+Tab",)
    assert ActionBindings((action,)).resolve("shift_tab") is action


def test_display_preserves_first_declared_printable_key_spelling():
    action = ResolvedAction(ViewAction("info", "Info", ("i", "I", "CTRL_O")))
    assert action.keys == ("I", "CTRL_O")
    assert action.key_labels == ("i", "Ctrl+O")


def test_repeated_keyless_ids_are_rejected():
    with pytest.raises(ValueError, match="ID"):
        ActionBindings((ResolvedAction(ViewAction("back", "")), ResolvedAction(ViewAction("back", ""))))


def test_empty_bindings_are_valid_and_unbound():
    assert ActionBindings(()).resolve("ENTER") is None


def test_bindings_require_immutable_typed_tuple():
    with pytest.raises(TypeError):
        ActionBindings([])
    with pytest.raises(TypeError):
        ActionBindings((ViewAction("select", "open"),))


def test_records_are_frozen():
    resolved = ResolvedAction(ViewAction("select", "open"))
    with pytest.raises(FrozenInstanceError):
        resolved.blocked_message = "Wait"
    bindings = ActionBindings((resolved,))
    with pytest.raises(FrozenInstanceError):
        bindings.items = ()


@pytest.mark.parametrize("key", ("", " ", "PAGE UP", "ENTER\n", "\x1b[A", None))
def test_invalid_symbolic_keys_fail(key):
    with pytest.raises((ValueError, TypeError)):
        normalize_key(key)
