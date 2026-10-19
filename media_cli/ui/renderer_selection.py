"""Select renderers centrally; load view adapters only when requested."""

from __future__ import annotations

import sys
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .view_backend import ViewBackend


class RendererKind(Enum):
    TUI = "tui"
    PLAIN = "plain"


def select_renderer(*, plain_requested: bool, input_is_terminal: bool) -> RendererKind:
    """Keep explicit mode preference and capability fallback in one policy."""
    for name, value in (("plain_requested", plain_requested), ("input_is_terminal", input_is_terminal)):
        if type(value) is not bool:
            raise TypeError(f"{name} requires bool")
    return RendererKind.PLAIN if plain_requested or not input_is_terminal else RendererKind.TUI


def renderer_for(flags: object) -> RendererKind:
    """Adapt legacy optional flags at the boundary; the policy has typed inputs."""
    return select_renderer(
        plain_requested=bool(getattr(flags, "plain_mode", False)),
        input_is_terminal=sys.stdin.isatty(),
    )


def view_backend_for(flags: object) -> ViewBackend:
    """Load only the selected adapter; implementations do not import each other."""
    return view_backend_for_kind(renderer_for(flags))


def view_backend_for_kind(kind: RendererKind) -> ViewBackend:
    """Resolve an explicit selection, including a coordinated setup fallback."""
    if not isinstance(kind, RendererKind):
        raise TypeError("View backend selection requires RendererKind")
    if kind is RendererKind.PLAIN:
        from .view_plain import PlainViewBackend

        return PlainViewBackend()
    from .view_tui import TuiViewBackend

    return TuiViewBackend()
