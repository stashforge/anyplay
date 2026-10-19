"""Compatibility dispatch for legacy picker calls, outside either renderer."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar, cast

from .picker_state import PickerState
from .renderer_selection import RendererKind, renderer_for
from .row_layout import DEFAULT_ROW_INDENTATION, RowIndentation

_P = ParamSpec("_P")
_R = TypeVar("_R")


class RendererUnavailable(RuntimeError):
    """The TUI cannot acquire a terminal; raised only before rendering starts."""


def _cancellable(callback: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(callback)
    def invoke(*args: Any, **kwargs: Any) -> Any:
        from .transition import PreparationCancelled

        try:
            return callback(*args, **kwargs)
        except PreparationCancelled:
            from .picker import invalidate_screen_buffer

            invalidate_screen_buffer()
            return None

    return invoke


def _prepare_request(request: dict[str, Any]) -> None:
    """Retain shared legacy callback/state behavior without drawing a screen."""
    if not isinstance(request.get("row_indentation", DEFAULT_ROW_INDENTATION), RowIndentation):
        raise TypeError("row_indentation requires RowIndentation")
    ui = request["ui"]
    resume = getattr(ui, "_resume_picker_position", None)
    state = request.get("state")
    if resume is not None:
        prompt = request["prompt"]
        prompt_key = str(prompt() if callable(prompt) else prompt)
        ui._resume_picker_position = None
        if state is None and resume[0] == prompt_key:
            state = request["state"] = resume[1]
    if state is not None:
        if not isinstance(state, PickerState):
            raise TypeError("state must be a PickerState")
        state.validate()
    for name in ("select_fn", "tab_fn", "right_fn", "info_fn", "reverse_fn", "alt_reverse_fn", "delete_fn", "adjust_fn"):
        callback = request.get(name)
        if callback is not None:
            request[name] = _cancellable(callback)


def dispatch_legacy_picker(terminal_picker: Callable[_P, _R]) -> Callable[_P, _R]:
    """Preserve the legacy signature while routing execution and setup fallback.

    Bind the declared signature once per call instead of maintaining duplicate
    argument forwarding lists that can silently drop newly introduced fields.
    """
    signature = inspect.signature(terminal_picker)

    @wraps(terminal_picker)
    def dispatch(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        _prepare_request(bound.arguments)

        def plain() -> _R:
            from .plain_picker import plain_pick

            return cast(_R, plain_pick(**bound.arguments))

        if renderer_for(bound.arguments["flags"]) is RendererKind.PLAIN:
            return plain()
        try:
            return terminal_picker(*bound.args, **bound.kwargs)
        except RendererUnavailable as error:
            logging.getLogger(__name__).debug("TUI unavailable; using plain picker: %s", error)
        return plain()

    return dispatch
