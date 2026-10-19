"""Full-screen info overlay for a single anime title.

Shows all available metadata for the selected show.  Works from any
picker (search, history, AniList lists) or from the action/episode menus.

Usage::

    from .info_screen import show_info_screen
    show_info_screen(show_dict, poster_manager=pm, ui=ui_state)

Press ``Q`` or ``ESC`` to return to wherever the caller was.
"""

from __future__ import annotations

import os
import re
import sys
from copy import deepcopy
from typing import TYPE_CHECKING

from ..core.terminal import fit_terminal_line as _fit
from ..domain.metadata import positive_int as _positive_int
from ..domain.titles import wrap_title as _wrap_title
from .terminal_runtime import borrow_terminal, terminal_application
from .transition import PreparationCancelled

if TYPE_CHECKING:
    from ..context import UiState
    from .poster import PosterManager

RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[38;5;244m"
LABEL  = "\033[1;38;2;190;180;235m"
WHITE  = "\033[1;38;2;145;185;245m"
MUTED  = "\033[38;2;170;180;195m"
CYAN   = "\033[1;36m"
HINT   = "\033[38;5;244m"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_key(fd: int) -> str:
    """Read one key from *fd* and return a name string."""
    ch = os.read(fd, 1)
    if ch == b"\x1b":
        rest = b""
        try:
            os.set_blocking(fd, False)
            rest = os.read(fd, 8)
        except Exception:
            pass
        finally:
            os.set_blocking(fd, True)
        seq = ch + rest
        if seq in (b"\x1b[A", b"\x1bOA"):
            return "UP"
        if seq in (b"\x1b[B", b"\x1bOB"):
            return "DOWN"
        if seq == b"\x1b":
            return "ESC"
        return "OTHER"  # HOME, END, PAGE_UP, PAGE_DOWN, DELETE, etc. — ignore
    if ch in (b"\r", b"\n"):
        return "ENTER"
    if ch == b"q" or ch == b"Q":
        return "Q"
    if ch == b"\x0f":
        return "CTRL_O"
    if ch == b"\x03":
        raise KeyboardInterrupt
    return "OTHER"



def _fmt_fuzzy_date(d: dict | str | None) -> str:
    if not d:
        return ""
    if isinstance(d, str):
        return d
    y = d.get("year")
    d.get("month") or d.get("date") and None  # month key
    # support both {year, month, date} and {year, month, day}
    month = d.get("month")
    day   = d.get("date") or d.get("day")
    if not y:
        return ""
    parts = [str(y)]
    if month:
        parts.append(f"{month:02d}")
    if day:
        parts.append(f"{day:02d}")
    return "-".join(parts)


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _section(label: str, value: str, w: int, lines: list) -> None:
    """Append a labelled section to *lines* if *value* is non-empty."""
    if not value:
        return
    lines.append(f"{LABEL}{label}{RESET}")
    for dl in _wrap_title(value, w - 2, 99).splitlines():
        lines.append(f"{MUTED}{dl}{RESET}")
    lines.append("")


def _build_content(show: dict, w: int) -> list[str]:
    """Return a list of ANSI-formatted lines for *show*."""
    lines: list[str] = []

    # ── Titles ────────────────────────────────────────────────────────
    romaji  = show.get("romajiName") or show.get("name") or ""
    english = show.get("englishName") or show.get("_display_english_name") or ""
    native  = show.get("nativeName") or ""
    alts    = [a for a in (show.get("altNames") or []) if a]

    if romaji:
        for tl in _wrap_title(romaji, w - 2, 2).splitlines():
            lines.append(f"{WHITE}{tl}{RESET}")
    if english and english != romaji:
        for tl in _wrap_title(english, w - 2, 99).splitlines():
            lines.append(f"{MUTED}{tl}{RESET}")
    lines.append("")

    # ── Quick stats row ───────────────────────────────────────────────
    stats: list[str] = []
    fmt   = show.get("type") or show.get("format") or ""
    if fmt:
        stats.append(fmt.replace("_", " "))
    status = show.get("_anilist_status") or show.get("status") or ""
    if any(term in str(show.get("status") or "").upper() for term in ("FINISHED", "COMPLETED", "ENDED")) or any(term in str(show.get("_anilist_status") or "").upper() for term in ("FINISHED", "COMPLETED", "ENDED")):
        status = "Finished"
    if status:
        stats.append(status.replace("_", " ").title())
    season_info = show.get("season") or {}
    if isinstance(season_info, dict):
        season_q = season_info.get("quarter") or ""
        season_y = season_info.get("year") or ""
    else:
        season_q = str(season_info)
        season_y = ""
    if season_q and season_y:
        stats.append(f"{season_q} {season_y}")
    elif season_y:
        stats.append(str(season_y))
    score = show.get("score")
    if score:
        stats.append(f"★ {score:.1f}")
    if stats:
        lines.append(f"{MUTED}{(' • ').join(stats)}{RESET}")
        lines.append("")


    # ── Episodes ──────────────────────────────────────────────────────
    avail_eps = (show.get("availableEpisodes") or {})
    avail_sub = _positive_int(avail_eps.get("sub")) or _positive_int(avail_eps.get("dub"))
    total_eps = _positive_int(show.get("episodeCount")) or _positive_int(show.get("episodes"))
    # ── AniList tracking ──────────────────────────────────────────────
    al_list     = show.get("_anilist_list") or ""
    al_progress = _positive_int(show.get("_anilist_progress"))
    if al_list:
        status_label = al_list.replace("_", " ").title()
        status_str = status_label
        if al_progress is not None:
            total_str = f"/{total_eps}" if total_eps else ""
            status_str += f" • {al_progress}{total_str} eps"
        _section("AniList Status", status_str, w, lines)

    # ── Episodes ──────────────────────────────────────────────────────
    ep_parts: list[str] = []
    if avail_sub is not None:
        ep_parts.append(f"Available {avail_sub}")
    if total_eps:
        ep_parts.append(f"Total {total_eps}")

    fmt = str(show.get("format") or show.get("type") or "").upper()
    if ep_parts and fmt != "MOVIE" and total_eps != 1:
        _section("Episodes", " • ".join(ep_parts), w, lines)

    # ── Next airing ───────────────────────────────────────────────────
    next_ep   = _positive_int(show.get("_next_airing_ep"))
    next_time = show.get("_next_airing_time")
    if next_ep and next_time is not None:
        try:
            from media_cli.domain.metadata import format_time
            time_str = f"in {format_time(int(next_time))}"
        except (ValueError, TypeError):
            time_str = str(next_time)
        _section("Next Episode", f"EP {next_ep} • {time_str}", w, lines)
    elif next_ep:
        _section("Next Episode", f"EP {next_ep}", w, lines)

    # ── Dates ─────────────────────────────────────────────────────────
    start = _fmt_fuzzy_date(show.get("airedStart"))
    end   = _fmt_fuzzy_date(show.get("airedEnd"))
    if start or end:
        date_str = start
        if end:
            date_str += f" → {end}" if start else end
        _section("Aired", date_str, w, lines)

    # ── Genres ────────────────────────────────────────────────────────
    genres = show.get("genres") or []
    if isinstance(genres, list) and genres:
        _section("Genres", " • ".join(str(g) for g in genres[:8] if g), w, lines)

    # ── Description ───────────────────────────────────────────────────
    desc = _clean_html(str(show.get("description") or "").strip())
    if desc:
        lines.append(f"{LABEL}Description{RESET}")
        for dl in _wrap_title(desc, w - 2, 99).splitlines():
            lines.append(f"{MUTED}{dl}{RESET}")
        lines.append("")

    # ── Native & Synonyms (Moved to bottom) ───────────────────────────
    if (native and native != romaji) or alts:
        lines.append(f"{LABEL}Alternative Titles{RESET}")
        if native and native != romaji:
            for tl in _wrap_title(f"Native: {native}", w - 2, 99).splitlines():
                lines.append(f"{MUTED}{tl}{RESET}")
        if alts:
            for tl in _wrap_title(f"Synonyms: {', '.join(alts)}", w - 2, 99).splitlines():
                lines.append(f"{MUTED}{tl}{RESET}")
        lines.append("")


    return lines



# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

@terminal_application
def show_info_screen(
    show: dict | None,
    *,
    poster_manager: PosterManager | None = None,
    ui: UiState | None = None,
) -> None:
    if not show:
        return

    if (not show.get("description") or not show.get("genres")) and not show.get("_enrichment_attempted"):
        try:
            from ..core.enrichment import enrich_show_if_missing
            from .display import with_loading

            enriched = deepcopy(show)
            with_loading("Loading info...", enrich_show_if_missing, enriched)
        except PreparationCancelled:
            return
        except Exception:
            pass
        else:
            show.update(enriched)

    with borrow_terminal() as runtime:
        if runtime is None:
            return
        runtime.assert_owner_thread()
        _show_info_screen(show, runtime.fileno(), poster_manager=poster_manager, ui=ui)


def _show_info_screen(
    show: dict,
    tty_fd: int,
    *,
    poster_manager: PosterManager | None,
    ui: UiState | None,
) -> None:
    from . import terminal_images

    first_frame_pending = True
    try:
        scroll = 0

        # fetch poster once (may be empty string if unavailable)
        if poster_manager is not None and ui is not None:
            ui.hovered_show_id  = show.get("_id") or show.get("id")
            ui.hovered_show_obj = show
        poster_raw = (poster_manager.get(show) if poster_manager else None) or ""

        from .covers import (
            POSTER_HEIGHT,
        )
        from .covers import (
            poster_symbol_lines as _poster_symbol_lines,
        )
        from .covers import (
            poster_uses_native_protocol as _poster_uses_native_protocol,
        )

        def _render(scroll_offset: int = 0) -> int:
            try:
                w, h = os.get_terminal_size(sys.stdin.fileno())
            except Exception:
                w, h = 80, 24

            nonlocal poster_raw, first_frame_pending
            # Refresh poster in case it just finished downloading
            if poster_manager and not poster_raw:
                poster_raw = poster_manager.get(show) or ""

            native = poster_raw if _poster_uses_native_protocol(poster_raw) else ""
            p_lines = _poster_symbol_lines(poster_raw, POSTER_HEIGHT, w)
            content = _build_content(show, w)

            out: list[str] = []

            # poster rows
            if poster_raw:
                for row in range(POSTER_HEIGHT):
                    pl = p_lines[row] if row < len(p_lines) else ""
                    out.append(f"\033[2K{pl}")
                out.append("\033[2K") # Fixed gap below poster so scrolled text doesn't hit the image

            # scrollable content
            visible_h = (h - POSTER_HEIGHT - 3) if poster_raw else (h - 2)
            total_lines = len(content)
            max_scroll  = max(0, total_lines - visible_h)
            clamped     = max(0, min(scroll_offset, max_scroll))

            visible = content[clamped: clamped + visible_h]
            for line in visible:
                out.append(f"\033[2K{_fit(line, w)}")

            # footer hint
            scroll_hint = f"  {clamped + 1}-{min(clamped + visible_h, total_lines)}/{total_lines}" \
                          if total_lines > visible_h else ""
            footer = _fit(
                f"{HINT}Q / Esc  close{scroll_hint}{RESET}",
                w,
            )
            # pad remaining rows
            used = len(out)
            for _ in range(h - 1 - used):
                out.append("\033[2K")
            out.append(f"\033[2K{footer}")

            clear_sig = terminal_images.replace_for_frame(has_image=bool(native))
            overlay   = f"\033[1;1H{native}" if native else ""
            sys.stdout.write(
                clear_sig + ("\033[2J" if first_frame_pending else "")
                + "\033[H" + "\r\n".join(out)
                + overlay + "\033[?25l"
            )
            sys.stdout.flush()
            first_frame_pending = False
            return clamped

        scroll = _render(scroll)

        while True:
            key = _read_key(tty_fd)
            if key in ("Q", "ESC", "CTRL_O"):
                break
            elif key == "DOWN":
                scroll += 1
                scroll = _render(scroll)
            elif key == "UP":
                scroll = max(0, scroll - 1)
                scroll = _render(scroll)
            elif key == "ENTER":
                break

    finally:
        try:
            from .picker import invalidate_screen_buffer
            invalidate_screen_buffer()
        except Exception:
            pass
        # Clear any native-protocol image we drew before returning to the picker,
        # so the picker can render its own image on the next redraw.
        sys.stdout.write(terminal_images.clear_now() + "\033[?25h")
        sys.stdout.flush()
