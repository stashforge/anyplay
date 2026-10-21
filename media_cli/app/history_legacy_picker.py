"""Compatibility presentation for plain/nonterminal History, not the shared TUI."""

from __future__ import annotations

import os
import time

from media_cli import app_core

from ..core.terminal import sanitize_terminal_text
from ..ui.footer import Footer
from ..ui.picker_state import PickerState
from ..ui.view_position_memory import ViewPositionMemory
from ..ui.view_session import UiEvent
from . import history_menu_controller
from .footer import context_prefix
from .history_menu_view import HISTORY_FOCUS_DELAY, HistoryEntryReference


def run_legacy_history(
    flags, ui, ms, *, picker, read_entries, read_view, select, switch_mode,
    refresh_title, refresh_view, delete, information, poll, focus, read_status, is_refreshing,
):
    """Adapt existing indexed callbacks to stable references; caller owns domain work.

    Invoke on the UI owner. This adapter retains old plain/redirected interaction
    until that separately scoped renderer is redesigned.
    """
    control = history_menu_controller.HistoryMenuController(
        read_view=read_view, select=select, switch_mode=switch_mode,
        refresh_title=refresh_title, refresh_view=refresh_view, delete=delete,
        information=information, poll=poll, focus=focus,
    )
    memory = ViewPositionMemory(ui, control.session, ("history",), True)
    memory.restore(control.session)
    frame = memory.frame
    state = frame.picker_state or PickerState()
    state.selected_index = next((i for i, item in enumerate(frame.view.items)
                                 if item.id == frame.position.selected_id), 0)
    frame.picker_state = state
    hovered, hover_since, pending_delete = None, time.monotonic(), None

    def options():
        return [app_core.format_history_entry(entry) for entry in read_entries()]

    def reference_at(index):
        entries = read_entries()
        return (HistoryEntryReference.from_entry(entries[index])
                if isinstance(index, int) and 0 <= index < len(entries) else None)

    def event(kind, reference=None, value=None):
        for command in control.updates(force=True):
            control.session.apply(command)
        item = next((item for item in control.session.current.items if item.reference == reference), None)
        if kind == "select" and item is None:
            return False
        control.session.dispatch(UiEvent(kind, control.session.current.id, control.session.generation,
                                         item.id if item is not None else None, value))
        return control.session.exited

    def header(index):
        nonlocal hovered, hover_since
        reference = reference_at(index)
        if reference != hovered:
            hovered, hover_since = reference, time.monotonic()
        try:
            width = os.get_terminal_size().columns
        except OSError:
            width = 80
        entries, parts = read_entries(), []
        if entries:
            entry = entries[max(0, min(index, len(entries) - 1))]
            show = entry.get("show", {})
            app_core.build_info_panel(show, entry.get("translation_type", "sub"), width, parts,
                                      local_only=True, main_title=show.get("name"))
        else:
            entry, show = {}, {}
            parts.extend(["", f"\033[38;5;244m{control.session.current.message}\033[0m", ""])
        updated = app_core.format_history_updated_time(entry)
        context = (context_prefix(show, ui, ms, mode="History"),
                   "INCOGNITO (READ-ONLY)" if flags.incognito_mode else "",
                   f"Updated {updated}" if updated else "")
        status = read_status()
        footer = Footer(
            statuses=(status,) if status is not None else (),
            context=tuple(sanitize_terminal_text(part) for part in context if part),
            actions=("Enter open", "Tab/Ctrl+N next", "Ctrl+R refresh title", "F5/Alt+R refresh view")
            + (() if flags.incognito_mode else ("Del/Ctrl+D delete",)) + ("Esc quit",),
            status_separator=" • ",
        )
        parts.append(app_core._poster_footer_line(show, footer, width))
        return "\n".join(parts)

    def poster(index):
        entries = read_entries()
        if entries:
            show = entries[max(0, min(index, len(entries) - 1))].get("show", {})
            ui.hovered_show_id, ui.hovered_show_obj = show.get("_id"), show
            app_core._hovered_show_id = ui.hovered_show_id
            return app_core._get_poster(show) or ""
        return ""

    def tab(_selected=None, direction=1):
        modes = tuple(tab.id for tab in control.session.current.tabs)
        mode = control.session.current.active_tab_id
        event("tab", value=modes[(modes.index(mode) + direction) % len(modes)])
        return options(), header(0)

    def refresh(selected=None):
        reference = reference_at(selected)
        if reference is not None:
            event("action", reference, "refresh_title")
        return options(), header(selected if selected is not None else 0)

    def refresh_all(selected=None):
        event("action", value="refresh_view")
        return options(), header(selected if selected is not None else 0)

    def info(selected):
        reference = reference_at(selected)
        if reference is not None:
            event("action", reference, "information")

    def can_delete(selected):
        nonlocal pending_delete
        pending_delete = reference_at(selected) if not flags.incognito_mode else None
        return pending_delete is not None

    def remove(selected):
        nonlocal pending_delete
        reference, pending_delete = pending_delete or reference_at(selected), None
        if reference is not None:
            event("action", reference, "delete")
        return options(), header(min(selected, max(0, len(read_entries()) - 1)))

    def choose(selected):
        reference = reference_at(selected)
        return event("select", reference) if reference is not None else False

    def tick():
        for command in control.updates(force=True):
            control.session.apply(command)
        if hovered is not None and time.monotonic() - hover_since >= HISTORY_FOCUS_DELAY:
            focus(hovered)
        return is_refreshing()

    def prefix(index):
        entries = read_entries()
        return "\033[38;2;166;227;161m◆\033[0m " if 0 <= index < len(entries) and entries[index].get("has_new_release") else ""

    def prompt():
        base = f"Watch History · {control.session.current.active_tab_id}"
        return f"\033[38;2;155;125;185mINCOGNITO\033[0m · {base}" if flags.incognito_mode else base

    try:
        selected = picker(
            flags, ui, prompt, options(), header_fn=header, top_header_fn=poster,
            tab_fn=tab, reverse_fn=refresh, alt_reverse_fn=refresh_all,
            delete_fn=None if flags.incognito_mode else remove,
            can_delete_fn=None if flags.incognito_mode else can_delete,
            live_fn=lambda _query: (options(), "", False), info_fn=info,
            help_dict=dict(control.session.current.help), keep_cursor_hidden_on_select=True,
            count_total=lambda: control.session.current.total_count, tick_fn=tick,
            item_prefix_fn=prefix, select_fn=choose, item_key_fn=reference_at, state=state,
        )
        if not control.session.exited:
            if selected in (-2, -3):
                event("quit" if selected == -2 else "back")
            elif reference_at(selected) is not None:
                choose(selected)
        return control.session.result
    finally:
        frame.position.query, frame.position.scroll = state.query, state.scroll
        frame.position.active_tab_id = control.session.current.active_tab_id
        reference = reference_at(state.selected_index)
        frame.position.selected_id = next((item.id for item in frame.view.items if item.reference == reference), None)
        memory.save()
