"""UI Presentation Header Cards and Panels.

Enforces the standardized 4-line Header Section layout contract:
  Line 1: Primary Title Line (title_line)
  Line 2: Alternative / Subtitle Line (alt_title_line)
  Line 3: Metadata Badge Line (metadata_line)
  Line 4: Navigation / Footer Line (footer_line)
"""

from __future__ import annotations

import os
from typing import Any, Callable

from ..core.terminal import (
    sanitize_terminal_text,
    truncate_display,
)
from ..domain.metadata import format_info_metadata_line
from ..domain.titles import get_display_titles, get_show_display_title
from ..state.preferences import get_active_feedback
from .display import _poster_footer_line

# Styling constants
C_TITLE = "[1;97m"         # Bold White
C_ALT = "[38;5;248m"          # Dim / Light Grey
C_META = "[38;5;248m"         # Muted Grey for metadata
C_HINT = "[38;5;244m"         # Dark Grey hint
C_WARN = "[38;5;222m"         # Soft Amber / Toast
C_ERR = "[38;5;196m"          # Bright Red
C_CYAN = "[36m"               # Cyan
C_RESET = "[0m"               # Reset ANSI


def _t(text: str, width: int) -> str:
    return truncate_display(text or "", max(1, width - 1))


def render_header_card(
    show: dict[str, Any] | None,
    ttype: str = "sub",
    width: int = 80,
    footer_text: str | None = None,
    *,
    override_ep_str: str | None = None,
    main_title: str | None = None,
    local_only: bool = False,
    hide_anilist_status: str | None = None,
) -> list[str]:
    if not isinstance(show, dict) or not show:
        line1 = ""
        line2 = f"{C_HINT}No selection{C_RESET}"
        line3 = ""
        line4 = _poster_footer_line({}, footer_text or "", width) if footer_text is not None else ""
        return [line1, line2, line3, line4]

    if "_local_progress" not in show and not local_only:
        from ..domain.metadata import prepare_show_display_state
        prepare_show_display_state(show, ttype)

    # Line 1: Primary Title
    title = main_title if main_title else get_show_display_title(show)
    line1 = f"{C_TITLE}{_t(title, width)}{C_RESET}"

    # Line 2: Alternative Title (or Genres fallback)
    alt = get_display_titles(show, title)
    if not alt:
        genres = show.get("genres") or show.get("_provider_genres")
        if isinstance(genres, list) and genres:
            alt = " • ".join(str(g) for g in genres if g)
        elif isinstance(genres, str) and genres.strip():
            alt = genres.strip().replace(", ", " • ")
    line2 = f"{C_ALT}{_t(alt, width)}{C_RESET}" if alt else f"{C_ALT}No alternative title{C_RESET}"

    # Line 3: Metadata Badges
    meta_str = format_info_metadata_line(
        show,
        ttype,
        override_ep_str=override_ep_str,
        local_only=local_only,
        hide_anilist_status=hide_anilist_status,
    )
    line3 = f"{C_META}{_t(meta_str, width)}{C_RESET}"

    # Line 4: Navigation / Footer Line
    if footer_text is not None:
        line4 = _poster_footer_line(show, footer_text, width)
    else:
        line4 = ""

    return [line1, line2, line3, line4]


def render_search_header(
    source_name: str,
    base_query: str,
    ttype: str,
    get_results_fn: Callable[[], list[dict[str, Any]]],
    get_loading_fn: Callable[[], str],
    selected_idx: int = 0,
    esc_action: str = "quit",
    get_error_fn: Callable[[], str] | None = None,
    filter_query: str = "",
    badges: list[str] | None = None,
) -> str:
    try:
        width = os.get_terminal_size().columns
    except OSError:
        width = 80

    safe_query = sanitize_terminal_text(base_query)
    clean_filter = sanitize_terminal_text(filter_query)
    shows = get_results_fn()
    loading_msg = get_loading_fn()

    # Case A: Active results available
    if shows and 0 <= selected_idx < len(shows):
        selected_show = shows[selected_idx]
        badge_list = list(badges or [])
        short_query = f'"{truncate_display(safe_query, 16)}"' if safe_query else ""
        feedback = get_active_feedback(selected_show)
        if not feedback and get_error_fn:
            feedback = get_error_fn()
        if feedback:
            items = [p for p in ["Enter=select", "?=Help", "Left=search", f"Esc={esc_action}"] if p]
            footer_nav = f"{C_WARN}* {feedback}{C_RESET}  │  {' • '.join(items)}"
        else:
            items = [p for p in [source_name, short_query, *badge_list, "Enter=select", "?=Help", "Left=search", f"Esc={esc_action}"] if p]
            footer_nav = " • ".join(items)
            if loading_msg:
                footer_nav = f"{loading_msg}  │  {footer_nav}"
        lines = render_header_card(
            selected_show,
            ttype=ttype,
            width=width,
            footer_text=footer_nav,
            main_title=selected_show.get("name"),
        )
        return chr(10).join(lines)

    # Case B: Live search in progress with no results yet
    if loading_msg:
        line1 = ""
        line2 = f"{C_HINT}Use Up/Down to browse previous searches.{C_RESET}"
        line3 = f"\033[38;5;250mProvider: \033[1;97m{source_name}{C_RESET}" if source_name else ""
        line4 = loading_msg
        return chr(10).join([line1, line2, line3, line4])

    # Case C: No matches found / Filter query mismatch
    line1 = ""
    if shows and clean_filter:
        line2 = f"{C_HINT}No match: {_t(clean_filter, width - 11)}{C_RESET}"
    else:
        line2 = ""
    line3 = f"\033[38;5;250mProvider: \033[1;97m{source_name}{C_RESET}" if source_name else ""

    if shows:
        badge_list = list(badges or [])
        short_query = f'"{truncate_display(safe_query, 16)}"' if safe_query else ""
        items = [p for p in [source_name, short_query, *badge_list, "?=Help", "Left=search", f"Esc={esc_action}"] if p]
        line4 = f"{C_HINT}{' • '.join(items)}{C_RESET}"
    else:
        err_msg = get_error_fn() if get_error_fn else ""
        if err_msg:
            line4 = f"{C_ERR}{err_msg}{C_RESET} • Left=new search • Esc={esc_action}"
        else:
            line4 = f'{C_HINT}No results for "{truncate_display(safe_query, 20)}"{C_RESET} • Left=new search • Esc={esc_action}'

    return chr(10).join([line1, line2, line3, line4])



def render_modal_card(
    primary_title: str,
    secondary_text: str,
    transition_line: str,
    status_line: str,
    width: int = 80,
) -> str:
    line1 = f"{C_TITLE}{_t(primary_title, width)}{C_RESET}"
    line2 = f"{C_ALT}{_t(secondary_text, width)}{C_RESET}" if secondary_text else ""
    line3 = f"{_t(transition_line, width)}"
    line4 = f"{_t(status_line, width)}"

    return chr(10).join([line1, line2, line3, line4])


def render_menu_card(
    subtitle: str,
    description: str,
    shortcuts: str,
    width: int = 80,
) -> str:
    line1 = ""
    line2 = f"{C_ALT}{_t(subtitle, width)}{C_RESET}"
    line3 = f"{C_HINT}{_t(description, width)}{C_RESET}"
    line4 = f"{C_HINT}{_t(shortcuts, width)}{C_RESET}"

    return chr(10).join([line1, line2, line3, line4])
