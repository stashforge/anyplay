"""AnimeThemes TUI flows."""

from __future__ import annotations

import os
import threading
from typing import Any

from .. import app_core
from ..services import animethemes as animethemes_service
from ..ui.help import picker_help
from ..ui.picker import tui_pick
from ..ui.spinner import loading_line as _loading_line
from .footer import context_footer


def _first_text(*values) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _row_show(row: dict) -> dict:
    raw = row.get("raw") or {}
    kind = row.get("kind") or row.get("bucket") or "anime"
    provider_bits = {
        "_provider": "animethemes",
        "_provider_name": "AnimeThemes",
        "_has_provider_link": True,
    }
    if kind == "anime":
        year = raw.get("year")
        return {
            **provider_bits,
            "_id": raw.get("id") or raw.get("slug") or row.get("label"),
            "name": _first_text(raw.get("name"), row.get("label"), "Unknown"),
            "englishName": _first_text(raw.get("english_name"), raw.get("title")),
            "romajiName": _first_text(raw.get("name")),
            "nativeName": _first_text(raw.get("native_name")),
            "altNames": raw.get("synonyms") or raw.get("aliases") or [],
            "type": _first_text(raw.get("media_format"), raw.get("type"), "ANIME").upper(),
            "status": _first_text(raw.get("status")),
            "airedStart": {"year": year} if year else None,
            "score": raw.get("score"),
            "_animethemes_slug": raw.get("slug"),
        }
    if kind == "song":
        artists = raw.get("artists") or []
        artist_names = ", ".join(
            str(artist.get("name") or "").strip()
            for artist in artists
            if isinstance(artist, dict) and str(artist.get("name") or "").strip()
        )
        return {
            **provider_bits,
            "_id": raw.get("id") or row.get("label"),
            "name": _first_text(raw.get("title"), row.get("label"), "Unknown song"),
            "englishName": artist_names,
            "type": "SONG",
        }
    if kind == "artist":
        return {
            **provider_bits,
            "_id": raw.get("id") or row.get("label"),
            "name": _first_text(raw.get("name"), row.get("label"), "Unknown artist"),
            "type": "ARTIST",
        }
    return {
        **provider_bits,
        "_id": raw.get("id") or row.get("label"),
        "name": _first_text(raw.get("basename"), raw.get("filename"), row.get("label"), "Unknown video"),
        "englishName": " ".join(
            part
            for part in (
                _first_text(raw.get("resolution")),
                _first_text(raw.get("source")),
            )
            if part
        ),
        "type": "VIDEO",
    }


def _tab_counts_text(active_tab: str, counts: dict[str, int]) -> str:
    parts = []
    for name in animethemes_service.ANIMETHEMES_SEARCH_TABS:
        parts.append(f"{name.title()} {counts.get(name, 0)}")
    return " • ".join(parts)


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


def _search_header(
    query: str,
    tab: str,
    counts: dict[str, int],
    rows: list[dict] | None = None,
    selected_idx: int = 0,
    loading_msg: str = "",
    error_msg: str = "",
):
    rows = list(rows or [])
    shows = [_row_show(row) for row in rows]
    return app_core.render_search_header(
        "AnimeThemes",
        query,
        "video",
        lambda: shows,
        lambda: loading_msg,
        selected_idx=max(0, selected_idx),
        esc_action="Back",
        get_error_fn=lambda: error_msg,
        badges=[_tab_counts_text(tab, counts)],
    )


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
    app_core.enter_alt_screen()
    data: dict[str, list[dict]] = {}
    loading = True
    error = ""

    def _worker():
        nonlocal data, loading, error
        try:
            data = animethemes_service.search(query)
        except Exception as exc:
            error = str(exc)
            data = {}
        finally:
            loading = False

    threading.Thread(target=_worker, daemon=True).start()
    active_idx = 0

    def rows_for(tab):
        return animethemes_service.flatten_search_bucket(tab, data.get(tab) or [])

    def counts_for():
        return {
            tab: len(data.get(tab) or [])
            for tab in animethemes_service.ANIMETHEMES_SEARCH_TABS
        }

    def loading_text():
        if not loading:
            return ""
        try:
            width = os.get_terminal_size().columns
        except OSError:
            width = 80
        return _loading_line("Searching…", width, getattr(app_core.display, "_spinner_style", None))

    rows = rows_for(animethemes_service.ANIMETHEMES_SEARCH_TABS[active_idx])

    def _tab_fn(opt=None, direction=1):
        nonlocal active_idx, rows
        tabs = animethemes_service.ANIMETHEMES_SEARCH_TABS
        active_idx = (active_idx + direction) % len(tabs)
        rows = rows_for(tabs[active_idx])
        labels = [row["label"] for row in rows] or ["No results"]
        return labels, _search_header(query, tabs[active_idx], counts_for(), rows, 0, loading_text(), error)

    live_was_done = False

    def _live_fn(_q=""):
        nonlocal active_idx, rows, live_was_done
        tabs = animethemes_service.ANIMETHEMES_SEARCH_TABS
        if not loading and not live_was_done:
            live_was_done = True
            counts = counts_for()
            if not rows_for(tabs[active_idx]):
                for idx, tab_name in enumerate(tabs):
                    if counts.get(tab_name, 0):
                        active_idx = idx
                        break
        tab_name = tabs[active_idx]
        rows = rows_for(tab_name)
        labels = [row["label"] for row in rows] or ["No results"]
        return labels, _search_header(query, tab_name, counts_for(), rows, 0, loading_text(), error), not loading

    while True:
        tab = animethemes_service.ANIMETHEMES_SEARCH_TABS[active_idx]
        labels = [row["label"] for row in rows] or ["No results"]
        hd = picker_help("Search AnimeThemes", "Back", "Back", tab_label="Switch tab")
        idx = tui_pick(
            flags,
            ui,
            "AnimeThemes",
            labels,
            header_fn=lambda si: _search_header(
                query,
                animethemes_service.ANIMETHEMES_SEARCH_TABS[active_idx],
                counts_for(),
                rows,
                si,
                loading_text(),
                error,
            ),
            tab_fn=_tab_fn,
            live_fn=_live_fn,
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
