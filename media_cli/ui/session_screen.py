"""Shared chrome for standard structured sessions; domain events stay with callers."""
from .action_picker import action_picker
from .detail_panel import render_detail_lines


def session_screen_picker(flags, ui, prompt, options, *, session, view_presentation, **hooks):
    # Query-entry forms have their own input layout and cursor semantics.
    if session.current.selection_policy.query_input:
        from .picker import terminal_pick
        return terminal_pick(flags, ui, prompt, options, **hooks)

    def selected(index):
        items = session.current.items
        return items[index] if 0 <= index < len(items) else None

    information_cache = {}

    def information(index, width):
        item = selected(index)
        if item is None:
            return ()
        view = session.current
        signature = (width, item, view.info_lines, view.details, view.extra_details, view.info_context, view_presentation.theme)
        cached = information_cache.get(item.id)
        if cached is not None and cached[0] == signature:
            return cached[1]
        lines = uncached_information(index, width)
        information_cache[item.id] = (signature, lines)
        return lines

    def uncached_information(index, width):
        item = selected(index)
        if item is None:
            return ()
        if item.info_lines or session.current.info_lines:
            from .styled_text import render_styled_text
            return tuple(render_styled_text(line, width=width, theme=view_presentation.theme) for line in (item.info_lines or session.current.info_lines))
        fields = view_presentation._details(item.id)
        lines = render_detail_lines(primary=fields.title, secondary=fields.secondary,
                                    details=fields.metadata, width=width, theme=view_presentation.theme)
        from .library_info import finish_info_panel
        extra = item.extra_details or session.current.extra_details
        parts = item.footer_context or ((session.current.info_context,) if session.current.info_context else ())
        context = " · ".join((*parts, *((item.activity,) if item.activity else ())))
        if extra or context:
            return finish_info_panel(lines, extra, context, width, theme=view_presentation.theme)
        return lines

    def shortcuts(index):
        item = selected(index)
        footer = view_presentation._footer(item_id=item.id if item else None)
        result = []
        for action in footer.actions:
            key, separator, label = action.partition("=")
            if separator:
                result.append((key.lower(), label))
        if session.current.selection_policy.multiple:
            result.append(("space", "mark"))
        result.insert(1, ("↑↓", "move"))
        result.insert(2, ("/", "filter"))
        if session.current.help and not any(key == "?" for key, _label in result):
            result.insert(max(0, len(result) - 1), ("?", "help"))
        return tuple((key, label) for key, label in result
                     if key in {"enter/right", "enter", "↑↓", "/", "tab", "tab/ctrl+n", "?", "esc/left", "esc", "space"})

    def feedback_priority():
        from .footer import StatusKind
        current = session.current
        statuses = tuple(current.footer_statuses) + (current.footer.statuses if current.footer else ())
        return session.navigation is not None or any(status.kind is not StatusKind.LOADING for status in statuses)

    view = session.current
    from .screen_chrome import standard_screen_title, list_header_context
    key_aliases, shortcut_help = {}, []
    for action in view.actions:
        alias = {"information": "i", "refresh_title": "r", "refresh": "r",
                 "refresh_view": "R", "flip": "o"}.get(action.id)
        if alias and action.keys:
            key_aliases[alias] = action.keys[0]
            shortcut_help.append((alias, action.label))
    select_shortcuts = {}
    for index, item in enumerate(view.items):
        if not item.id.startswith("action:") or not item.selectable:
            continue
        alias = {"episodes": "e", "songs": "e", "mirrors": "m", "quality": "m",
                 "download_menu": "d", "download": "d"}.get(str(item.reference).lower())
        if alias:
            select_shortcuts[alias] = index
            shortcut_help.append((alias, item.label_text))
    active = session.position.active_tab_id or view.active_tab_id
    from .navigation_tabs import NavigationTabs
    tabs = NavigationTabs(tuple(view.tabs), active) if view.tabs else None
    title, badge, context = list_header_context(
        view.screen_title or standard_screen_title(view.prompt if view.tabs else str(prompt), view.context),
        view.context, badge=view.screen_badge,
    )
    screen = dict(
        layout=getattr(ui, "tui_layout", "bottom"), screen_title=title, badge=badge,
        summary_noun="results", context=context, tabs=tabs,
        info_fn=information, shortcuts_fn=shortcuts,
        reserve_cover=bool(flags.show_image and (view.image is not None or any(item.image is not None for item in view.items))),
        key_aliases=key_aliases, select_shortcuts=select_shortcuts, shortcut_help=tuple(shortcut_help),
        filter_passthrough=("DELETE",) if any("DELETE" in action.keys and action.id == "delete" for action in view.actions) else (),
        status_from_header=True, status_priority=feedback_priority, placeholder="Filter…",
        empty="No matching results" if view.state in {"ready", "empty"} else "",
    )
    return action_picker(flags, ui, prompt, options, presentation=screen, **hooks)
