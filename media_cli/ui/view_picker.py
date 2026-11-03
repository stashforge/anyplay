"""Adapt stable-ID view sessions to the existing terminal picker."""

from __future__ import annotations

import math
import shutil
import time
from contextvars import ContextVar
from functools import lru_cache

from ..core.terminal import sanitize_terminal_text
from .navigation_tabs import NavigationTabs
from .picker_dispatch import RendererUnavailable
from .picker_state import PickerState
from .poster import PosterManager
from .renderer_selection import RendererKind
from .row_text import plain_row_text, render_row_text
from .styled_text import StyledText, TextRun
from .terminal_runtime import terminal_application
from .theme import DEFAULT_THEME
from .view_backend import ViewInputContext
from .view_model import visible_items
from .view_position_memory import ViewPositionMemory
from .view_presentation import ViewPresentation
from .view_renderer import ViewRenderer
from .view_session import ExitSession, UiEvent, ViewSession

_ACTIVE_SESSION: ContextVar[ViewSession | None] = ContextVar("active_view_session", default=None)


def active_view_session() -> ViewSession | None:
    """Return the invoking UI session only while its event handler runs."""
    return _ACTIVE_SESSION.get()


def _request_redraw(ui):
    from . import picker

    picker._needs_redraw = True
    callback = getattr(ui, "request_redraw", None)
    if callable(callback):
        callback()


def _save_position(frame, rows, state):
    position = frame.position
    position.query, position.scroll = state.query, state.scroll
    selected = rows[state.selected_index].id if 0 <= state.selected_index < len(rows) else None
    visible = [item for item in visible_items(frame.view, state.query) if item.selectable]
    valid = {item.id for item in visible}
    if selected in valid:
        position.selected_id = selected
    elif position.selected_id not in valid:
        current_indices = {item.id: index for index, item in enumerate(frame.view.items)}
        nearest = min(
            visible,
            key=lambda item: abs(current_indices[item.id] - state.selected_index),
            default=None,
        )
        position.selected_id = nearest.id if nearest is not None else None
    marked_ids = {rows[index].id for index in state.marked_indices if 0 <= index < len(rows)}
    position.marked_ids = marked_ids.intersection(item.id for item in frame.view.items if item.selectable)


def _run_view_session(flags, ui, session, *, picker=None, poster_manager=None, theme=DEFAULT_THEME, updates_fn=None,
                      position_memory=None, focus_delay=0.5):
    """Run a session, dispatching selection/actions after the picker releases input.

    Each renderer invocation owns an immutable index map. Structural updates
    finish that invocation before reopening, so label-based legacy selection
    reconciliation cannot change the identity of a focused or marked item.
    Paint-only footer updates retain the invocation and are read on live ticks.
    updates_fn may publish already-prepared commands on the UI owner thread;
    it must not fetch data, wait for work or write to the terminal.
    """
    renderer = ViewRenderer(flags, picker=picker)
    backend = renderer.backend
    if poster_manager is None:
        from .display import _configured_loading_frame, cover_cache_dir, cover_read_cache_dirs

        poster_manager = PosterManager(
            enabled=lambda: bool(flags.show_image) and backend.supports_images,
            cache_dir=cover_cache_dir,
            read_cache_dirs=cover_read_cache_dirs,
            hovered_show_id=lambda: ui.hovered_show_id,
            request_redraw=lambda: _request_redraw(ui),
            loading_frame=_configured_loading_frame,
        )
    previous_show = getattr(ui, "hovered_show_obj", None)
    if (backend.supports_images and isinstance(previous_show, dict) and previous_show.get("thumbnail")
            and previous_show.get("_poster_raw") and previous_show.get("_poster_status") == "ready"):
        # Carry a rendered cover across the legacy/shared picker boundary.
        poster_manager.get(dict(previous_show))
    presentation = ViewPresentation(session, poster_manager, theme=theme)
    focus_target = None
    focus_since = 0.0
    focus_delivered = False

    def observe_focus():
        nonlocal focus_target, focus_since, focus_delivered
        target = (session.current.id, session.generation, session.position.selected_id)
        if target != focus_target:
            focus_target, focus_since, focus_delivered = target, time.monotonic(), False

    def dispatch(event):
        if event.kind == "select":
            # auto_event consumes suppression; only commit it after a real
            # selection, never while an update can still abort the renderer.
            session.auto_event()
        token = _ACTIVE_SESSION.set(session)
        try:
            handled = session.dispatch(event)
        finally:
            _ACTIVE_SESSION.reset(token)
        if event.kind in {"back", "quit"} and not handled and not session.exited:
            session.apply(ExitSession())

    def drain_updates():
        session.drain_updates()
        if updates_fn is not None and not session.exited:
            for command in updates_fn():
                session.apply(command)

    while not session.exited:
        drain_updates()
        if session.exited:
            break
        if position_memory is not None:
            position_memory.restore(session)
        view, generation, version = session.current, session.generation, session.version
        frame = session.frame(view.id, generation)
        rows = view.items
        for item in rows:
            for content in (item.label, item.hint, item.prefix):
                if isinstance(content, StyledText):
                    for run in content.runs:
                        theme.resolve_style(run.role)
        options = [item.label_text for item in rows]
        ids = {item.id: index for index, item in enumerate(rows)}
        state = frame.picker_state or PickerState()
        state.query = frame.position.query
        state.selected_index = ids.get(frame.position.selected_id, 0)
        state.scroll = frame.position.scroll
        state.marked_indices = {ids[item_id] for item_id in frame.position.marked_ids if item_id in ids}
        frame.picker_state = state
        pending_event = None
        started = False

        def changed():
            return session.exited or session.version != version

        @lru_cache(maxsize=1)
        def matching_rows(query):
            return tuple(visible_items(view, query))

        def item_at(index):
            if not isinstance(index, int) or not 0 <= index < len(rows):
                return None
            item = rows[index]
            return item if item.selectable and any(row.id == item.id for row in matching_rows(frame.position.query)) else None

        def event(kind, index=None, value=None):
            item = item_at(index)
            return UiEvent(kind, view.id, generation, item.id if item is not None else None, value)

        def focus(index):
            if changed():
                return
            item = item_at(index)
            if started:
                selected_id = item.id if item is not None else None
                if selected_id != frame.position.selected_id:
                    session.cancel_navigation()
                frame.position.selected_id = selected_id
                observe_focus()
            selected_id = frame.position.selected_id
            ui.hovered_show_obj = presentation.show_for(selected_id) if selected_id is not None else None
            ui.hovered_show_id = presentation.selected_show_id if selected_id is not None else None

        def header(index):
            focus(index)
            item_id = frame.position.selected_id
            return backend.header(presentation, item_id)

        def poster(index):
            focus(index)
            if not presentation.image_slot_enabled(frame.position.selected_id):
                return None
            return presentation.poster(frame.position.selected_id)

        def filter_rows(query, _options):
            visible = {item.id for item in matching_rows(query)}
            return [index for index, item in enumerate(rows) if item.id in visible]

        def live(query):
            nonlocal started, focus_delivered
            started = True
            if not changed():
                if query != frame.position.query:
                    session.cancel_navigation()
                frame.position.query = query
                ui.active_picker_query = query
                if not any(item.id == frame.position.selected_id and item.selectable for item in matching_rows(query)):
                    frame.position.selected_id = None
                observe_focus()
            drain_updates()
            if not changed() and backend.supports_hover and not focus_delivered and frame.position.selected_id is not None:
                if time.monotonic() - focus_since >= focus_delay:
                    focus_delivered = True
                    session.dispatch(UiEvent("focus", view.id, generation, frame.position.selected_id))
            if changed():
                _request_redraw(ui)
                return options, initial_header, view.state != "loading"
            return options, header(ids.get(frame.position.selected_id, -1)), view.state != "loading"

        def tab(index, direction=1):
            nonlocal pending_event
            if view.tabs:
                tab_ids = [tab.id for tab in view.tabs]
                current = frame.position.active_tab_id
                tab_index = tab_ids.index(current) if current in tab_ids else 0
                value = tab_ids[(tab_index + direction) % len(tab_ids)]
            else:
                value = direction
            pending_event = event("tab", index, value)

        def emit(kind, index, value):
            nonlocal pending_event
            pending_event = event(kind, index, value)

        def adjust(index, direction, current_view=view):
            if changed() or session.input_blocked or current_view.state == "loading":
                return False
            item = item_at(index)
            if item is None or not item.adjustable or direction not in (-1, 1):
                return False
            emit("action", index, "increase" if direction == 1 else "decrease")
            return True

        input_context = ViewInputContext(view=view, session=session, focus=focus, emit=emit, tab=tab,
                                         blocked=presentation.blocked_action)

        def key(key, index):
            return backend.handle_key(key, index, input_context)

        def frame_ready():
            if session.acknowledge_frame(view.id, generation):
                _request_redraw(ui)

        def info(index):
            item = item_at(index)
            if item is None or not item.info:
                return
            from .info_screen import show_info_screen

            show = dict(presentation.show_for(item.id))
            show.update(name=sanitize_terminal_text(item.label_text), description=presentation.info(item.id),
                        _enrichment_attempted=True)
            show_info_screen(show, poster_manager=poster_manager if backend.supports_images else None, ui=ui)
            _request_redraw(ui)

        observe_focus()
        initial_header = header(state.selected_index)
        auto = (
            not frame.auto_entered
            and session.navigation is None
            and view.state == "ready"
            and view.selection_policy.auto_single
            and sum(item.selectable for item in view.items) == 1
        )
        active_tab_id = frame.position.active_tab_id or view.active_tab_id
        active_tab = next((tab for tab in view.tabs if tab.id == active_tab_id), None)
        prompt = f"{view.prompt} \u00b7 {sanitize_terminal_text(active_tab.label)}" if active_tab is not None else view.prompt

        def row_part(content):
            if isinstance(content, StyledText) and renderer.kind is RendererKind.TUI:
                width = shutil.get_terminal_size(fallback=(80, 24)).columns
                return render_row_text(content, columns=max(0, width), theme=theme)
            return plain_row_text(content)

        def render_label(index, query, selected, width):
            content = rows[index].label
            if not isinstance(content, StyledText):
                return None
            return render_row_text(content, columns=max(0, width), query=query, selected=selected, theme=theme)

        try:
            result = renderer.pick(
                flags, ui, prompt, options,
                header=initial_header,
                header_fn=header,
                top_header_fn=poster if backend.supports_images and flags.show_image else None,
                live_fn=live,
                tick_fn=lambda: backend.supports_images and presentation.needs_tick(),
                info_fn=info,
                hints=lambda index: row_part(rows[index].hint) if 0 <= index < len(rows) else "",
                item_prefix_fn=lambda index: row_part(rows[index].prefix) if 0 <= index < len(rows) else "",
                item_render_fn=render_label if renderer.kind is RendererKind.TUI else None,
                select_fn=lambda index: item_at(index) is not None,
                tab_fn=tab if view.tabs else None,
                multi_select=view.selection_policy.multiple,
                return_query_on_enter=view.selection_policy.query_input,
                auto_select_single_when_done=(lambda: (
                    renderer.kind is RendererKind.PLAIN or not session.input_blocked
                )) if auto else False,
                reverse_items=view.reverse_items,
                count_total=view.total_count,
                disabled_indices={index for index, item in enumerate(rows) if not item.selectable},
                heading_indices={index for index, item in enumerate(rows) if item.kind == "heading"},
                row_indentation=view.row_indentation,
                state=state,
                return_on_quit=True,
                structured=True,
                action_labels=backend.action_labels(view, navigation_pending=session.navigation is not None),
                navigation_prompt=StyledText((TextRun(sanitize_terminal_text(view.prompt), "prompt"),)),
                navigation_tabs=NavigationTabs(tuple(view.tabs), active_tab_id) if active_tab is not None else None,
                filter_fn=filter_rows,
                adjust_fn=adjust if any(item.adjustable for item in rows) else None,
                key_fn=key,
                help_dict=dict(view.help) or None,
                exit_fn=lambda: pending_event is not None or changed(),
                frame_ready_fn=frame_ready if renderer.kind is RendererKind.TUI else None,
                input_pending_fn=lambda: session.input_blocked,
            )
        except RendererUnavailable as error:
            renderer.fallback(error)
            backend = renderer.backend
            continue
        if renderer.kind is RendererKind.PLAIN:
            session.acknowledge_frame(view.id, generation)
        if isinstance(result, int) and result >= 0 and item_at(result) is not None:
            state.selected_index = result
        if isinstance(result, str):
            state.query = result
        if frame.generation == generation:
            _save_position(frame, rows, state)
        if session.exited:
            break
        if result == -5:
            # Interrupt wins over an earlier queued action or completed update.
            dispatch(UiEvent("quit", session.current.id, session.generation))
            continue
        if changed():
            continue
        if pending_event is not None:
            if pending_event.kind == "select":
                if view.selection_policy.query_input:
                    pending_event = event("query", value=state.query)
                elif view.selection_policy.multiple:
                    targets = sorted(index for index in state.marked_indices if item_at(index) is not None)
                    targets = targets or [ids.get(pending_event.item_id, -1)]
                    selected = [item_at(index) for index in targets]
                    if not selected or any(item is None for item in selected):
                        continue
                    pending_event = event("select", targets[0], tuple(item.id for item in selected))
            dispatch(pending_event)
        elif isinstance(result, str):
            dispatch(event("query", value=result))
        elif isinstance(result, list):
            selected = [item_at(index) for index in result]
            if selected and all(item is not None for item in selected):
                frame.position.marked_ids = {item.id for item in selected}
                dispatch(event("select", result[0], tuple(item.id for item in selected)))
        elif result in {-2, -3}:
            dispatch(event("back"))
        elif isinstance(result, int) and item_at(result) is not None:
            dispatch(event("select", result))
    return session.result


@terminal_application
def run_view_session(flags, ui, session, *, picker=None, poster_manager=None, theme=DEFAULT_THEME, updates_fn=None,
                     position_key=None, restore_position=True, focus_delay=0.5):
    if isinstance(focus_delay, bool) or not isinstance(focus_delay, (int, float)):
        raise TypeError("View focus_delay requires seconds as a number")
    if not math.isfinite(focus_delay) or focus_delay < 0:
        raise ValueError("View focus_delay requires finite nonnegative seconds")
    memory = ViewPositionMemory(ui, session, position_key, restore_position)
    try:
        return _run_view_session(flags, ui, session, picker=picker, poster_manager=poster_manager,
                                 theme=theme, updates_fn=updates_fn, position_memory=memory, focus_delay=focus_delay)
    finally:
        memory.save()
        session.cancel_navigation()
