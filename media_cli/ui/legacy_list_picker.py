"""Presentation-only bridge: legacy picker continues to own input and callbacks."""
import re

from ..core.terminal import sanitize_terminal_text, strip_ansi, truncate_display
from ..state.theme import MINT_SCREEN_THEME
from .list_screen import list_screen_frame
from .list_selection import ListSelection
from .screen_chrome import RESET, role_prefix, standard_screen_title, list_header_context
from .fuzzy import fuzzy_match, fuzzy_highlight


LEGACY_SHORTCUTS = (("enter", "select"), ("↑↓", "move"),
                    ("type", "filter"), ("esc", "back"))

_NAVIGATION = re.compile(
    r"^(?:(?:ctrl|shift|alt)[+\- ]*[\w?←→↑↓/]+|"
    r"enter|esc(?:ape)?|tab|backspace|delete|del|space|home|end|"
    r"page\s*(?:up|down)|f\d+|[?←→↑↓]|arrow(?:s| keys)?|up/down)"
    r"(?=$|[\s=:/(+\-])", re.IGNORECASE,
)
_STATUS = re.compile(
    r"\b(?:load(?:ing)?|searching|scanning|refreshing|fetching|waiting|"
    r"downloading|connecting|retrying|resolving|buffering|ready|"
    r"error|failed|failure|unavailable|warning|cancelled|canceled|"
    r"saved|deleted|removed|complete(?:d)?|status|no matches|no results|"
    r"press .* again|delete.*y/n)\b|[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]", re.IGNORECASE,
)


def split_legacy_footer(line, *, priority=False):
    """Separate recognizable controls from context without interpreting domains.

    Unrecognized standalone messages keep the legacy status behavior. Alongside
    controls, non-status segments are context for the shared summary instead.
    """
    plain = sanitize_terminal_text(strip_ansi(str(line)))
    if priority:
        return "", plain
    segments = [part.strip() for part in re.split(r"\s*[•·|│]\s*", plain) if part.strip()]
    has_controls = any(_NAVIGATION.match(part) for part in segments)
    context, statuses = [], []
    for part in segments:
        if _NAVIGATION.match(part):
            continue
        if _STATUS.search(part) or not has_controls:
            statuses.append(part)
        else:
            context.append(part)
    return " · ".join(context), " · ".join(statuses)


class LegacySelection(ListSelection):
    """Use the legacy picker's already filtered and ordered underlying indexes."""

    def visible(self):
        return self.order


def legacy_list_frame(title, options, visible, selected, *, width, height,
                      header="", query="", cursor=0, scroll=0, reverse=True,
                      poster=None, render_name=None, shortcuts=LEGACY_SHORTCUTS, priority=False,
                      tui_layout="bottom", action_hint="", chrome=None, tab_viewports=None):
    # Only the final header row carries legacy controls/context/status.
    # Earlier rows remain generic details without domain interpretation.
    details = str(header or "").split("\n")
    context, status = split_legacy_footer(details.pop() if details else "", priority=priority)
    # Legacy headers arrive styled, but are sanitized at this boundary. Restore
    # the semantic loading role rather than letting plain text inherit a color.
    if status and re.search(r"\b(?:loading|searching|scanning|refreshing|fetching|waiting|resolving|connecting|buffering|checking)\b", status, re.IGNORECASE):
        from .footer import Footer, FooterStatus, StatusKind, render_footer
        status = render_footer(Footer(statuses=(FooterStatus(status, StatusKind.LOADING),)), width, theme=MINT_SCREEN_THEME)
    state = LegacySelection(
        [(str(i), {"name": sanitize_terminal_text(str(label)),
                    "render_name": (lambda focus, space, i=i: render_name(i, space))
                    if render_name else None}) for i, label in enumerate(options)],
        "", selected=selected, query=query, cursor=cursor,
        filtering=bool(query), bottom=tui_layout == "bottom",
    )
    if tab_viewports is not None:
        state._tab_windows = tab_viewports
    if action_hint and 0 <= selected < len(state.entries):
        state.entries[selected][1].update(hint=sanitize_terminal_text(strip_ansi(str(action_hint))), inline_hint=True)
    state.order = list(reversed(visible)) if reverse else list(visible)
    # Legacy scroll counts from the bottom for reversed screens.
    from .library_info import finish_info_panel
    from .metadata_styles import legacy_metadata
    from .styled_text import render_styled_text
    plain = [sanitize_terminal_text(strip_ansi(line)) for line in details]
    roles = ('primary', 'secondary')
    info = [role_prefix(MINT_SCREEN_THEME, role) + line + RESET
            for role, line in zip(roles, plain[:2])]
    if len(plain) >= 3:
        info.append(render_styled_text(legacy_metadata(plain[2]), width=width, theme=MINT_SCREEN_THEME))
        info = list(finish_info_panel(info, plain[3] if len(plain) > 3 else '',
                                     plain[4] if len(plain) > 4 else context, width))
    info_count = (5 if len(info) >= 5 and height >= 26 else 4) if height >= 17 else (2 if height >= 12 else 1)
    from .covers import info_cover_size
    panel_height = max(info_count, info_cover_size()[1] if poster and width >= 60 and height >= 26 else 0)
    capacity = max(0, max(4, height - panel_height - 4) - 4)
    state.scroll_start = max(0, len(visible) - capacity - scroll) if reverse else scroll
    chrome = chrome or {}
    schema = chrome.get('filter_schema', ())
    completion = ''
    if schema:
        from types import SimpleNamespace
        from .smart_filter import compile_filter, filter_hint, field_completion, filter_placeholder, filter_value_options
        state.filtering = chrome.get('filter_editing', bool(query))
        shortcuts = tuple(('/', label) if key == 'type' else (key, label) for key, label in shortcuts)
        choices = filter_value_options((SimpleNamespace(filter_values=value) for value in chrome.get('filter_values', ())), schema)
        if state.filtering:
            hint = filter_hint(query, schema, choices) or compile_filter(query, schema).error
            status = role_prefix(MINT_SCREEN_THEME, 'muted') + hint + RESET if hint else ''
            completion = field_completion(query, schema, choices) if cursor == len(query) else ''
    if chrome.get("info_fn") and selected >= 0:
        info = chrome["info_fn"](selected, width)
    heading, badge, summary_context = list_header_context(
        chrome.get("title") or standard_screen_title(sanitize_terminal_text(str(title)), context.split(' · ')),
        () if chrome else context.split(' · '), badge=chrome.get("badge", ""),
    )
    frame = list_screen_frame(
        state, heading, width=width, height=height,
        info=info, status=status, shortcuts=chrome.get("shortcuts", shortcuts), poster=poster,
        reserve_cover=poster is not None,
        status_priority=priority, summary_context=summary_context, theme=MINT_SCREEN_THEME,
        badge=badge, tabs=chrome.get("tabs"),
        placeholder=filter_placeholder(schema) if schema else 'Filter titles…',
        completion=completion, cursor_visible=chrome.get('cursor_visible', True),
    )
    offset = max(0, len(visible) - capacity - state.scroll_start) if reverse else state.scroll_start
    return frame, offset, max(1, capacity)


def legacy_row(label, *, width, disabled=False, prefix="", marked=None, query=""):
    role = "muted" if disabled else "row_normal"
    mark = "" if marked is None else ("[✔] " if marked else "[ ] ")
    raw = str(label)
    plain = sanitize_terminal_text(strip_ansi(raw))
    source_style = re.match(r"(?:\x1b\[[0-9;]*m)+", raw)
    base = role_prefix(MINT_SCREEN_THEME, role)
    if source_style and not disabled:
        base = source_style.group(0)
    match = fuzzy_match(query, plain) if query else None
    rendered = fuzzy_highlight(plain, match[1] if match else set(), base_style=base,
                               match_style=role_prefix(MINT_SCREEN_THEME, "row_match"), reset_style=RESET)
    return RESET + truncate_display(prefix + mark + rendered, width) + RESET
