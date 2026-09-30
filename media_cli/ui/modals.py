"""UI Modal Dialogs and Interactive Picker Headers."""

from __future__ import annotations

import os
from typing import Any, Callable

from ..core.terminal import truncate_display
from .display import _get_poster, _poster_footer_line, with_loading
from .panels import render_modal_card, render_search_header
from .picker import tui_pick


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
        C_K = "\033[38;5;244m"
        C_T = "\033[1;97m"
        R = "\033[0m"
        anilist_title = (
            (anilist_show or {}).get("name")
            or (anilist_show or {}).get("englishName")
            or ""
        )
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80
        parts = [
            "",
            f"{C_K}Search {provider_label} for:{R}",
            f"{C_T}{truncate_display(anilist_title, w)}{R}",
        ]
        footer_text = f"{error} • Esc=cancel" if error else "Enter=search • Esc=cancel"
        parts.append(_poster_footer_line(anilist_show, footer_text, w))
        return "\n".join(parts)

    return _hdr


def manual_anilist_input_header(show: dict[str, Any], error: str = ""):
    def _hdr(si: int) -> str:
        C_K = "\033[38;5;244m"
        C_T = "\033[1;97m"
        R = "\033[0m"
        source_title = (
            show.get("_allanime_name") or show.get("name") or ""
            if isinstance(show, dict)
            else str(show or "")
        )
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80
        parts = [
            "",
            f"{C_K}Search AniList and select tracking title.{R}",
            f"{C_T}{truncate_display(source_title, w)}{R}",
        ]
        footer_text = (
            f"\033[38;5;222m{error}\033[0m • Enter=search • Esc=cancel"
            if error
            else "Enter=search • Esc=cancel"
        )
        parts.append(_poster_footer_line(show, footer_text, w))
        return "\n".join(parts)

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
    al_title = (
        (anilist_show or {}).get("name")
        or (anilist_show or {}).get("englishName")
        or "Anime Title"
    )

    def _hdr(idx: int) -> str:
        C_T = "\033[1;97m"
        C_K = "\033[38;5;244m"
        C_Y = "\033[38;5;222m"
        C_C = "\033[36m"
        R = "\033[0m"

        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80

        line1 = f"{C_T}{truncate_display(al_title, w)}{R}"
        line2 = f"{C_K}Switch provider stream match:{R}"

        hov_key = ordered_keys[idx] if 0 <= idx < len(ordered_keys) else current_key
        hov_name = provider_display_name(hov_key)
        hov_info = get_provider_registry().get(hov_key, {})
        ptype = str(hov_info.get("type") or "anime").title()
        langs = "/".join(lang.upper() for lang in hov_info.get("languages", ["sub"]))
        badge = f" [{ptype} • {langs}]" if ptype else ""
        line3 = f"{C_Y}Current: {curr_name}{R}  {C_K}│{R}  {C_C}Target: {hov_name}{badge}{R}"
        line4 = f"{C_K}Enter=select  Left/Esc=cancel{R}"

        return f"{line1}\n{line2}\n{line3}\n{line4}"

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
    al_title = anilist_show.get("name") or anilist_show.get("englishName") or ""
    al_eng = anilist_show.get("englishName")

    def _hdr(idx: int) -> str:
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80

        if al_eng and al_eng.strip().lower() != (target_title or "").strip().lower():
            secondary = al_eng
        else:
            secondary = (
                f"AniList: {al_title}"
                if al_title and al_title.lower() != target_title.lower()
                else ""
            )

        curr_name = provider_display_name(current_pid)
        t_name = provider_display_name(target_pid)
        trans_line = f"\033[38;5;222m{curr_name}\033[0m  \033[38;5;244m➔\033[0m  \033[36m{t_name}\033[0m"

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
        status_line = f"\033[38;5;250m{match_tag} • {eps_badge}{langs}\033[0m"

        return render_modal_card(
            target_title, secondary, trans_line, status_line, width=w
        )

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
    al_eng = anilist_show.get("englishName")
    al_id = str(anilist_show.get("_id") or anilist_show.get("id") or "")
    p_title = provider_show.get("name") or ""
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

        if al_eng and al_eng.strip().lower() != al_romaji.strip().lower():
            secondary = al_eng
        elif p_title and p_title.lower() != al_romaji.lower():
            secondary = f"Source: {p_title}"
        else:
            secondary = ""

        id_badge = f" (ID: {al_id})" if al_id else ""
        trans_line = f"\033[38;5;222m{p_name}\033[0m  \033[38;5;244m➔\033[0m  \033[36mAniList{id_badge}\033[0m"

        al_status = str(anilist_show.get("status") or "").title() or "Finished"
        al_fmt = str(
            anilist_show.get("format") or anilist_show.get("type") or "TV"
        ).upper()
        al_eps = anilist_show.get("episodeCount")
        eps_str = f" • EP {al_eps}" if al_eps else ""
        al_score = anilist_show.get("score") or anilist_show.get("averageScore")
        score_str = f" • ★ {al_score}" if al_score else ""
        source = anilist_show.get("_match_source")
        tag = "✔ Matched by ID" if source == "id" else "✔ Suggested match"
        status_line = (
            f"\033[38;5;250m{tag} • {al_status} • {al_fmt}{eps_str}{score_str}\033[0m"
        )

        return render_modal_card(al_romaji, secondary, trans_line, status_line, width=w)

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

        t_romaji = (
            anilist_show.get("name")
            or anilist_show.get("englishName")
            or "Unknown Title"
        )
        t_eng = anilist_show.get("englishName")
        if (
            t_eng
            and t_eng.strip().lower()
            != (anilist_show.get("name") or "").strip().lower()
        ):
            secondary = t_eng
        else:
            al_list = str(anilist_show.get("_anilist_list") or "").upper() or "WATCHING"
            al_prog = anilist_show.get("_anilist_progress", 0)
            secondary = (
                f"\033[38;5;244mAL {al_list} • EP {al_prog} • {ttype.upper()}\033[0m"
            )

        curr_name = provider_display_name(current_pid)
        t_name = provider_display_name(target_pid)
        trans_line = f"\033[38;5;222m{curr_name}\033[0m  \033[38;5;244m➔\033[0m  \033[36m{t_name}\033[0m"
        status_line = "\033[38;5;222m* No match found\033[0m"

        return render_modal_card(t_romaji, secondary, trans_line, status_line, width=w)

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
