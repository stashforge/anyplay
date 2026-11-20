"""Downloads adapter: existing catalogue/actions with the shared list screen."""
from types import SimpleNamespace
from .smart_filter import compile_filter, filter_hint, field_completion, accept_field_completion, filter_placeholder, filter_value_options
from .input_result import KeyResult
from .list_selection import ListSelection
from .list_screen import list_screen_frame
from .screen_chrome import RESET, role_prefix, run_framed_screen
from ..state.theme import MINT_SCREEN_THEME


def run_library_picker(flags, ui, options, *, presentation, live_fn, header_fn, top_header_fn,
                       select_fn, delete_fn, can_delete_fn, info_fn, item_key_fn, delete_prompt):
    state = ListSelection([(str(i), {"name": name}) for i, name in enumerate(options)], "", bottom=presentation.get("layout") != "top")
    confirming = False
    ready = False
    status_override = ""
    theme = MINT_SCREEN_THEME
    schema = presentation.get("filter_schema", ())
    value_options = {}
    options_snapshot = None
    def visible():
        if not schema:
            return ListSelection.visible(state)
        plan = compile_filter(state.query, schema)
        return [i for i, (_key, meta) in enumerate(state.entries)
                if plan.matches(meta.get('name', ''), dict(meta.get('filter_values', ())))]
    state.visible = visible

    def entries_for(rows):
        structured = presentation.get("rows_fn")
        metadata = structured() if structured else [{"name": name} for name in rows]
        nonlocal value_options, options_snapshot
        snapshot = tuple(meta.get('filter_values', ()) for meta in metadata) if schema else None
        if schema and snapshot != options_snapshot:
            value_options = filter_value_options((SimpleNamespace(filter_values=values) for values in snapshot), schema)
            options_snapshot = snapshot
        return [(str(item_key_fn(index)), meta) for index, meta in enumerate(metadata)]

    def refresh():
        nonlocal ready
        selected_id = state.entries[state.selected][0] if state.selected < len(state.entries) else None
        rows, _message, ready = live_fn(state.query)
        state.entries = entries_for(rows)
        state.selected = next((i for i, (key, _) in enumerate(state.entries) if key == selected_id), min(state.selected, max(0, len(rows) - 1)))
        visible = state.visible()
        if visible and state.selected not in visible:
            state.selected = visible[0]

    # Resolve real identities before the first worker update can reorder rows.
    state.entries = entries_for(options)
    saved = getattr(ui, "downloads_selection", None)
    if saved is not None:
        selected_id, state.query, state.filtering, state.cursor = saved
        state.selected = next((i for i, (key, _) in enumerate(state.entries) if key == selected_id), 0)

    def render(width, height, _elapsed):
        refresh()
        index = state.selected if state.selected in state.visible() else -1
        if index >= 0 and presentation.get("info_fn"):
            details = presentation["info_fn"](index, width)
        else:
            details = header_fn(index).splitlines()[:-1] if index >= 0 else []
        status = presentation["status_fn"](index, width)
        if state.filtering and schema:
            hint = filter_hint(state.query, schema, value_options) or compile_filter(state.query, schema).error
            status = role_prefix(theme, 'muted') + hint + RESET if hint else ''
        if confirming:
            question = delete_prompt.rstrip()
            if not question.lower().endswith("y/n"):
                question += " y/N"
            status = role_prefix(theme, "warning") + question + RESET
        elif status_override:
            status = role_prefix(theme, "info") + status_override + RESET
        poster = top_header_fn(index) if flags.show_image and index >= 0 else None
        return list_screen_frame(state, "Downloads", width=width, height=height, info=details,
                                 status=status, status_priority=confirming or bool(status_override), badge="Local library", poster=poster, reserve_cover=bool(flags.show_image), summary_context=presentation.get("context", ""),
                                 cursor_visible=_elapsed < .7 or int((_elapsed - .7) * 2) % 2 == 0,
                                 completion=field_completion(state.query, schema, value_options) if state.cursor == len(state.query) else '',
                                 placeholder=filter_placeholder(schema) if schema else "Filter downloaded titles…", empty="No matching downloaded titles" if state.filtering else ("No downloaded titles" if ready else "Checking downloaded files…"),
                                 shortcuts=(("enter", "open"), ("↑↓", "move"), ("/", "filter"), ("del", "delete"), ("?", "help"), ("esc", "back")))

    def key(key):
        nonlocal confirming, status_override
        if key in ("CTRL_C", "EOF", ""):
            return -5
        if confirming:
            confirming = False
            if key.lower() == "y" and can_delete_fn(state.selected):
                delete_fn(state.selected)
            return KeyResult.CONSUMED
        status_override = ""
        if state.filtering and schema and key == 'RIGHT' and state.cursor == len(state.query):
            completed = accept_field_completion(state.query, schema, value_options)
            if completed != state.query:
                state.query, state.cursor = completed, len(completed)
                return None
        if key == "ENTER" and state.selected in state.visible() and not select_fn(state.selected):
            status_override = "Still checking downloaded files…"
            return None
        if key == "DELETE" and not state.filtering:
            if state.selected in state.visible() and can_delete_fn(state.selected):
                confirming = True
            return None
        if key == "?" and not state.filtering:
            from .key_guide import list_key_groups, show_key_guide
            show_key_guide(list_key_groups({"Enter": "Open downloaded title", "↑ / ↓": "Move selection",
                "Esc": "Clear filter or return", "Delete": "Delete title after confirmation",
                "i / Ctrl+O": "Title information",
                **({"Filters": 'provider:anipm,anikoto downloaded:>1 genre:"Martial Arts"',
                    "Fields": ', '.join(field.name for field in schema),
                    "Combine": 'Space/and combines conditions; commas select text alternatives; Right accepts completion'} if schema else {})}))
            return KeyResult.CONSUMED
        if key == "CTRL_O" or (key == "i" and not state.filtering):
            if info_fn and state.selected in state.visible():
                info_fn(state.selected)
                return KeyResult.CONSUMED
            return None
        return state.key(key)

    try:
        return run_framed_screen(render, key)
    finally:
        selected_id = state.entries[state.selected][0] if state.selected < len(state.entries) else None
        ui.downloads_selection = (selected_id, state.query, state.filtering, state.cursor)
