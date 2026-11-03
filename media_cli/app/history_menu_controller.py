"""Route shared History view events to application-owned operations."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from ..context import CliFlags, UiState
from ..state.theme import APPLICATION_THEME
from ..ui.confirmation import confirm_view_action
from ..ui.transition import PreparationCancelled
from ..ui.view_model import ViewSpec
from ..ui.view_picker import run_view_session
from ..ui.view_session import ExitSession, HandlerResult, ReplaceView, UiCommand, UiEvent, UpdateFooter, ViewSession
from .history_menu_view import HISTORY_FOCUS_DELAY, HistoryEntryReference
from .history_diagnostics import HistoryDiagnostics

_ACTIONS = frozenset({"select", "back", "quit", "tab", "help", "information", "refresh_title", "refresh_view", "delete"})


@dataclass(frozen=True)
class HistoryMenuResult:
    kind: Literal["selected", "back", "quit"]
    reference: HistoryEntryReference | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"selected", "back", "quit"}:
            raise ValueError("Unknown History outcome")
        if self.kind == "selected" and not isinstance(self.reference, HistoryEntryReference):
            raise TypeError("Selected History outcome requires an entry reference")
        if self.kind != "selected" and self.reference is not None:
            raise ValueError("Only selected History outcomes may carry a reference")


class HistoryMenuController:
    def __init__(
        self, *, read_view: Callable[[], ViewSpec], switch_mode: Callable[[str], object],
        select: Callable[[HistoryEntryReference], bool],
        refresh_title: Callable[[HistoryEntryReference], object], refresh_view: Callable[[], object],
        delete: Callable[[HistoryEntryReference], object] | None,
        information: Callable[[HistoryEntryReference], object],
        focus: Callable[[HistoryEntryReference], object] | None = None,
        poll: Callable[[], object] | None = None,
        clock: Callable[[], float] = time.monotonic,
        diagnostics: HistoryDiagnostics | None = None,
    ) -> None:
        self._read, self._switch, self._select = read_view, switch_mode, select
        self._refresh_view, self._focus, self._poll = refresh_view, focus, poll
        self._handlers = {"refresh_title": refresh_title, "delete": delete, "information": information}
        for callback in (read_view, switch_mode, select, refresh_title, refresh_view, information):
            if not callable(callback):
                raise TypeError("History operations require callable handlers")
        for callback in (delete, focus, poll):
            if callback is not None and not callable(callback):
                raise TypeError("Optional History handlers require callables or None")
        self._clock, self._next_poll = clock, 0.0
        self.diagnostics = diagnostics if diagnostics is not None else HistoryDiagnostics()
        self._published = self._read()
        self._validate(self._published)
        self.session = ViewSession(self._published, self._handle_event)

    def _validate(self, view: ViewSpec) -> None:
        if not isinstance(view, ViewSpec):
            raise TypeError("History read_view must return a ViewSpec")
        for action in view.actions:
            if action.id not in _ACTIONS:
                raise ValueError(f"Unknown History action: {action.id!r}")
            if action.id == "delete" and self._handlers["delete"] is None:
                raise ValueError("History delete action requires a handler")
        for item in view.items:
            if item.selectable and not isinstance(item.reference, HistoryEntryReference):
                raise TypeError("History selectable rows require HistoryEntryReference values")

    def updates(self, *, force: bool = False) -> tuple[UiCommand, ...]:
        """Accept ready application results, then publish; never wait for workers."""
        if self.session.exited:
            return ()
        now = self._clock()
        if not force and now < self._next_poll:
            return ()
        self._next_poll = now + 0.1
        self.diagnostics.count('polls')
        try:
            with self.diagnostics.phase('controller'):
                with self.diagnostics.phase('poll'):
                    if self._poll is not None:
                        self._poll()
                with self.diagnostics.phase('view_read'):
                    view = self._read()
                with self.diagnostics.phase('validate'):
                    self._validate(view)
                with self.diagnostics.phase('compare'):
                    unchanged = view == self._published
                if unchanged:
                    return ()
                footer_only = replace(view, footer_statuses=self._published.footer_statuses) == self._published
                if footer_only:
                    self.diagnostics.count('footer_updates')
                    self._published = view
                    return (UpdateFooter(self.session.current.id, self.session.generation, view.footer_statuses),)
                if self.diagnostics.enabled:
                    if view.items != self._published.items:
                        kind = 'items'
                    else:
                        kind = 'other'
                    self.diagnostics.count(f'replace_{kind}')
                self._published = view
                return (ReplaceView(self.session.current.id, self.session.generation, view),)
        finally:
            self.diagnostics.report()

    def _handle_event(self, event: UiEvent) -> HandlerResult:
        if event.kind in {"back", "quit"}:
            return ExitSession(HistoryMenuResult("back" if event.kind == "back" else "quit"))
        try:
            if event.kind == "tab":
                if not isinstance(event.value, str):
                    raise TypeError("History tab requires a mode ID")
                self._switch(event.value)
            elif event.kind == "action" and event.value == "refresh_view":
                self._refresh_view()
            else:
                item = self.session.item(event.item_id)
                if item is None or not item.selectable:
                    return None
                reference = item.reference
                if not isinstance(reference, HistoryEntryReference):
                    raise TypeError("History interaction requires an entry reference")
                if event.kind == "select":
                    accepted = self._select(reference)
                    if type(accepted) is not bool:
                        raise TypeError("History selection preparation must return a boolean")
                    if accepted:
                        return ExitSession(HistoryMenuResult("selected", reference))
                elif event.kind == "focus" and self._focus is not None:
                    self._focus(reference)
                elif event.kind == "action" and isinstance(event.value, str) and event.value in self._handlers:
                    handler = self._handlers[event.value]
                    if handler is not None:
                        handler(reference)
                else:
                    return None
        except PreparationCancelled:
            if event.kind == "tab":
                self.session.position.active_tab_id = self._published.active_tab_id
        return self.updates(force=True)


def run_history_menu(
    flags: CliFlags, ui: UiState, *, read_view: Callable[[], ViewSpec], switch_mode: Callable[[str], object],
    select: Callable[[HistoryEntryReference], bool], refresh_title: Callable[[HistoryEntryReference], object],
    refresh_view: Callable[[], object], delete: Callable[[HistoryEntryReference], object] | None,
    information: Callable[[HistoryEntryReference], object],
    focus: Callable[[HistoryEntryReference], object] | None = None,
    poll: Callable[[], object] | None = None,
    diagnostics: HistoryDiagnostics | None = None,
) -> HistoryMenuResult:
    def confirmed_delete(reference: HistoryEntryReference) -> None:
        view = controller.session.current
        item = next((item for item in view.items if item.reference == reference and item.selectable), None)
        if item is not None and delete is not None and confirm_view_action(
            flags, ui, view, item_id=item.id,
            question="Delete this history entry?", theme=APPLICATION_THEME,
        ):
            delete(reference)

    controller = HistoryMenuController(
        read_view=read_view, switch_mode=switch_mode, select=select, refresh_title=refresh_title,
        refresh_view=refresh_view, delete=confirmed_delete if delete is not None else None,
        information=information, focus=focus, poll=poll, diagnostics=diagnostics,
    )
    try:
        result = run_view_session(
            flags, ui, controller.session, theme=APPLICATION_THEME, updates_fn=controller.updates,
            position_key=(controller.session.current.id,),
            focus_delay=HISTORY_FOCUS_DELAY,
        )
    finally:
        controller.diagnostics.report(force=True)
    if not isinstance(result, HistoryMenuResult):
        raise TypeError("History session must return a typed outcome")
    return result
