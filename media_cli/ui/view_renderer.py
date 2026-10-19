"""Keep a structured session's picker and view adapter on the same backend."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .picker_dispatch import RendererUnavailable
from .renderer_selection import RendererKind, renderer_for, view_backend_for_kind
from .terminal_runtime import current_runtime, terminal_application


class ViewRenderer:
    def __init__(self, flags: object, *, picker: Callable[..., Any] | None = None) -> None:
        self.kind = renderer_for(flags)
        self.backend = view_backend_for_kind(self.kind)
        self._picker = picker

    @terminal_application
    def pick(self, *args: Any, **kwargs: Any) -> Any:
        if self._picker is None:
            if self.kind is RendererKind.PLAIN:
                from .plain_picker import plain_pick

                self._picker = plain_pick
            else:
                from .picker import terminal_pick

                self._picker = terminal_pick
        runtime = current_runtime() if self.kind is RendererKind.PLAIN else None
        if runtime is not None:
            # A failed TUI descriptor setup may leave the outer owner in raw
            # mode. Numbered input needs its original terminal settings.
            with runtime.suspend():
                return self._picker(*args, **kwargs)
        return self._picker(*args, **kwargs)

    def fallback(self, error: RendererUnavailable) -> None:
        """Switch once; the caller must rebuild content before invoking again."""
        if self.kind is not RendererKind.TUI:
            raise error
        logging.getLogger(__name__).debug("TUI unavailable; rebuilding view with plain backend: %s", error)
        self.kind = RendererKind.PLAIN
        self.backend = view_backend_for_kind(self.kind)
        self._picker = None
