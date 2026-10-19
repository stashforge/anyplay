"""Effective structured TUI actions, including native defaults and current availability."""

from __future__ import annotations

from .actions import ActionAvailability, ActionBindings, ResolvedAction, normalize_key
from .picker_actions import CORE_PICKER_ACTIONS
from .view_model import ViewAction, ViewSpec


def tui_bindings(view: ViewSpec, *, navigation_pending: bool = False) -> ActionBindings:
    declared = ActionBindings(tuple(ResolvedAction(action) for action in view.actions))
    resolved: list[ResolvedAction] = []
    claimed: set[str] = set()
    protected = {"CTRL_C", "ESC", "LEFT"} if navigation_pending else {"CTRL_C"}
    can_select = view.selection_policy.query_input or any(item.selectable for item in view.items)

    def append(action: ViewAction) -> None:
        message = None
        if navigation_pending and action.id not in {"back", "quit"}:
            message = "Still preparing the next screen"
        elif action.id == "select" and not can_select:
            message = "No selectable results"
        elif action.id == "tab" and not view.tabs:
            message = "No tabs available"
        availability = ActionAvailability.BLOCKED if message else ActionAvailability.AVAILABLE
        binding = ResolvedAction(action, availability, message)
        resolved.append(binding)
        claimed.update(binding.keys)

    for binding in declared.items:
        # Keyless declarations are presentation/typed-command labels, not native bindings.
        keys = tuple(key for key in binding.action.keys if normalize_key(key) not in protected)
        if keys:
            append(ViewAction(binding.action.id, binding.action.label, keys))

    defaults: tuple[ViewAction, ...] = CORE_PICKER_ACTIONS
    if view.tabs:
        defaults += (ViewAction("tab", "Change tab", ("TAB", "BTAB")),)
    for action in defaults:
        keys = tuple(key for key in action.keys if key not in claimed)
        if keys:
            append(ViewAction(action.id, action.label, keys))
    return ActionBindings(tuple(resolved))
