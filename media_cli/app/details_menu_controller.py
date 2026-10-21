"""Application-owned action menu events and ready-data updates."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from ..context import CliFlags, UiState
from ..state.theme import APPLICATION_THEME
from ..ui.poster import PosterManager
from ..ui.transition import PreparationCancelled
from ..ui.view_model import ViewSpec
from ..ui.view_picker import run_view_session
from ..ui.view_session import ExitSession, HandlerResult, ReplaceView, UiCommand, UiEvent, ViewSession

_BUILTIN_ACTIONS = frozenset({"select", "back", "quit", "help", "refresh", "translation", "information",
                              "increase", "decrease"})


@dataclass(frozen=True)
class DetailsMenuResult:
    kind: Literal["selected", "back", "quit"]
    action_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"selected", "back", "quit"}:
            raise ValueError("Unknown details menu outcome")
        if self.kind == "selected" and (not isinstance(self.action_id, str) or not self.action_id.strip()):
            raise TypeError("Selected outcome requires an action ID")
        if self.kind != "selected" and self.action_id is not None:
            raise ValueError("Only selection may carry an action ID")


class DetailsMenuController:
    def __init__(
        self,
        *,
        ui: UiState,
        read_view: Callable[[], ViewSpec],
        switch_translation: Callable[[], object],
        information: Callable[[], object],
        activate: Callable[[str, int | None], object] | None = None,
        action_handlers: Mapping[str, Callable[[str | None], object]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ui = ui
        self._read = read_view
        self._switch = switch_translation
        self._information = information
        self._activate = activate
        self._action_handlers = dict(action_handlers or {})
        for action, handler in self._action_handlers.items():
            if not isinstance(action, str) or not action.strip() or not callable(handler):
                raise TypeError("Menu action handlers require nonblank IDs and callable values")
            if action in _BUILTIN_ACTIONS:
                raise ValueError(f"Cannot override built-in menu action {action!r}")
        self._clock = clock
        self._next_poll = 0.0
        self._published = self._read()
        self._validate_handlers(self._published)
        if activate is None and any(item.adjustable for item in self._published.items):
            raise ValueError("Adjustable menus require an activation callback")
        self.session = ViewSession(self._published, self._handle_event)

    def _validate_handlers(self, view: ViewSpec) -> None:
        for action in view.actions:
            if action.id not in _BUILTIN_ACTIONS and action.id not in self._action_handlers:
                raise ValueError(f"Menu action {action.id!r} requires a handler")

    def updates(self, *, force: bool = False) -> tuple[UiCommand, ...]:
        """Read ready application state without fetching or waiting on provider work."""
        if self.session.exited:
            return ()
        now = self._clock()
        if not force and now < self._next_poll:
            return ()
        self._next_poll = now + 0.1
        view = self._read()
        self._validate_handlers(view)
        if view == self._published:
            return ()
        self._published = view
        return (ReplaceView(self.session.current.id, self.session.generation, view),)

    def _retain_cover(self) -> None:
        visible = self._ui.hovered_show_obj
        show = self._ui.ui_show_ctx
        image = self.session.current.image
        if (
            image is not None
            and isinstance(visible, dict)
            and isinstance(show, dict)
            and visible.get("_image_key") == image.key
            and PosterManager._get_cover_url(visible) == PosterManager._get_cover_url(show)
            and visible.get("_poster_status") == "ready"
            and visible.get("_poster_raw")
        ):
            show.update(
                _poster_raw=visible["_poster_raw"],
                _poster_status="ready",
                _poster_status_time=visible.get("_poster_status_time", time.time()),
            )
            show.pop("_poster_failed", None)

    def _handle_event(self, event: UiEvent) -> HandlerResult:
        if event.kind in {"back", "quit"}:
            self._retain_cover()
            return ExitSession(DetailsMenuResult("back" if event.kind == "back" else "quit"))
        if event.kind == "action" and isinstance(event.value, str) and event.value in self._action_handlers:
            item = self.session.item(event.item_id)
            reference = item.reference if item is not None else None
            if reference is not None and not isinstance(reference, str):
                raise TypeError("Menu action requires a string item reference or None")
            self._retain_cover()
            try:
                self._action_handlers[event.value](reference)
            except PreparationCancelled:
                pass
            return self.updates(force=True)
        if event.kind == "action" and event.value == "refresh":
            self._retain_cover()
            return ExitSession(DetailsMenuResult("selected", "REFRESH_TITLE"))
        if event.kind == "select" or (event.kind == "action" and event.value in {"increase", "decrease"}):
            item = self.session.item(event.item_id)
            if item is None:
                return None
            self._retain_cover()
            if not isinstance(item.reference, str):
                raise TypeError("Details menu selection requires an action ID")
            if self._activate is not None:
                direction = None if event.kind == "select" else (1 if event.value == "increase" else -1)
                try:
                    self._activate(item.reference, direction)
                except PreparationCancelled:
                    pass
                return self.updates(force=True)
            return ExitSession(DetailsMenuResult("selected", item.reference))
        if event.kind == "action" and event.value in {"translation", "information"}:
            self._retain_cover()
            try:
                (self._switch if event.value == "translation" else self._information)()
            except PreparationCancelled:
                pass
            return self.updates(force=True)
        return None


def run_details_menu(
    flags: CliFlags,
    ui: UiState,
    *,
    read_view: Callable[[], ViewSpec],
    switch_translation: Callable[[], object],
    information: Callable[[], object],
    activate: Callable[[str, int | None], object] | None = None,
    action_handlers: Mapping[str, Callable[[str | None], object]] | None = None,
    initial_selected_id: str | None = None,
) -> DetailsMenuResult:
    controller = DetailsMenuController(
        ui=ui, read_view=read_view, switch_translation=switch_translation, information=information,
        activate=activate, action_handlers=action_handlers,
    )
    if initial_selected_id is not None:
        item = controller.session.item(initial_selected_id)
        if item is None or not item.selectable:
            raise ValueError("Initial menu selection must identify a selectable item")
        controller.session.position.selected_id = initial_selected_id
    result = run_view_session(flags, ui, controller.session, theme=APPLICATION_THEME, updates_fn=controller.updates,
                              position_key=(controller.session.current.id,))
    if not isinstance(result, DetailsMenuResult):
        raise TypeError("Details session must return a typed menu outcome")
    return result
