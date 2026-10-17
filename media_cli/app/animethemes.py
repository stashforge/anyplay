"""AnimeThemes TUI flows."""

from __future__ import annotations

import os
import logging
import threading
from typing import Any

from .. import app_core
from ..services import animethemes as animethemes_service
from ..ui.help import picker_help
from ..ui.picker import tui_pick
from ..ui.spinner import loading_line as _loading_line
from .footer import context_footer

_TAB_LABELS = {
    "anime": "Anime",
    "animethemes": "Theme",
    "artists": "Artist",
    "series": "Series",
    "studios": "Studio",
    "playlists": "Playlist",
}


def _first_text(*values) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _image_url(value) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("thumbnail", "image", "cover", "poster", "avatar", "logo"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        if isinstance(candidate, dict):
            nested = _image_url(candidate)
            if nested:
                return nested
    cover = value.get("coverImage")
    if isinstance(cover, dict):
        for key in ("extraLarge", "large", "medium", "url"):
            candidate = cover.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    images = value.get("images")
    if isinstance(images, list):
        for image in images:
            if isinstance(image, dict):
                for key in ("link", "url", "path", "src"):
                    candidate = image.get(key)
                    if isinstance(candidate, str) and candidate.strip():
                        return candidate.strip()
    for key in ("anime", "song", "artist", "series", "studio", "user"):
        nested = value.get(key)
        if isinstance(nested, dict):
            image = _image_url(nested)
            if image:
                return image
    return ""


def _season_year(raw: dict) -> str:
    season = _first_text(raw.get("season")).title()
    year = raw.get("year")
    year_text = str(year).strip() if year else ""
    return " ".join(part for part in (season, year_text) if part)


def _theme_count(raw: dict) -> str:
    for key in ("theme_count", "themes_count", "animetheme_count", "animethemes_count"):
        try:
            count = int(raw.get(key) or 0)
        except (TypeError, ValueError):
            count = 0
        if count > 0:
            return f"{count} theme" if count == 1 else f"{count} themes"
    themes = raw.get("animethemes")
    if isinstance(themes, list) and themes:
        return f"{len(themes)} theme" if len(themes) == 1 else f"{len(themes)} themes"
    return ""


def _artist_names(raw: dict) -> str:
    artists = raw.get("artists") or []
    if not artists and isinstance(raw.get("song"), dict):
        artists = raw["song"].get("artists") or []
    names = [
        str(artist.get("name") or "").strip()
        for artist in artists
        if isinstance(artist, dict) and str(artist.get("name") or "").strip()
    ]
    return ", ".join(names)


def _anime_name(raw: dict) -> str:
    anime = raw.get("anime")
    if isinstance(anime, dict):
        return _first_text(anime.get("name"), anime.get("title"))
    return _first_text(anime)


def _theme_label(raw: dict) -> str:
    if raw.get("slug") or raw.get("type"):
        return _first_text(raw.get("slug"), raw.get("type"))
    theme = raw.get("animetheme") or raw.get("theme") or {}
    if isinstance(theme, dict):
        return _first_text(theme.get("slug"), theme.get("type"))
    return _first_text(theme)


def _metadata_for_row(row: dict) -> str:
    raw = row.get("raw") or {}
    bucket = row.get("bucket") or ""
    kind = row.get("kind") or ""
    if bucket == "anime":
        return " • ".join(
            part
            for part in (
                _first_text(raw.get("media_format"), raw.get("type")).upper(),
                _season_year(raw),
                _theme_count(raw),
            )
            if part
        )
    if bucket == "animethemes" or kind == "theme":
        return " • ".join(part for part in (_theme_label(raw), _anime_name(raw)) if part) or "THEME"
    if bucket == "artists" or kind == "artist":
        return "ARTIST"
    if bucket == "series" or kind == "series":
        return "SERIES"
    if bucket == "studios" or kind == "studio":
        return "STUDIO"
    if bucket == "playlists" or kind == "playlist":
        user = raw.get("user")
        owner = _first_text(user.get("name") if isinstance(user, dict) else "", raw.get("user_name"))
        return " • ".join(
            part for part in ("PLAYLIST", owner, _theme_count(raw)) if part
        )
    return ""


def _row_show(row: dict) -> dict:
    raw = row.get("raw") or {}
    kind = row.get("kind") or row.get("bucket") or "anime"
    image = _image_url(raw)
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
            "thumbnail": image,
        }
    if kind == "theme":
        song = raw.get("song") if isinstance(raw.get("song"), dict) else {}
        title = _first_text(song.get("title"), raw.get("title"), row.get("label"), "Unknown song")
        artists = _artist_names(raw)
        return {
            **provider_bits,
            "_id": raw.get("id") or row.get("label"),
            "name": _first_text(row.get("label"), title),
            "englishName": artists,
            "type": "THEME",
            "thumbnail": image,
        }
    if kind in {"artist", "series", "studio", "playlist"}:
        return {
            **provider_bits,
            "_id": raw.get("id") or row.get("label"),
            "name": _first_text(raw.get("name"), row.get("label"), f"Unknown {kind}"),
            "type": kind.upper(),
            "thumbnail": image,
            "description": raw.get("information") or "",
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
        "thumbnail": image,
    }


def _tab_label(tab: str) -> str:
    return _TAB_LABELS.get(tab, tab.title())


def _active_tabs(data: dict[str, list[dict]]) -> list[str]:
    return [
        tab for tab in animethemes_service.ANIMETHEMES_SEARCH_TABS
        if len(data.get(tab) or []) > 0
    ]


def _search_prompt(active_tabs: list[str], active_idx: int) -> str:
    if not active_tabs:
        return "Search AnimeThemes"
    tab = active_tabs[max(0, min(active_idx, len(active_tabs) - 1))]
    return f"Search AnimeThemes · {_tab_label(tab)}"


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


def _show_poster(show, ui):
    ui.hovered_show_id = show.get("_id")
    ui.hovered_show_obj = show
    app_core._hovered_show_id = ui.hovered_show_id
    poster = app_core._get_poster(show) or ""
    state = (show.get("_id"), show.get("thumbnail"), show.get("_poster_status"), bool(poster))
    if getattr(ui, "_animethemes_poster_state", None) != state:
        ui._animethemes_poster_state = state
        logging.getLogger(__name__).debug(
            "AnimeThemes cover: id=%s title=%s status=%s rendered=%s url=%s",
            state[0], show.get("name"), state[2], state[3], state[1] or "missing",
        )
    return poster


def _search_header(
    query: str,
    tab: str,
    rows: list[dict] | None = None,
    selected_idx: int = 0,
    loading_msg: str = "",
    error_msg: str = "",
):
    rows = list(rows or [])
    if not rows or not (0 <= selected_idx < len(rows)):
        return app_core.render_search_header(
            "AnimeThemes",
            query,
            "sub",
            lambda: [],
            lambda: loading_msg,
            selected_idx=max(0, selected_idx),
            esc_action="back",
            get_error_fn=lambda: error_msg,
        )

    row = rows[selected_idx]
    show = _row_show(row)
    title = show.get("name") or row.get("label") or "Unknown"
    alt = show.get("englishName") or "No alternative title"
    if row.get("kind") == "artist":
        raw = row.get("raw") or {}
        title = "Artist: " + title
        memberships = ", ".join(g.get("name", "") for g in raw.get("groups") or [])
        alt = raw.get("information") or ("Member of " + memberships if memberships else alt)
    metadata = _metadata_for_row(row)
    footer = f'AnimeThemes • "{query}" • Tab=switch • Enter=open • Left=search • Esc=back'
    try:
        width = os.get_terminal_size().columns
    except OSError:
        width = 80
    from ..ui.panels import C_ALT, C_META, C_TITLE, C_RESET
    from ..core.terminal import truncate_display

    def _fit(value: str) -> str:
        return truncate_display(str(value or ""), max(1, width - 1))

    return "\n".join([
        f"{C_TITLE}{_fit(title)}{C_RESET}",
        f"{C_ALT}{_fit(alt)}{C_RESET}",
        f"{C_META}{_fit(metadata)}{C_RESET}",
        app_core._poster_footer_line(show, footer, width),
    ])


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
        top_header_fn=lambda _si: _show_poster(show, ui),
        hints={row["label"]: row.get("episodes") or "play video" for row in rows},
        info_fn=app_core.make_single_show_info_fn(show, ui),
        help_dict=hd,
    )
    if idx < 0:
        return "DETAILS"
    _play_row(rows[idx], cfg, args)
    return "DETAILS"


def _pick_video_rows(flags, ui, ms, cfg, args, show: dict, rows: list[dict], *, header_fn=None) -> None:
    if not rows:
        app_core.set_action_feedback(show, "No AnimeThemes videos found.")
        return
    sub_idx = tui_pick(
        flags,
        ui,
        "Select song",
        [item["label"] for item in rows],
        header_fn=header_fn or (lambda _si: _theme_header(show, ui, ms, "")),
        top_header_fn=lambda _si: _show_poster(show, ui),
        hints={item["label"]: item.get("episodes") or "play video" for item in rows},
        help_dict=picker_help("Select song", "Back", "Back"),
    )
    if sub_idx >= 0:
        _play_row(rows[sub_idx], cfg, args)


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

    def loading_text():
        if not loading:
            return ""
        try:
            width = os.get_terminal_size().columns
        except OSError:
            width = 80
        return _loading_line("Searching…", width, getattr(app_core.display, "_spinner_style", None))

    def tabs_for():
        return _active_tabs(data)

    def current_tab():
        tabs = tabs_for()
        if tabs:
            return tabs[max(0, min(active_idx, len(tabs) - 1))]
        return animethemes_service.ANIMETHEMES_SEARCH_TABS[0]

    rows = rows_for(current_tab())

    def _tab_fn(opt=None, direction=1):
        nonlocal active_idx, rows
        tabs = tabs_for()
        if not tabs:
            return [], _search_header(query, current_tab(), [], 0, loading_text(), error)
        active_idx = (active_idx + direction) % len(tabs)
        rows = rows_for(tabs[active_idx])
        labels = [row["label"] for row in rows] if rows else ([] if loading else ["No results"])
        return labels, _search_header(query, tabs[active_idx], rows, 0, loading_text(), error)

    live_was_done = False
    opened_artists = set()

    def auto_open_artist():
        return (current_tab() == "artists" and len(rows) == 1
                and (rows[0].get("raw") or {}).get("slug") not in opened_artists)

    def _live_fn(_q=""):
        nonlocal active_idx, rows, live_was_done
        tabs = tabs_for()
        if not loading and not live_was_done:
            live_was_done = True
            if tabs and not rows_for(tabs[max(0, min(active_idx, len(tabs) - 1))]):
                for idx, tab_name in enumerate(tabs):
                    if len(data.get(tab_name) or []) > 0:
                        active_idx = idx
                        break
        tab_name = tabs[max(0, min(active_idx, len(tabs) - 1))] if tabs else current_tab()
        rows = rows_for(tab_name) if tabs else []
        labels = [row["label"] for row in rows] if rows else ([] if loading else ["No results"])
        return labels, _search_header(query, tab_name, rows, 0, loading_text(), error), not loading

    def _top_header(si):
        if rows and 0 <= si < len(rows):
            show = _row_show(rows[si])
            poster = _show_poster(show, ui)
            if poster:
                return poster
        return ""

    while True:
        tabs = tabs_for()
        tab = tabs[max(0, min(active_idx, len(tabs) - 1))] if tabs else current_tab()
        labels = [row["label"] for row in rows] if rows else ([] if loading else ["No results"])
        hd = picker_help("Search AnimeThemes", "Back", "Back", tab_label="Switch tab")
        idx = tui_pick(
            flags,
            ui,
            lambda: _search_prompt(tabs_for(), active_idx),
            labels,
            header_fn=lambda si: _search_header(
                query,
                current_tab(),
                rows,
                si,
                loading_text(),
                error,
            ),
            top_header_fn=_top_header,
            tab_fn=_tab_fn,
            live_fn=_live_fn,
            info_fn=app_core.make_info_fn(lambda: [_row_show(row) for row in rows], ui),
            help_dict=hd,
            auto_select_single_when_done=auto_open_artist,
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
            show = _row_show(row)
            anime = app_core.with_loading(
                "Loading AnimeThemes…",
                animethemes_service.get_anime_for_show,
                {**show, "_animethemes_slug": slug, "name": raw.get("name")},
            )
            video_rows = animethemes_service.flatten_anime_videos(anime or {})
            if not video_rows:
                continue
            if isinstance(anime, dict):
                show.update(
                    {
                        "_id": anime.get("id") or show.get("_id"),
                        "name": anime.get("name") or show.get("name"),
                        "type": anime.get("media_format") or show.get("type"),
                        "airedStart": {"year": anime.get("year")} if anime.get("year") else show.get("airedStart"),
                        "thumbnail": _image_url(anime) or show.get("thumbnail"),
                    }
                )
            ui.ui_show_ctx = show
            _pick_video_rows(flags, ui, ms, cfg, args, show, video_rows)
        elif row["kind"] == "artist":
            from .animethemes_artist import open_artist
            opened_artists.add(raw.get("slug"))
            open_artist(flags, ui, ms, cfg, args, raw)
        elif row["kind"] == "theme":
            show = _row_show(row)
            ui.ui_show_ctx = show
            _pick_video_rows(
                flags,
                ui,
                ms,
                cfg,
                args,
                show,
                animethemes_service.theme_video_rows(raw),
            )
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
