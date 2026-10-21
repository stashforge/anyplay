"""Reuse the picker's inline confirmation without executing domain actions."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import nullcontext
from copy import deepcopy
from typing import Any

from .picker_dispatch import RendererUnavailable
from .renderer_selection import RendererKind, renderer_for
from .row_text import plain_row_text
from .terminal_runtime import current_runtime, terminal_application
from .theme import DEFAULT_THEME, Theme
from .view_model import ViewSpec


@terminal_application
def confirm_picker_action(
    flags: Any,
    ui: Any,
    prompt: str,
    options: Sequence[str],
    *,
    selected_index: int,
    question: str,
    input_fn: Callable[[str], str] = input,
    **picker_options: Any,
) -> bool:
    """Render the existing rows with a y/N footer and return only a decision."""
    if type(selected_index) is not int or not 0 <= selected_index < len(options):
        raise ValueError("Confirmation requires a valid selected row index")
    if selected_index in set(picker_options.get("disabled_indices") or ()) | set(picker_options.get("heading_indices") or ()):
        raise ValueError("Cannot confirm a disabled row")
    question = plain_row_text(question)
    if not question.strip():
        raise ValueError("Confirmation requires a nonblank question")
    reserved = {"delete_fn", "delete_prompt", "confirmation_index", "initial_selected"}
    if reserved.intersection(picker_options):
        raise ValueError("Confirmation owns its decision callbacks and initial selection")
    message = f"{question} y/N"
    if renderer_for(flags) is RendererKind.TUI:
        from .picker import terminal_pick

        try:
            result = terminal_pick(
                flags, ui, prompt, options, initial_selected=selected_index,
                confirmation_index=selected_index, delete_prompt=message,
                **picker_options,
            )
            if result == -5:
                raise KeyboardInterrupt
            return bool(result == selected_index)
        except RendererUnavailable:
            pass
    runtime = current_runtime()
    with runtime.suspend() if runtime is not None else nullcontext():
        try:
            return input_fn(f"{message}: ").strip().casefold() == "y"
        except EOFError:
            return False


@terminal_application
def confirm_view_action(
    flags: Any,
    ui: Any,
    view: ViewSpec,
    *,
    item_id: str,
    question: str,
    theme: Theme = DEFAULT_THEME,
    input_fn: Callable[[str], str] = input,
) -> bool:
    """Present immutable shared view data with the existing default-No decision."""
    if not isinstance(view, ViewSpec):
        raise TypeError("Confirmation requires a ViewSpec")
    selected_index = next((i for i, item in enumerate(view.items) if item.id == item_id and item.selectable), None)
    if selected_index is None:
        raise ValueError("Confirmation requires a selectable item ID")
    question = plain_row_text(question)
    if not question.strip():
        raise ValueError("Confirmation requires a nonblank question")
    rows = tuple(item.label_text for item in view.items)
    if renderer_for(flags) is RendererKind.PLAIN:
        return confirm_picker_action(
            flags, ui, view.prompt, rows, selected_index=selected_index, question=question, input_fn=input_fn,
        )

    from .view_picker import active_view_session, run_view_session
    from .view_session import ExitSession, UiEvent, ViewSession

    def handle(event: UiEvent) -> ExitSession | None:
        if event.kind == "quit":
            raise KeyboardInterrupt
        if event.kind in {"select", "back"}:
            return ExitSession(event.kind == "select" and event.item_id == item_id)
        return None

    def pick(flags: Any, ui: Any, prompt: str, options: Sequence[str], **kwargs: Any) -> int:
        kwargs["auto_select_single_when_done"] = False
        decision = confirm_picker_action(
            flags, ui, prompt, options, selected_index=selected_index,
            question=question, input_fn=input_fn, **kwargs,
        )
        return selected_index if decision else -2

    session = ViewSession(view, handle)
    source = active_view_session()
    if source is not None and source.current.id == view.id and source.position.selected_id == item_id:
        source_frame = source.frame(source.current.id, source.generation)
        frame = session.frame(view.id, session.generation)
        # Confirmation borrows presentation state, never the parent's mutable objects.
        frame.position = deepcopy(source_frame.position)
        frame.picker_state = deepcopy(source_frame.picker_state)
    session.position.selected_id = item_id
    result = run_view_session(flags, ui, session, picker=pick, theme=theme)
    if type(result) is not bool:
        raise TypeError("Confirmation must return a boolean decision")
    return result
