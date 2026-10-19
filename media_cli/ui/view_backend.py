"""Shared view-adapter boundary: content in, semantic input events out."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from .input_result import KeyResult

if TYPE_CHECKING:
    from .actions import ResolvedAction
    from .view_model import ViewAction, ViewSpec
    from .view_presentation import ViewPresentation
    from .view_session import ViewSession


def action_kind(action: ViewAction) -> str:
    return action.id if action.id in {"select", "back", "quit", "tab"} else "action"


@dataclass(frozen=True)
class ViewInputContext:
    view: ViewSpec
    session: ViewSession
    focus: Callable[[int], None]
    emit: Callable[[str, int, str | None], None]
    tab: Callable[[int, int], None]
    blocked: Callable[[ResolvedAction], None]


class ViewBackend(Protocol):
    @property
    def supports_images(self) -> bool: ...

    @property
    def supports_hover(self) -> bool: ...

    def header(self, presentation: ViewPresentation, item_id: str | None) -> str: ...

    def action_labels(self, view: ViewSpec, *, navigation_pending: bool = False) -> list[str]: ...

    def handle_key(self, key: str, index: int, context: ViewInputContext) -> bool | KeyResult: ...
