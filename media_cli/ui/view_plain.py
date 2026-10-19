"""Plain view presentation and typed commands; no TUI bindings or styling."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .view_backend import ViewInputContext, action_kind

if TYPE_CHECKING:
    from .view_model import ViewSpec
    from .view_presentation import ViewPresentation


class PlainViewBackend:
    supports_images = False
    supports_hover = False

    def header(self, presentation: ViewPresentation, item_id: str | None) -> str:
        return "\n".join(presentation.plain_details(item_id))

    def action_labels(self, view: ViewSpec, *, navigation_pending: bool = False) -> list[str]:
        labels = []
        configured = set()
        commands = {"select": "1,2" if view.selection_policy.multiple else "1-N", "back": "b", "quit": "q", "tab": "t"}
        for action in view.actions:
            kind = action_kind(action)
            configured.add(kind)
            labels.append(f"[{commands.get(kind, action.id)}] {action.label}")
        for kind, label in (("select", "Select"), ("back", "Back"), ("quit", "Quit")):
            if kind not in configured:
                labels.append(f"[{commands[kind]}] {label}")
        if view.tabs and "tab" not in configured:
            labels.append("[t] Next tab")
        return labels

    def handle_key(self, key: str, index: int, context: ViewInputContext) -> bool:
        command = key.strip().casefold()
        if command in {"b", "back", "<", "left", "esc"}:
            context.emit("back", index, None)
        elif command in {"q", "quit", "exit"}:
            context.emit("quit", index, None)
        elif command in {"t", "tab"} and context.view.tabs:
            context.tab(index, 1)
        else:
            action = next(
                (
                    action
                    for action in context.view.actions
                    if action.id.casefold() == command and action_kind(action) == "action"
                ),
                None,
            )
            if action is None:
                return False
            context.emit("action", index, action.id)
        return True
