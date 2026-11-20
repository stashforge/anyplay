"""Shared fixed header, anchored rows, selection information and status layout."""
from dataclasses import dataclass

from ..core.terminal import display_width, sanitize_terminal_text, sanitize_input_text, truncate_display
from ..state.theme import MINT_SCREEN_THEME
from .covers import compact_cover, info_cover_size, placeholder_cover, poster_symbol_lines, poster_uses_native_protocol
from .screen_chrome import RESET, role_prefix, shortcut_footer, screen_header
from .row_text import render_row_text
from .styled_text import StyledText, TextRun, render_styled_text


@dataclass
class ScreenFrame:
    lines: list[str]
    poster: str = ""
    poster_row: int = 0
    poster_column: int = 0
    poster_width: int = 12
    poster_height: int = 8


def list_column_label(title):
    text = title.casefold()
    for words, label in ((('files', 'select file'), 'file'), (('episode', 'progress'), 'episode'), (('provider',), 'provider'),
                         (('artist',), 'artist'), (('studio',), 'studio'), (('playlist',), 'playlist'),
                         (('song', ' · theme'), 'song'), (('version',), 'version'),
                         (('quality', 'mirror'), 'quality'), (('details', 'playback', 'settings'), 'action')):
        if any(word in text for word in words):
            return label
    return 'title'


def list_screen_frame(state, title, *, width, height, info=(), status="", badge="", shortcuts=(),
                      placeholder="Filter…", cursor_visible=True, completion="", poster=None, reserve_cover=False, summary_context="", status_priority=False, summary_noun="results", empty="No matching results", theme=MINT_SCREEN_THEME, tabs=None, columns_header=""):
    lines = [""] * max(1, height)
    limit = max(1, width - 1)
    def style(role):
        return role_prefix(theme, role)
    def put(row, text):
        if 0 <= row < height - 2:
            lines[row] = truncate_display(text, limit)
    heading, divider = screen_header(title, width=limit, badge=badge)
    put(0, render_styled_text(heading, width=width, theme=theme))
    put(1, render_styled_text(divider, width=width, theme=theme))
    visible = state.visible()
    result_count = getattr(state, "result_count", len(visible))
    result_total = getattr(state, "result_total", len(state.entries))
    summary = f"{result_count}/{result_total} {summary_noun}" if state.filtering else f"{result_count} {summary_noun}"
    styled_context = summary_context if isinstance(summary_context, StyledText) else None
    context = sanitize_terminal_text(styled_context.text if styled_context else summary_context)
    # Reserve three count digits without moving the count away from column zero.
    count_slot = f"999/999 {summary_noun}" if state.filtering else f"999 {summary_noun}"
    summary_width = max(display_width(summary), display_width(count_slot))
    summary_line = style("counter") + summary + RESET
    if context or (tabs is not None and tabs.items):
        summary_line += " " * (summary_width - display_width(summary))
    if tabs is not None and tabs.items:
        budget = summary_width + 3 + (display_width(context) + 3 if context else 0)
        available = limit - budget
        if available < 10 and context:
            context = ""
            budget = summary_width + 3
            available = limit - budget
        if available < 10:
            summary_line = ""
            budget, available = 0, limit
        from .navigation_tabs import tab_windows
        windows = tab_windows(state)
        viewport = windows.setdefault((title, tuple(item.id for item in tabs.items)), {})
        labels = tabs.window_content(max(1, available), viewport=viewport)
        if context:
            summary_line += style("muted") + " · " + context + RESET
        if summary_line:
            summary_line += style("muted") + " · " + RESET
        summary_line += render_styled_text(labels, width=max(1, available), theme=theme)
    elif context:
        summary_line += style("muted") + " · " + RESET
        summary_line += render_styled_text(styled_context, width=max(1, limit-summary_width-3), theme=theme) if styled_context else style("muted") + context + RESET
    put(2, summary_line)
    if not state.bottom:
        put(3, '  ' + (columns_header or style('screen.column') + list_column_label(title) + RESET))
    poster_width, cover_rows = info_cover_size()
    if poster:
        poster = compact_cover(poster) or ("" if poster_uses_native_protocol(poster) else poster)
    has_cover_slot = bool(poster or reserve_cover) and width >= 60 and height >= 26 and visible and state.selected in visible
    if has_cover_slot and not poster:
        poster = placeholder_cover()
    info_count = (5 if len(info) >= 5 and height >= 26 else 4) if height >= 17 else (2 if height >= 12 else 1)
    # Keep the selected title's cover and information together in one panel.
    poster_height = cover_rows if has_cover_slot else 0
    panel_height = max(info_count, poster_height)
    info_start = max(4, height - panel_height - 4)
    region = max(0, info_start - 4)
    capacity = region
    pos = visible.index(state.selected) if state.selected in visible else 0
    start = min(max(0, state.scroll_start), max(0, len(visible) - capacity))
    if capacity and pos < start:
        start = pos
    elif capacity and pos >= start + capacity:
        start = pos - capacity + 1
    state.scroll_start = start
    shown = visible[start:start + capacity]
    rows_top = info_start - len(shown) if state.bottom else 4
    if columns_header and state.bottom:
        put(rows_top - 1, "  " + columns_header)
    poster_row = info_start + 1
    native = ""
    if poster_height:
        if poster_uses_native_protocol(poster):
            native = poster
        else:
            for offset, line in enumerate(poster_symbol_lines(poster, poster_height, width)):
                put(poster_row + offset, truncate_display(line, poster_width))
    name_width = max((display_width(sanitize_terminal_text(meta.get("name", key))) for key, meta in state.entries), default=0)
    for offset, index in enumerate(shown):
        key, meta = state.entries[index]
        if meta.get("heading"):
            put(rows_top + offset, '  ' + style('muted') + sanitize_terminal_text(meta['name']) + RESET)
            continue
        name = sanitize_terminal_text(meta.get("name", key))
        hint = sanitize_terminal_text(meta.get("hint", ""))
        pointer = style("prompt") + "❯ " + RESET if index == state.selected else "  "
        name_role = "row_selected" if index == state.selected and theme.row_selected != theme.row_normal else "row_normal"
        renderer = meta.get("render_name")
        rendered = renderer(index == state.selected and theme.row_selected != theme.row_normal, max(1, limit - 2)) if callable(renderer) else None
        name_text = (style("success") + meta["prefix"] + RESET if meta.get("prefix") else "") + (rendered if rendered is not None else render_row_text(StyledText((TextRun(name, name_role),)), columns=max(1, limit - 2), query=state.query, theme=theme))
        padding = " " if meta.get("inline_hint") else " " * max(2, name_width - display_width(name) + 2)
        put(rows_top + offset, RESET + pointer + name_text + RESET + (style("muted") + padding + hint + RESET if hint else ""))
    put(info_start, style("separator") + "─" * limit + RESET)
    if visible and state.selected in visible:
        for offset, line in enumerate(info[:info_count]):
            indent = poster_width + 1 if poster_height else 0
            row = info_start + 1 + offset
            content = " " * indent + truncate_display(line, max(1, limit - indent))
            # Symbol covers share the row; native covers overlay the reserved area.
            if poster_height and not native:
                cover = lines[row]
                content = cover + " " * max(0, indent - display_width(cover)) + truncate_display(line, max(1, limit - indent))
            put(row, content)
    else:
        put(info_start + 1, style("secondary") + empty + RESET)
    if height >= 2:
        query = None
        if state.filtering:
            text = sanitize_input_text(state.query) if state.query else placeholder
            cursor = min(state.cursor, len(text)) if state.query else 0
            before, under, after = text[:cursor], text[cursor:cursor + 1] or " ", text[cursor + 1:]
            if completion and cursor == len(text):
                under, after = completion[0], completion[1:]
            block = "\033[7m" + under + "\033[27m" if cursor_visible else under
            query = (style("muted") if not state.query else "") + before
            query += (style("muted") if completion and cursor == len(text) else "") + block + after + RESET
        lines[-2:] = shortcut_footer(width, shortcuts, query=query, message=status, message_priority=status_priority, theme=theme)
    return ScreenFrame(lines, native, poster_row + 1, 0, poster_width, poster_height)
