"""Artist browsing using the standard AnimeThemes pickers."""

import logging
import threading
import time

from .. import app_core
from ..services import animethemes as service
from ..ui.help import picker_help
from ..ui.picker import tui_pick
from .animethemes import _pick_video_rows, _row_show, _show_poster


def artist_header(artist, section=None, row=None):
    from ..core.terminal import truncate_display
    from ..ui.panels import C_ALT, C_META, C_TITLE, C_RESET
    import shutil

    width = shutil.get_terminal_size().columns
    name = artist.get("name") or "Unknown artist"
    show = _row_show({"kind": "artist", "raw": artist})
    if row is not None:
        raw = row["raw"]
        song = raw.get("song") or {}
        title = song.get("title") or "Unknown song"
        credits = [f"Artist: {name}"]
        role = (row.get("credit") or {}).get("as") or (section or {}).get("credit")
        if role:
            credits.append(f"As {role}")
        others = []
        for performer in song.get("artists") or []:
            performer_name = performer.get("name") or ""
            if not performer_name or performer_name in {name, (section or {}).get("group")}:
                continue
            performance = performer.get("artistsong") or {}
            character = performance.get("as")
            others.append(f"{character} (CV: {performer_name})" if character else performer_name)
        if others:
            credits.append("With " + ", ".join(others))
        elif (section or {}).get("group"):
            credits.append(section["group"])
        alternate = " • ".join(credits)
        theme = str(raw.get("type") or "") + str(raw.get("sequence") or "")
        metadata = " • ".join(p for p in (theme, (raw.get("anime") or {}).get("name")) if p)
        show = _row_show(row)
        action = "play"
    else:
        title = f"Artist: {name}"
        alternate = (section or {}).get("label") or artist.get("information") or "No alternative title"
        if (section or {}).get("credit"):
            alternate += " • As " + section["credit"]
        direct = len(service.artist_theme_rows(artist))
        group_count = len(artist.get("groups") or [])
        metadata = (f"{direct} direct {'theme' if direct == 1 else 'themes'} • "
                    f"{group_count} {'group' if group_count == 1 else 'groups'}")
        action = "open"
    footer = f"AnimeThemes • Artist • Enter={action} • Left/Esc=back"
    lines = [f"{color}{truncate_display(str(text), max(1, width - 1))}{C_RESET}"
             for color, text in ((C_TITLE, title), (C_ALT, alternate), (C_META, metadata))]
    lines.append(app_core._poster_footer_line(show, footer, width))
    return "\n".join(lines)


def _artist_songs(flags, ui, ms, cfg, args, artist, section):
    rows = list(section["rows"])
    labels = [r["label"] for r in rows]
    hovered = 0
    hovered_at = time.monotonic()
    attempted = set()
    in_flight = set()

    def show_for(index):
        return _row_show(rows[max(0, min(index, len(rows) - 1))])

    def top(index):
        nonlocal hovered, hovered_at
        if 0 <= index < len(rows):
            if index != hovered:
                hovered, hovered_at = index, time.monotonic()
            return _show_poster(show_for(index), ui)
        return ""

    def enrich(index):
        try:
            rows[index] = service.enrich_artist_theme(rows[index])
            logging.getLogger(__name__).debug(
                "Artist song enriched: theme=%s cover=%s",
                rows[index]["raw"].get("id"), bool(_row_show(rows[index]).get("thumbnail")),
            )
            app_core._request_poster_redraw()
        except Exception:
            logging.getLogger(__name__).debug("Artist song enrichment failed", exc_info=True)
        finally:
            in_flight.discard(index)

    def live(_query=""):
        if hovered not in attempted and len(in_flight) < 2 and time.monotonic() - hovered_at >= 0.5:
            attempted.add(hovered)
            in_flight.add(hovered)
            threading.Thread(target=enrich, args=(hovered,), daemon=True).start()
        return labels, artist_header(artist, section, rows[hovered]), True

    while True:
        index = tui_pick(
            flags, ui, "Select song", labels,
            header_fn=lambda i: artist_header(artist, section, rows[i] if 0 <= i < len(rows) else rows[0]),
            top_header_fn=top, live_fn=live,
            info_fn=app_core.make_info_fn(lambda: [show_for(i) for i in range(len(rows))], ui),
            help_dict=picker_help("Select song", "Back", "Back"),
        )
        if index < 0:
            return
        row = rows[index]
        if not (row["raw"].get("animethemeentries")):
            row = app_core.with_loading("Loading song…", service.enrich_artist_theme, row)
            rows[index] = row
        videos = service.theme_video_rows(row["raw"])
        _pick_video_rows(
            flags, ui, ms, cfg, args, _row_show(row), videos,
            header_fn=lambda _i: artist_header(artist, section, row),
        )


def open_artist(flags, ui, ms, cfg, args, raw):
    artist = app_core.with_loading("Loading artist…", service.get_artist, raw["slug"])
    sections = app_core.with_loading("Loading performances…", service.artist_sections, artist)
    if not sections:
        app_core.set_action_feedback(_row_show({"kind": "artist", "raw": artist}), "No artist themes found.")
        return
    show = _row_show({"kind": "artist", "raw": artist})
    if len(sections) == 1:
        _artist_songs(flags, ui, ms, cfg, args, artist, sections[0])
        return
    while True:
        index = tui_pick(
            flags, ui, "Select group", [s["label"] for s in sections],
            hints={s["label"]: f"{len(s['rows'])} {'theme' if len(s['rows']) == 1 else 'themes'}" for s in sections},
            header_fn=lambda i: artist_header(artist, sections[i] if 0 <= i < len(sections) else None),
            top_header_fn=lambda _i: _show_poster(show, ui),
            info_fn=app_core.make_single_show_info_fn(show, ui),
            help_dict=picker_help("Select group", "Back", "Back"),
        )
        if index < 0:
            return
        _artist_songs(flags, ui, ms, cfg, args, artist, sections[index])
