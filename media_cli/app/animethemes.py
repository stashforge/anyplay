"""AnimeThemes TUI flows."""

from __future__ import annotations

import os
from typing import Any

from .. import app_core
from ..domain.titles import get_show_display_title
from ..services import animethemes as animethemes_service
from ..ui.help import picker_help
from ..ui.picker import tui_pick
from .footer import context_footer

_C_HINT = "\033[38;5;244m"
_RST = "\033[0m"


def _truncate(text: str, width: int) -> str:
    return app_core._poster_footer_line({}, text, width).strip()


def _theme_header(show, ui, ms, ttype="sub"):
    try:
        w = os.get_terminal_size().columns
    except OSError:
        w = 80
    parts = []
    app_core.build_info_panel(show, ttype, w, parts)
    footer = context_footer(show, ui, ms, "AnimeThemes • Enter=play • ?=Help • Esc=Back")
    parts.append(app_core._poster_footer_line(show, footer, w))
    return "\n".join(parts)


def _search_header(query: str, tab: str, counts: dict[str, int]):
    try:
        w = os.get_terminal_size().columns
    except OSError:
        w = 80
    tab_title = tab.title()
    count_text = " • ".join(
        f"{name.title()} {counts.get(name, 0)}"
        for name in animethemes_service.ANIMETHEMES_SEARCH_TABS
    )
    lines = [
        "AnimeThemes",
        f"Search: {query}",
        f"Tab: {tab_title}",
        count_text or "No results",
        f"{_C_HINT}AnimeThemes • Tab=switch • Enter=play/open • ?=Help • Esc=Back{_RST}",
    ]
    return "\n".join(_truncate(line, w) for line in lines)


def _play_row(row: dict, cfg: dict, args: Any):
    stream = row.get("stream") or {}
    title = row.get("anime") or row.get("song") or "AnimeThemes"
    ep = row.get("theme") or "Video"
    player = args.player or cfg.get("player", "mpv")
    if app_core.is_termux():
        app_core.play_android(
            title,
            ep,
            stream,
            None,
            player,
            total_eps=1,
            show_id=None,
            is_binge=False,
        )
        app_core._exit_player_screen()
        return
    app_core.play_desktop(
        title,
        ep,
        stream,
        fetch_callback=None,
        total_eps=1,
        is_binge=False,
        show_id=None,
        osd_msg=f"AnimeThemes\n{row.get('song') or ep}",
        episode_index=0,
        next_episode=None,
        aniskip_enabled=False,
        aniskip_auto=False,
    )
    app_core._exit_player_screen()


def open_anime_songs(flags, ui, ms, cfg, args, show: dict, ttype: str = "sub") -> str:
    anime = app_core.with_loading(
        "Loading AnimeThemes…",
        animethemes_service.get_anime_for_show,
        show,
    )
    rows = animethemes_service.flatten_anime_videos(anime or {})
    if not rows:
        app_core.set_action_feedback(show, "No AnimeThemes videos found.")
        return "DETAILS"

    hd = picker_help("Select song", "Back", "Back")
    idx = tui_pick(
        flags,
        ui,
        "Select song",
        [row["label"] for row in rows],
        header_fn=lambda _si: _theme_header(show, ui, ms, ttype),
        hints={row["label"]: row.get("episodes") or "play video" for row in rows},
        info_fn=app_core.make_single_show_info_fn(show, ui),
        help_dict=hd,
    )
    if idx < 0:
        return "DETAILS"
    _play_row(rows[idx], cfg, args)
    return "DETAILS"


def handle_animethemes_search_state(flags, ui, ms, cfg, args, ttype: str) -> str:
    del ttype
    query = " ".join(getattr(args, "query", None) or []) or ms.query_str
    if not query:
        app_core.err("AnimeThemes search needs a query.")
        return "QUIT"
    data = app_core.with_loading(
        "Searching AnimeThemes…",
        animethemes_service.search,
        query,
    )
    active_idx = 0
    counts = {
        tab: len(data.get(tab) or [])
        for tab in animethemes_service.ANIMETHEMES_SEARCH_TABS
    }

    def rows_for(tab):
        return animethemes_service.flatten_search_bucket(tab, data.get(tab) or [])

    rows = rows_for(animethemes_service.ANIMETHEMES_SEARCH_TABS[active_idx])

    def _tab_fn(opt=None, direction=1):
        nonlocal active_idx, rows
        tabs = animethemes_service.ANIMETHEMES_SEARCH_TABS
        active_idx = (active_idx + direction) % len(tabs)
        rows = rows_for(tabs[active_idx])
        labels = [row["label"] for row in rows] or ["No results"]
        return labels, _search_header(query, tabs[active_idx], counts)

    while True:
        tab = animethemes_service.ANIMETHEMES_SEARCH_TABS[active_idx]
        labels = [row["label"] for row in rows] or ["No results"]
        hd = picker_help("Search AnimeThemes", "Back", "Back", tab_label="Switch tab")
        idx = tui_pick(
            flags,
            ui,
            "Search AnimeThemes",
            labels,
            header_fn=lambda _si: _search_header(query, tab, counts),
            tab_fn=_tab_fn,
            help_dict=hd,
        )
        if idx < 0:
            return "QUIT"
        if not rows:
            continue
        row = rows[idx]
        raw = row.get("raw") or {}
        if row["kind"] == "anime":
            slug = raw.get("slug")
            if not slug:
                continue
            anime = app_core.with_loading(
                "Loading AnimeThemes…",
                animethemes_service.get_anime_for_show,
                {"_animethemes_slug": slug, "name": raw.get("name")},
            )
            video_rows = animethemes_service.flatten_anime_videos(anime or {})
            if not video_rows:
                continue
            show = {
                "name": raw.get("name") or anime.get("name") if anime else raw.get("name"),
                "_provider_name": "AnimeThemes",
                "_provider": "animethemes",
            }
            ui.ui_show_ctx = show
            sub_idx = tui_pick(
                flags,
                ui,
                "Select song",
                [item["label"] for item in video_rows],
                header_fn=lambda _si: _theme_header(show, ui, ms),
                hints={item["label"]: item.get("episodes") or "play video" for item in video_rows},
                help_dict=picker_help("Select song", "Back", "Back"),
            )
            if sub_idx >= 0:
                _play_row(video_rows[sub_idx], cfg, args)
        elif row["kind"] == "video" and raw.get("link"):
            play_row = {
                **row,
                "anime": "AnimeThemes",
                "theme": raw.get("filename") or raw.get("basename") or "Video",
                "song": raw.get("basename") or "Video",
                "stream": {
                    "link": raw["link"],
                    "resolution": str(raw.get("resolution") or "WEB"),
                    "source": raw.get("source") or "AnimeThemes",
                    "type": "webm",
                },
            }
            _play_row(play_row, cfg, args)
        else:
            app_core.set_action_feedback({}, f"{row['kind'].title()} result has no direct video yet.")
    return "QUIT"
