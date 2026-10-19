"""Adapt stable-ID view sessions to the existing terminal picker."""

from __future__ import annotations

import time

from ..core.terminal import sanitize_terminal_text
from .navigation_tabs import NavigationTabs
from .picker_dispatch import RendererUnavailable
from .picker_state import PickerState
from .poster import PosterManager
from .renderer_selection import RendererKind
from .styled_text import StyledText, TextRun
from .terminal_runtime import terminal_application
from .view_backend import ViewInputContext
from .view_model import visible_items
from .view_presentation import ViewPresentation
from .view_renderer import ViewRenderer
from .view_session import ExitSession, UiEvent


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


def _run_view_session(flags, ui, session, *, picker=None, poster_manager=None):
    """Run a session, dispatching selection/actions after the picker releases input.

    Each renderer invocation owns an immutable index map. Accepted updates
    finish that invocation before reopening, so label-based legacy selection
    reconciliation cannot change the identity of a focused or marked item.
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
    presentation = ViewPresentation(session, poster_manager)
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
        handled = session.dispatch(event)
        if event.kind in {"back", "quit"} and not handled and not session.exited:
            session.apply(ExitSession())

    while not session.exited:
        session.drain_updates()
        if session.exited:
            break
        view, generation, version = session.current, session.generation, session.version
        frame = session.frame(view.id, generation)
        rows = view.items
        options = [item.label for item in rows]
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

        def item_at(index):
            return rows[index] if isinstance(index, int) and 0 <= index < len(rows) and rows[index].selectable else None

        def event(kind, index=None, value=None):
            item = item_at(index)
            return UiEvent(kind, view.id, generation, item.id if item is not None else None, value)

        def focus(index):
            if changed():
                return
            item = item_at(index)
            if started and item is not None:
                if item.id != frame.position.selected_id:
                    session.cancel_navigation()
                frame.position.selected_id = item.id
                observe_focus()
            selected_id = frame.position.selected_id
            ui.hovered_show_obj = presentation.show_for(selected_id)
            ui.hovered_show_id = presentation.selected_show_id

        def header(index):
            focus(index)
            item_id = frame.position.selected_id
            return backend.header(presentation, item_id)

        def poster(index):
            focus(index)
            return presentation.poster(frame.position.selected_id)

        def filter_rows(query, _options):
            visible = {item.id for item in visible_items(view, query)}
            return [index for index, item in enumerate(rows) if item.id in visible]

        def live(query):
            nonlocal started, focus_delivered
            started = True
            if not changed():
                if query != frame.position.query:
                    session.cancel_navigation()
                frame.position.query = query
                ui.active_picker_query = query
                observe_focus()
            session.drain_updates()
            if not changed() and backend.supports_hover and not focus_delivered and frame.position.selected_id is not None:
                if time.monotonic() - focus_since >= 0.5:
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
            show.update(name=sanitize_terminal_text(item.label), description=presentation.info(item.id),
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
        try:
            result = renderer.pick(
                flags, ui, prompt, options,
                header=initial_header,
                header_fn=header,
                top_header_fn=poster if backend.supports_images and flags.show_image else None,
                live_fn=live,
                tick_fn=lambda: backend.supports_images and presentation.needs_tick(),
                info_fn=info,
                hints=lambda index: rows[index].hint if 0 <= index < len(rows) else "",
                item_prefix_fn=lambda index: rows[index].prefix if 0 <= index < len(rows) else "",
                select_fn=lambda index: item_at(index) is not None,
                tab_fn=tab if view.tabs else None,
                multi_select=view.selection_policy.multiple,
                return_query_on_enter=view.selection_policy.query_input,
                auto_select_single_when_done=(lambda: (
                    renderer.kind is RendererKind.PLAIN or not session.input_blocked
                )) if auto else False,
                reverse_items=view.reverse_items,
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
                key_fn=key,
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
def run_view_session(flags, ui, session, *, picker=None, poster_manager=None):
    try:
        return _run_view_session(flags, ui, session, picker=picker, poster_manager=poster_manager)
    finally:
        session.cancel_navigation()
