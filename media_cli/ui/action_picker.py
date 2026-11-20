"""Render a structured action session with shared chrome; keep its event hooks."""
import logging
import time
from contextlib import contextmanager

from .input_result import KeyResult
from .list_selection import ListSelection
from .list_screen import list_screen_frame
from .screen_chrome import RESET, role_prefix, run_framed_screen, ScreenTransition, list_header_context
from ..state.theme import MINT_SCREEN_THEME
from .picker_dispatch import RendererUnavailable


class _Transition(ScreenTransition):
    pass


class _AutomaticSelection(Exception):
    def __init__(self, index):
        self.index = index


def action_picker(flags, ui, prompt, options, *, presentation, **hooks):
    saved = hooks.get("state")
    disabled = hooks.get("disabled_indices") or set()
    rows = ListSelection([(str(i), {"name": name}) for i, name in enumerate(options)], "",
                         selected=saved.selected_index if saved else 0,
                         query=saved.query if saved else "", bottom=presentation.get("layout") != "top")
    from .navigation_tabs import tab_windows
    rows._tab_windows = tab_windows(ui)
    rows.scroll_start = saved.scroll if saved else 0
    marked = set(saved.marked_indices) if saved else set()
    rows.cursor = min(len(rows.query), saved.query_cursor) if saved and saved.query_cursor is not None else len(rows.query)
    rows.filtering = bool(rows.query)
    def visible():
        filter_fn = hooks.get("filter_fn")
        indices = filter_fn(rows.query, options) if filter_fn else list(range(len(options)))
        indices = [i for i in indices if i not in disabled]
        reverse = bool(hooks.get("reverse_items", True))
        if not rows.bottom:
            reverse = not reverse
        return list(reversed(indices)) if reverse else indices
    rows.visible = visible
    render_label = hooks.get("item_render_fn")
    if render_label is not None:
        for i, (_key, meta) in enumerate(rows.entries):
            meta["render_name"] = lambda selected, width, index=i: render_label(index, rows.query, selected, width)
    @contextmanager
    def phase(name):
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - started
            if elapsed >= .05:
                logging.getLogger(__name__).debug("Slow list stage: stage=%s elapsed_ms=%.1f", name, elapsed*1000)

    def render(width, height, _elapsed):
        live = hooks.get("live_fn")
        if live:
            with phase("refresh-callback"):
                live(rows.query)
        if hooks.get("exit_fn") and hooks["exit_fn"]():
            raise _Transition
        available = rows.visible()
        if available and rows.selected not in available:
            rows.selected = available[0]
        auto = hooks.get("auto_select_single_when_done")
        if auto and not rows.query and len(available) == 1 and (auto() if callable(auto) else auto):
            raise _AutomaticSelection(available[0])
        index = rows.selected if rows.selected in available else -1
        # Keep shared-session focus and cover ownership in the existing adapter.
        with phase("header-focus"):
            header_text = hooks["header_fn"](index)
        hint = hooks.get("hints")
        for i, (_key, meta) in enumerate(rows.entries):
            prefix = hooks.get("item_prefix_fn")
            meta["prefix"] = ("✔ " if i in marked else "") + (prefix(i) if callable(prefix) else "")
            meta["inline_hint"] = True
            meta["hint"] = hint(i) if i == index and callable(hint) else ""
        poster_fn = hooks.get("top_header_fn")
        with phase("cover"):
            poster = poster_fn(index) if poster_fn and index >= 0 else None
        status = (header_text.splitlines()[-1] if header_text else "") if presentation.get("status_from_header") else presentation.get("status_fn", lambda _i, _w: "")(index, width)
        filter_error_fn = hooks.get("filter_error_fn")
        filter_error = filter_error_fn(rows.query) if filter_error_fn else ""
        if filter_error:
            from ..core.terminal import sanitize_terminal_text
            status = role_prefix(MINT_SCREEN_THEME, "muted") + sanitize_terminal_text(filter_error) + RESET
        filter_hint_fn = hooks.get("filter_hint_fn")
        field_hint = filter_hint_fn(rows.query) if rows.filtering and filter_hint_fn else ""
        if field_hint:
            status = role_prefix(MINT_SCREEN_THEME, "muted") + field_hint + RESET
        confirmation = presentation.get("confirmation_question")
        if confirmation:
            question = confirmation.rstrip()
            if not question.lower().endswith("y/n"):
                question += " y/N"
            status = role_prefix(MINT_SCREEN_THEME, "warning") + question + RESET
        shortcuts = presentation.get("shortcuts_fn", lambda _i: (("enter", "select"), ("↑↓", "move"), ("tab", "sub/dub"), ("/", "filter"), ("?", "help"), ("esc", "back")))(index)
        title, badge, context = list_header_context(
            presentation.get("screen_title", "Downloads · Details"),
            presentation.get("context", "").split(" · "), badge=presentation.get("badge", ""),
        )
        columns_header = ""
        table_fn = presentation.get("table_fn")
        highlight_query = hooks.get("highlight_query_fn")
        paint_query = highlight_query(rows.query) if highlight_query else rows.query
        if table_fn:
            table, cells = table_fn(width)
            for i, (_key, meta) in enumerate(rows.entries):
                if i < len(cells):
                    meta["render_name"] = lambda _focus, _space, i=i: table.render(cells[i], theme=MINT_SCREEN_THEME, query=paint_query)
            columns_header = table.render({}, theme=MINT_SCREEN_THEME, header=True)
        summary_fn = presentation.get("summary_fn")
        if summary_fn:
            context = summary_fn()
        with phase("details-panel"):
            information = presentation["info_fn"](index, width) if index >= 0 else ()
        with phase("frame-layout"):
            return list_screen_frame(rows, title, width=width, height=height, columns_header=columns_header,
                                     badge=badge, poster=poster, reserve_cover=presentation.get("reserve_cover", False), tabs=presentation.get("tabs", hooks.get("navigation_tabs")), summary_context=context,
                                     info=information,
                                     cursor_visible=_elapsed < .7 or int((_elapsed - .7) * 2) % 2 == 0,
                                     completion=(hooks['filter_completion_fn'](rows.query)
                                                 if rows.cursor == len(rows.query) and hooks.get('filter_completion_fn') else ''),
                                     status=status, status_priority=bool(confirmation) or (not ((filter_error or field_hint) and rows.filtering) and (presentation["status_priority"]() if callable(presentation.get("status_priority")) else bool(presentation.get("status_priority")))), summary_noun=presentation.get("summary_noun", "actions"),
                                     placeholder=hooks.get("filter_placeholder") or presentation.get("placeholder", "Filter actions…"), empty=presentation.get("empty", "No matching actions"),
                                     shortcuts=shortcuts)
    def key(key):
        if key in ("CTRL_C", "EOF", ""):
            return -5
        if hooks.get("input_pending_fn") and hooks["input_pending_fn"]():
            handled = hooks["key_fn"](key, rows.selected) if hooks.get("key_fn") else False
            return -6 if handled and handled is not KeyResult.CONSUMED else None
        if rows.filtering and key in presentation.get("filter_passthrough", ()):
            handled = hooks["key_fn"](key, rows.selected) if hooks.get("key_fn") else False
            if handled:
                return None if handled is KeyResult.CONSUMED else -6
        if presentation.get("confirmation_question"):
            return rows.selected if key.lower() == "y" else -2
        if rows.filtering and key == "RIGHT" and rows.cursor == len(rows.query):
            complete = hooks.get("filter_completion_fn")
            suffix = complete(rows.query) if complete else ""
            if suffix:
                accept = hooks.get("filter_accept_fn")
                if accept:
                    rows.query = accept(rows.query)
                    rows.cursor = len(rows.query)
                else:
                    for char in suffix:
                        rows.key(char)
                return None
        adjust = hooks.get("adjust_fn")
        if key in ("LEFT", "RIGHT") and adjust is not None:
            boundary = not rows.filtering or (key == "LEFT" and rows.cursor == 0) or (key == "RIGHT" and rows.cursor == len(rows.query))
            if boundary and adjust(rows.selected, -1 if key == "LEFT" else 1):
                return -6
        if rows.filtering and (key in ("ESC", "LEFT", "RIGHT", "HOME", "END", "BACKSPACE", "DELETE", "CTRL_U") or (len(key) == 1 and key.isprintable())):
            rows.key(key)
            return None
        if not rows.filtering and key == "q":
            return -5
        if not rows.filtering:
            target = presentation.get("select_shortcuts", {}).get(key)
            if target is not None and target in rows.visible():
                rows.selected = target
                key = "ENTER"
            else:
                key = presentation.get("key_aliases", {}).get(key, key)
        if key == " " and hooks.get("multi_select") and not rows.filtering:
            if rows.selected in rows.visible():
                marked.symmetric_difference_update({rows.selected})
            return None
        if key in ("PAGE_UP", "PAGE_DOWN"):
            available = rows.visible()
            if available:
                position = available.index(rows.selected) if rows.selected in available else 0
                rows.selected = available[max(0, min(len(available) - 1, position + (-10 if key == "PAGE_UP" else 10)))]
            return None
        handled = hooks["key_fn"](key, rows.selected) if hooks.get("key_fn") else False
        if handled is KeyResult.CONSUMED:
            return None
        if handled:
            return -6
        if key == "?" and hooks.get("help_dict"):
            from .key_guide import list_key_groups, show_key_guide
            show_key_guide(list_key_groups(hooks["help_dict"], aliases=presentation.get("shortcut_help", ())))
            return KeyResult.CONSUMED
        if key == "RIGHT" and not rows.filtering:
            key = "ENTER"
        result = rows.key(key)
        if isinstance(result, int) and result >= 0 and hooks.get("select_fn") and not hooks["select_fn"](result):
            return None
        if key == "ENTER" and hooks.get("multi_select") and isinstance(result, int) and result >= 0:
            return sorted(marked) if marked else [result]
        return result
    try:
        if not hasattr(ui, '_list_frame_memory'):
            ui._list_frame_memory = {}
        result = run_framed_screen(render, key, frame_ready=hooks.get("frame_ready_fn"), batch_navigation=True,
                                   frame_memory=ui._list_frame_memory)
        if result is None:
            raise RendererUnavailable("No terminal for details")
        return result
    except _AutomaticSelection as selection:
        return selection.index
    except _Transition:
        return -6
    finally:
        if saved is not None:
            saved.selected_index = rows.selected
            saved.query = rows.query
            saved.query_cursor = rows.cursor
            saved.scroll = rows.scroll_start
            saved.marked_indices = marked
