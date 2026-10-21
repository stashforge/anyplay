"""TUI view presentation and key bindings, independent of plain commands."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from .actions import ActionAvailability, ResolvedAction, normalize_key
from .input_result import KeyResult
from .picker_actions import loading_bindings, picker_bindings
from .tui_actions import tui_bindings
from .view_backend import ViewInputContext, action_kind
from .view_presentation import ViewPresentation

if TYPE_CHECKING:
    from .view_model import ViewSpec


class TuiViewBackend:
    supports_images = True
    supports_hover = True

    def header(self, presentation: ViewPresentation, item_id: str | None) -> str:
        return presentation.details(item_id, shutil.get_terminal_size(fallback=(80, 24)).columns)

    def action_labels(self, view: ViewSpec, *, navigation_pending: bool = False) -> list[str]:
        labels = []
        bindings = tui_bindings(view, navigation_pending=navigation_pending)
        for binding in bindings.items:
            if binding.availability is ActionAvailability.BLOCKED:
                continue
            action = binding.action
            keys = "/".join(binding.key_labels)
            labels.append(f"[{keys}] {action.label}")
        if not navigation_pending:
            labels.extend(action.label for action in view.actions if not action.keys)
        return labels

    def handle_key(self, key: str, index: int, context: ViewInputContext) -> bool | KeyResult:
        view, session = context.view, context.session
        if len(key) == 1 and key.isprintable():
            return KeyResult.CONSUMED if session.input_blocked else False
        name = normalize_key(key)
        bindings = tui_bindings(view, navigation_pending=session.input_blocked)
        binding = bindings.resolve(name)
        if session.input_blocked:
            if binding is None:
                native = picker_bindings(tabs=bool(view.tabs), info=any(item.info for item in view.items),
                                         multiple=view.selection_policy.multiple)
                binding = loading_bindings(native).resolve(name)
            if binding is not None and binding.availability is ActionAvailability.AVAILABLE:
                context.emit(action_kind(binding.action), index, None)
                return True
            if binding is not None:
                context.blocked(binding)
            return KeyResult.CONSUMED
        if view.state == "loading" and not any(item.selectable for item in view.items) and not view.selection_policy.query_input:
            candidate = binding or loading_bindings(picker_bindings()).resolve(name)
            editing = bool(session.position.query) and name in {"HOME", "END", "RIGHT"}
            if candidate is not None and candidate.action.id in {"select", "move"} and not editing:
                context.blocked(ResolvedAction(candidate.action, ActionAvailability.BLOCKED, "Waiting for results"))
                return KeyResult.CONSUMED
        context.focus(index)
        if name in {"UP", "DOWN", "HOME", "END", "PAGE_UP", "PAGE_DOWN"}:
            session.cancel_navigation()
        if binding is not None:
            if (
                name in {"LEFT", "RIGHT"} and binding.action.id in {"back", "select"}
                and (view.selection_policy.query_input or session.position.query)
            ):
                return False
            if binding.availability is ActionAvailability.BLOCKED:
                return True
            action = binding.action
            kind = action_kind(action)
            if kind == "select" and name in {"ENTER", "RIGHT"}:
                return False
            if kind == "tab":
                if view.tabs:
                    context.tab(index, -1 if name in {"BTAB", "CTRL_P"} else 1)
            elif name == "CTRL_O" and action.id == "info":
                return False
            else:
                context.emit(kind, index, action.id if kind == "action" else None)
            return True
        return False
