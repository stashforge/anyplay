"""UI Modal Dialogs and Interactive Picker Headers."""

from __future__ import annotations

import os
from typing import Any, Callable

from ..core.terminal import truncate_display
from ..domain.metadata import format_info_metadata_line
from ..domain.titles import get_display_titles, get_show_display_title
from .display import _get_poster, with_loading
from .panels import render_search_header
from .picker import tui_pick

C_TITLE = "\033[1;97m"
C_ALT = "\033[38;5;248m"
C_HINT = "\033[38;5;244m"
C_WARN = "\033[38;5;222m"
C_ACCENT = "\033[36m"
R = "\033[0m"


def _show_needs_enrichment(show: dict[str, Any] | None) -> bool:
    if not show or not isinstance(show, dict):
        return False
    if show.get("_enrichment_attempted"):
        return False
    if (
        not show.get("description")
        or not show.get("genres")
        or not (show.get("aniListId") or show.get("anilistMatch"))
    ):
        return True
    return False


def make_shows_info_fn(
    shows_getter: Callable[[], list[dict[str, Any]]],
    ui: Any,
    poster_manager: Any = None,
    enrich_fn: Any = None,
):
    """Return an ``info_fn`` for pickers bound to dynamic show lists."""

    def _info_fn(idx: int) -> None:
        from media_cli.ui.info_screen import show_info_screen

        shows = shows_getter()
        if not shows or not (0 <= idx < len(shows)):
            return
        target_show = shows[idx]
        if enrich_fn and _show_needs_enrichment(target_show):
            with_loading("Loading title info…", enrich_fn, target_show)
        show_info_screen(
            target_show,
            poster_manager=poster_manager,
            ui=ui,
        )

    return _info_fn


def make_single_show_info_fn(
    show: dict[str, Any] | None,
    ui: Any,
    poster_manager: Any = None,
    enrich_fn: Any = None,
):
    """Return an ``info_fn`` for pickers bound to a single show (e.g. details, episodes)."""

    def _info_fn(idx: int) -> None:
        if not show:
            return
        if enrich_fn and _show_needs_enrichment(show):
            with_loading("Loading title info…", enrich_fn, show)
        from media_cli.ui.info_screen import show_info_screen

        show_info_screen(
            show,
            poster_manager=poster_manager,
            ui=ui,
        )

    return _info_fn


def _match_header_card(
    show: dict[str, Any] | None,
    ttype: str,
    context_line: str,
    width: int,
    *,
    sync_enabled: bool | None = None,
    local_only: bool = False,
) -> str:
    show = show if isinstance(show, dict) else {}
    title = get_show_display_title(show, sync_enabled=sync_enabled)
    alt = get_display_titles(show, title) or "No alternative title"
    metadata = format_info_metadata_line(show, ttype, local_only=local_only)
    if not metadata:
        metadata = "No metadata"

    return "\n".join([
        f"{C_TITLE}{truncate_display(title, max(1, width - 1))}{R}",
        f"{C_ALT}{truncate_display(alt, max(1, width - 1))}{R}",
        f"{C_ALT}{truncate_display(metadata, max(1, width - 1))}{R}",
        truncate_display(context_line, max(1, width - 1)),
    ])


def search_input_header(
    source_name: str,
    esc_action: str = "quit",
    get_error_fn: Callable[[], str] | None = None,
):
    def _hdr(si: int) -> str:
        C_K = "\033[38;5;244m"
        R = "\033[0m"
        parts = [""]
        parts.append(f"{C_K}Use Up/Down to browse previous searches.{R}")
        parts.append(f"\033[38;5;250mProvider: \033[1;97m{source_name}{R}")
        err = get_error_fn() if get_error_fn else ""
        if err:
            parts.append(f"{C_K}{err}  │  Esc={esc_action}{R}")
        else:
            parts.append(f"{C_K}Enter=search  ? = Help  Esc={esc_action}{R}")
        return "\n".join(parts)

    return _hdr


def search_cover_header(
    get_results_fn: Callable[[], list[dict[str, Any]]], ui: Any = None
):
    def _hdr(si: int) -> str:
        shows = get_results_fn()
        if shows and 0 <= si < len(shows):
            if ui is not None:
                ui.hovered_show_id = shows[si].get("_id") or shows[si].get("id")
                ui.hovered_show_obj = shows[si]
            poster = _get_poster(shows[si])
            if poster:
                return poster
        return ""

    return _hdr


def search_result_header(
    source_name: str,
    query_str: str,
    ttype: str,
    get_results: Callable[[], list[dict[str, Any]]],
    get_loading: Callable[[], str],
    esc_action: str = "quit",
    get_error_fn: Callable[[], str] | None = None,
    filter_query: str = "",
    badges: list[str] | None = None,
):
    def _hdr(si: int) -> str:
        return render_search_header(
            source_name,
            query_str,
            ttype,
            get_results,
            get_loading,
            selected_idx=si,
            esc_action=esc_action,
            get_error_fn=get_error_fn,
            filter_query=filter_query,
            badges=badges,
        )

    return _hdr


def manual_match_input_header(
    anilist_show: dict[str, Any],
    target_provider_name: str = "Provider",
    error: str = "",
    p_name: str | None = None,
):
    provider_label = p_name or target_provider_name or "Provider"

    def _hdr(si: int) -> str:
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80
        if error:
            context = f"{C_WARN}{error}{R} • {provider_label} • Enter=search • Esc=cancel"
        else:
            context = f"{provider_label} • Enter=search • Esc=cancel"
        return _match_header_card(anilist_show, "sub", context, w, sync_enabled=True)

    return _hdr


def manual_anilist_input_header(show: dict[str, Any], error: str = ""):
    def _hdr(si: int) -> str:
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80
        if error:
            context = f"{C_WARN}{error}{R} • AniList • Enter=search • Esc=cancel"
        else:
            context = "AniList • Enter=search • Esc=cancel"
        return _match_header_card(show, "sub", context, w, sync_enabled=False)

    return _hdr


def select_provider_for_match(
    flags: Any,
    ui: Any,
    current_pid: str,
    anilist_show: dict[str, Any],
    ttype: str = "sub",
):
    from media_cli.providers import (
        available_providers,
        get_provider_registry,
        provider_display_name,
        provider_key,
    )

    provs = available_providers()
    if not provs:
        return current_pid

    current_key = provider_key(current_pid)
    ordered_keys = [current_key] if current_key in provs else []
    for k in provs.keys():
        if k not in ordered_keys:
            ordered_keys.append(k)

    opts = []
    for k in ordered_keys:
        p_name = provider_display_name(k)
        if k == current_key:
            opts.append(f"{p_name} (Current)")
        else:
            opts.append(p_name)

    curr_name = provider_display_name(current_key)
    def _hdr(idx: int) -> str:
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80

        hov_key = ordered_keys[idx] if 0 <= idx < len(ordered_keys) else current_key
        hov_name = provider_display_name(hov_key)
        hov_info = get_provider_registry().get(hov_key, {})
        ptype = str(hov_info.get("type") or "anime").title()
        langs = "/".join(lang.upper() for lang in hov_info.get("languages", ["sub"]))
        badge = f" [{ptype} • {langs}]" if ptype else ""
        context = (
            f"{C_WARN}Current: {curr_name}{R}  {C_HINT}│{R}  "
            f"{C_ACCENT}Target: {hov_name}{badge}{R}  "
            f"{C_HINT}Enter=select  Left/Esc=cancel{R}"
        )
        return _match_header_card(anilist_show, ttype, context, w, sync_enabled=True)

    idx = tui_pick(
        flags,
        ui,
        "Select Provider",
        opts,
        header_fn=_hdr,
        help_dict={"Enter": "Select provider", "Esc": "Cancel"},
    )
    if idx is None or idx < 0 or idx >= len(ordered_keys):
        return None
    return ordered_keys[idx]


def confirm_auto_match(
    flags: Any,
    ui: Any,
    current_pid: str,
    target_pid: str,
    anilist_show: dict[str, Any],
    matched_show: dict[str, Any],
    ttype: str = "sub",
):
    from media_cli.providers import get_provider_registry, provider_display_name

    opts = [
        f'Link "{matched_show.get("name") or matched_show.get("englishName") or "Matched Title"}"',
        "Search Manually",
        "Back to Providers",
    ]
    target_title = (
        matched_show.get("name")
        or matched_show.get("englishName")
        or "Unknown Stream Title"
    )

    def _hdr(idx: int) -> str:
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80

        curr_name = provider_display_name(current_pid)
        t_name = provider_display_name(target_pid)

        t_info = get_provider_registry().get(target_pid, {})
        langs = "/".join(
            lang.upper() for lang in t_info.get("languages", ["sub", "dub"])
        )
        avail_eps = matched_show.get("availableEpisodes", {}).get(ttype) or 0
        eps_badge = f"Avail {avail_eps} EPs • " if avail_eps else ""
        source = matched_show.get("_match_source")
        match_tag = (
            "✔ Matched by ID"
            if source == "id"
            else (
                "✔ Found saved match"
                if source in ("stored", "manual")
                else "✔ Suggested match"
            )
        )
        context = (
            f"{C_WARN}{curr_name}{R}  {C_HINT}→{R}  {C_ACCENT}{t_name}{R} "
            f"{C_HINT}• {match_tag} • {eps_badge}{langs}{R}"
        )
        return _match_header_card(matched_show, ttype, context, w, sync_enabled=False)

    hints = {"Link Title": target_title}
    idx = tui_pick(
        flags,
        ui,
        "Confirm Match",
        opts,
        header_fn=_hdr,
        hints=hints,
        help_dict={"Enter": "Select", "Esc": "Cancel"},
    )
    if idx is None or idx in (-2, -3) or idx == 2:
        return "back"
    elif idx == 1:
        return "search"
    elif idx == 0:
        return "link"
    return "back"


def confirm_auto_anilist_match(
    flags: Any, ui: Any, provider_show: dict[str, Any], anilist_show: dict[str, Any]
):
    al_romaji = (
        anilist_show.get("name") or anilist_show.get("englishName") or "Unknown Title"
    )
    al_id = str(anilist_show.get("_id") or anilist_show.get("id") or "")
    p_name = (
        provider_show.get("_provider_name")
        or (provider_show.get("_provider") or "").title()
    ) or "Provider"

    opts = [
        "Link Title",
        "Search Manually",
        "Back",
    ]

    def _hdr(idx: int) -> str:
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80

        id_badge = f" (ID: {al_id})" if al_id else ""
        source = anilist_show.get("_match_source")
        tag = "✔ Matched by ID" if source == "id" else "✔ Suggested match"
        context = (
            f"{C_WARN}{p_name}{R}  {C_HINT}→{R}  {C_ACCENT}AniList{id_badge}{R} "
            f"{C_HINT}• {tag}{R}"
        )
        return _match_header_card(anilist_show, "sub", context, w, sync_enabled=True)

    hints = {"Link Title": al_romaji}
    idx = tui_pick(
        flags,
        ui,
        "Confirm Match",
        opts,
        header_fn=_hdr,
        hints=hints,
        help_dict={"Enter": "Select", "Esc": "Cancel"},
    )
    if idx is None or idx in (-2, -3) or idx == 2:
        return "back"
    elif idx == 1:
        return "search"
    elif idx == 0:
        return "link"
    return "back"


def no_match_prompt(
    flags: Any,
    ui: Any,
    current_pid: str,
    target_pid: str,
    anilist_show: dict[str, Any],
    ttype: str = "sub",
):
    from media_cli.providers import provider_display_name

    opts = [
        "Search Manually",
        "Back to Providers",
    ]

    def _hdr(idx: int) -> str:
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80

        curr_name = provider_display_name(current_pid)
        t_name = provider_display_name(target_pid)
        context = (
            f"{C_WARN}{curr_name}{R}  {C_HINT}→{R}  {C_ACCENT}{t_name}{R} "
            f"{C_HINT}• No match found • Search manually or go back{R}"
        )
        return _match_header_card(anilist_show, ttype, context, w, sync_enabled=True)

    idx = tui_pick(
        flags,
        ui,
        "Select Option",
        opts,
        header_fn=_hdr,
        help_dict={"Enter": "Select", "Esc": "Back"},
    )
    if idx is None or idx in (-2, -3) or idx == 1:
        return "back"
    elif idx == 0:
        return "search"
    return "back"
