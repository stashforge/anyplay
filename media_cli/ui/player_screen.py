"""
Player-screen renderer for Anyplay.

Renders the in-app overlay shown while an episode is loading or playing
through ``mpv``.  Extracted from ``app.py`` so that playback UI logic lives
next to the other ``ui/`` modules.

The module owns the ``_player_ui_state`` dict and the
:class:`PlayerScreen` façade class.  ``app.py`` creates a single
``PlayerScreen`` instance at startup and passes it to playback handlers.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import select
import sys
import threading
import time
from typing import TYPE_CHECKING

from ..core.terminal import fit_terminal_line as _fit_terminal_line
from ..domain.metadata import (
    anilist_list_status_label,
    anime_status_label,
    format_next_airing,
    format_years,
)
from ..domain.metadata import (
    positive_int as _positive_int,
)
from ..domain.titles import (
    get_display_titles,
    get_show_display_title,
)
from ..domain.titles import (
    wrap_title as _wrap_title,
)
from . import terminal_images
from .covers import (
    POSTER_HEIGHT,
)
from .covers import (
    poster_symbol_lines as _poster_symbol_lines,
)
from .covers import (
    poster_uses_native_protocol as _poster_uses_native_protocol,
)
from .poster import PosterManager
from .spinner import spinner_frame

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..context import UiState

TITLE_COLOR = "\033[1;38;2;145;185;245m"
SECTION_LABEL = "\033[1;38;2;190;180;235m"
TEXT_COLOR = "\033[38;2;170;180;195m"
RESET = "\033[0m"


def _safe_stdout_write(text: str, *, flush: bool = True) -> bool:
    """Best-effort terminal write for redraws; skip frames on EAGAIN."""
    try:
        sys.stdout.write(text)
        if flush:
            sys.stdout.flush()
        return True
    except BlockingIOError:
        _logger.debug("Dropped player-screen redraw because stdout would block")
        return False


# ---------------------------------------------------------------------------
# Shared mutable state (kept as a plain dict to allow _setitem_ callbacks)
# ---------------------------------------------------------------------------

_player_ui_state: dict = {
    "active": False,
    "show": None,
    "current_ep": 0,
    "current_ep_label": "",
    "total_eps": 0,
    "status_lines": [],
    "stream_info": {},
    "mpv_props": None,
}
"""Module-level state dict for the currently active player overlay.

``app.py`` passes a lambda that calls :func:`update_stream_info` so that
``desktop_playback.play_desktop`` can update stream metadata without a
circular import.
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _fmt_time(sec: float | None) -> str:
    """Format *sec* seconds as ``MM:SS`` or ``HH:MM:SS``."""
    if not sec:
        return "00:00"
    m, s = divmod(int(sec), 60)
    if m >= 60:
        h, m = divmod(m, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _thin_progress_bar(position: float, duration: float, width: int) -> str:
    """Return a low-height colored progress bar."""
    bar_width = max(10, min(40, width))
    ratio = 0
    if duration > 0:
        ratio = max(0, min(1, position / duration))
    filled = int(ratio * bar_width)
    filled_char = "\u2501"
    empty_char = "\u2500"
    return (
        f"\033[38;5;115m{filled_char * filled}\033[0m"
        f"\033[38;5;240m{empty_char * (bar_width - filled)}\033[0m"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def update_stream_info(info: dict) -> None:
    """Store stream metadata (mirror name, quality, preferred flag) for display."""
    _player_ui_state["stream_info"] = info


def activate(show: dict, current_ep: object, total_eps: int) -> None:
    """Mark the player screen as active and set the episode context.

    Call this before launching the player process.
    """
    _loading_cancel_event.clear()
    _player_ui_state.update({
        "active": True,
        "show": show,
        "current_ep": current_ep,
        "current_ep_label": "",
        "total_eps": total_eps,
        "status_lines": [],
        "stream_info": {},
        "mpv_props": None,
        "loading_cancel_requested": False,
        "_cleared_terminal_image": False,
        "_last_poster_key": None,
        "_poster_manager": None,
        "_ui": None,
        # partial-render bookkeeping
        "_cached_w": 0,
        "_cached_h": 0,
        "_row_state": None,
        "_row_bar": None,
        "_row_time": None,
    })
    try:
        from .picker import invalidate_screen_buffer
        invalidate_screen_buffer()
    except Exception:
        pass


_ticker_thread: threading.Thread | None = None
_ticker_stop_event: threading.Event = threading.Event()
_loading_cancel_event: threading.Event = threading.Event()
_loading_input_lock: threading.Lock = threading.Lock()
_loading_tty_file = None
_loading_tty_fd: int | None = None
_loading_tty_old_term = None
_loading_tty_old_blocking: bool | None = None
_loading_stdin_fd: int | None = None
_loading_stdin_old_term = None
_loading_stdin_old_blocking: bool | None = None
_loading_input_thread: threading.Thread | None = None
_loading_input_stop_event: threading.Event = threading.Event()


def reset_loading_cancel() -> None:
    """Clear any previous loading cancellation request."""
    _loading_cancel_event.clear()
    _player_ui_state["loading_cancel_requested"] = False


def request_loading_cancel() -> None:
    """Ask the current loading flow to stop and return to details."""
    _player_ui_state["loading_cancel_requested"] = True
    _loading_cancel_event.set()


def _handle_loading_cancel_bytes(key: bytes) -> bool:
    if b"q" in key or b"Q" in key or b"\x1b" in key:
        _logger.debug("Player loading cancel requested key=%r", key)
        request_loading_cancel()
        return True
    return False


def _open_loading_input() -> None:
    global _loading_tty_file, _loading_tty_fd, _loading_tty_old_term, _loading_tty_old_blocking
    if _loading_tty_fd is not None:
        return
    try:
        import termios

        fd = os.open("/dev/tty", os.O_RDWR)
        old_blocking = os.get_blocking(fd)
        os.set_blocking(fd, False)
        tty_file = os.fdopen(fd, "rb+", buffering=0)
        old_term = termios.tcgetattr(fd)
        _apply_loading_termios(fd, termios)
    except Exception:
        _logger.debug("Player loading cancel input unavailable", exc_info=True)
        try:
            tty_file.close()  # type: ignore[name-defined]
        except Exception:
            pass
        return
    _loading_tty_file = tty_file
    _loading_tty_fd = fd
    _loading_tty_old_term = old_term
    _loading_tty_old_blocking = old_blocking
    _logger.debug("Player loading cancel input using /dev/tty fd=%s", fd)


def _apply_loading_termios(fd: int, termios_module) -> None:
    new_term = termios_module.tcgetattr(fd)
    new_term[3] = new_term[3] & ~(termios_module.ECHO | termios_module.ICANON)
    new_term[6][termios_module.VMIN] = 0
    new_term[6][termios_module.VTIME] = 0
    termios_module.tcsetattr(fd, termios_module.TCSANOW, new_term)


def _open_loading_stdin_input() -> int | None:
    global _loading_stdin_fd, _loading_stdin_old_term, _loading_stdin_old_blocking
    try:
        import termios

        fd = sys.stdin.fileno()
        if not sys.stdin.isatty():
            return None
        if _loading_stdin_fd is None:
            old_term = termios.tcgetattr(fd)
            old_blocking = os.get_blocking(fd)
            os.set_blocking(fd, False)
            _loading_stdin_fd = fd
            _loading_stdin_old_term = old_term
            _loading_stdin_old_blocking = old_blocking
            _logger.debug("Player loading cancel input using stdin fd=%s", fd)
        _apply_loading_termios(fd, termios)
    except Exception:
        _logger.debug("Player loading stdin cancel input unavailable", exc_info=True)
        return None
    return fd


def _close_loading_stdin_input() -> None:
    global _loading_stdin_fd, _loading_stdin_old_term, _loading_stdin_old_blocking
    fd = _loading_stdin_fd
    old_term = _loading_stdin_old_term
    old_blocking = _loading_stdin_old_blocking
    _loading_stdin_fd = None
    _loading_stdin_old_term = None
    _loading_stdin_old_blocking = None
    if fd is None:
        return
    try:
        if old_term is not None:
            import termios
            termios.tcsetattr(fd, termios.TCSANOW, old_term)
    except Exception:
        pass
    try:
        if old_blocking is not None:
            os.set_blocking(fd, old_blocking)
    except Exception:
        pass


def _close_loading_input() -> None:
    global _loading_tty_file, _loading_tty_fd, _loading_tty_old_term, _loading_tty_old_blocking
    fd = _loading_tty_fd
    old_term = _loading_tty_old_term
    old_blocking = _loading_tty_old_blocking
    tty_file = _loading_tty_file
    _loading_tty_file = None
    _loading_tty_fd = None
    _loading_tty_old_term = None
    _loading_tty_old_blocking = None
    if fd is not None and old_term is not None:
        try:
            import termios
            termios.tcsetattr(fd, termios.TCSADRAIN, old_term)
        except Exception:
            pass
    if fd is not None and old_blocking is not None:
        try:
            os.set_blocking(fd, old_blocking)
        except Exception:
            pass
    if tty_file is not None:
        try:
            tty_file.close()
        except Exception:
            pass


def _poll_loading_cancel_input() -> None:
    stdin_fd = _open_loading_stdin_input()
    fds = [stdin_fd] if stdin_fd is not None else []
    if not fds:
        if _loading_tty_fd is None and sys.stdout.isatty():
            _open_loading_input()
        if _loading_tty_fd is not None:
            fds = [_loading_tty_fd]
    if not fds:
        return
    with _loading_input_lock:
        try:
            import select
            readable, _, _ = select.select(fds, [], [], 0)
            if not readable:
                return
            chunks = []
            for fd in readable:
                try:
                    chunks.append(os.read(fd, 32))
                except BlockingIOError:
                    pass
                except Exception:
                    pass
            key = b"".join(chunks)
        except Exception:
            _logger.debug("Player loading cancel input poll failed", exc_info=True)
            return
    _handle_loading_cancel_bytes(key)


def _start_loading_input_thread() -> None:
    """Start a dedicated loading-screen key reader for q/Esc cancellation."""
    global _loading_input_thread
    if _loading_input_thread and _loading_input_thread.is_alive():
        return
    _loading_input_stop_event.clear()

    def _loop() -> None:
        while not _loading_input_stop_event.is_set():
            stdin_fd = _open_loading_stdin_input()
            fds = [stdin_fd] if stdin_fd is not None else []
            if not fds:
                if _loading_tty_fd is None and sys.stdout.isatty():
                    _open_loading_input()
                if _loading_tty_fd is not None:
                    fds = [_loading_tty_fd]
            if not fds:
                _loading_input_stop_event.wait(0.1)
                continue
            with _loading_input_lock:
                try:
                    readable, _, _ = select.select(fds, [], [], 0.1)
                    if not readable:
                        continue
                    chunks = []
                    for fd in readable:
                        try:
                            chunks.append(os.read(fd, 32))
                        except BlockingIOError:
                            pass
                    key = b"".join(chunks)
                except Exception:
                    _logger.debug("Player loading cancel input listener failed", exc_info=True)
                    _loading_input_stop_event.wait(0.1)
                    continue
            _handle_loading_cancel_bytes(key)

    _loading_input_thread = threading.Thread(target=_loop, daemon=True)
    _loading_input_thread.start()
    _logger.debug("Player loading cancel input listener started")


def _stop_loading_input_thread() -> None:
    _loading_input_stop_event.set()
    if (
        _loading_input_thread
        and _loading_input_thread.is_alive()
        and threading.current_thread() is not _loading_input_thread
    ):
        _loading_input_thread.join(timeout=0.3)
    _close_loading_stdin_input()


def loading_cancel_requested() -> bool:
    """Return whether the user cancelled the active loading screen."""
    _poll_loading_cancel_input()
    requested = bool(_player_ui_state.get("loading_cancel_requested")) or _loading_cancel_event.is_set()
    if requested:
        _logger.debug("Player loading cancel flag observed")
    return requested


def start_loading_ticker(poster_manager=None, ui=None) -> None:
    """Start background ticker thread that smoothly animates the spinner and timer."""
    global _ticker_thread
    from ..context import FLAGS
    if getattr(FLAGS, "plain_mode", False) or not sys.stdout.isatty():
        return
    _ticker_stop_event.clear()
    _start_loading_input_thread()
    if _ticker_thread and _ticker_thread.is_alive():
        return

    def _loop():
        try:
            while not _ticker_stop_event.is_set():
                s = _player_ui_state
                if not s.get("active") or s.get("mpv_props"):
                    break
                row_spinner = s.get("_row_loading_spinner")
                w = s.get("_cached_w", 0)
                if row_spinner and w:
                    try:
                        cur_w, cur_h = os.get_terminal_size(sys.stdin.fileno())
                    except Exception:
                        cur_w, cur_h = w, s.get("_cached_h", 0)

                    if cur_w == w and cur_h == s.get("_cached_h", 0):
                        p_name = s.get("loading_provider_name", "")
                        p_str = f" from {p_name}" if p_name else ""
                        start_t = s.get("loading_start_time")
                        elapsed = f" ({int(time.time() - start_t)}s)" if start_t else ""
                        try:
                            from .display import _spinner_style
                            spin = spinner_frame(_spinner_style)
                        except Exception:
                            spin = spinner_frame()
                        spin_line = f"\033[1;36m{spin} Loading stream{p_str}...{elapsed}\033[0m"
                        _safe_stdout_write(
                            f"\033[{row_spinner};1H\033[2K{_fit_terminal_line(spin_line, w)}\033[?25l"
                        )
                    else:
                        try:
                            render(poster_manager=poster_manager, ui=ui)
                        except Exception:
                            pass
                else:
                    try:
                        render(poster_manager=poster_manager, ui=ui)
                    except Exception:
                        pass
                _ticker_stop_event.wait(0.1)
        finally:
            _stop_loading_input_thread()
            _close_loading_input()

    _ticker_thread = threading.Thread(target=_loop, daemon=True)
    _ticker_thread.start()


def stop_loading_ticker() -> None:
    """Stop the background loading ticker thread."""
    _ticker_stop_event.set()
    _stop_loading_input_thread()
    if _ticker_thread and _ticker_thread.is_alive() and threading.current_thread() is not _ticker_thread:
        _ticker_thread.join(timeout=0.3)
    _close_loading_input()


def deactivate(close_alt: bool = False) -> None:
    """Tear down the player overlay.

    Parameters
    ----------
    close_alt:
        If ``True``, also restore the normal terminal screen (leave
        alt-screen mode).  Pass ``True`` when fully leaving the player,
        ``False`` when transitioning to the action menu.
    """
    stop_loading_ticker()
    try:
        from .picker import invalidate_screen_buffer
        invalidate_screen_buffer()
    except Exception:
        pass
    try:
        terminal_images.clear_now()
    except Exception:
        pass
    from ..context import FLAGS
    if getattr(FLAGS, "plain_mode", False) or not sys.stdin.isatty():
        _player_ui_state["active"] = False
        return
    if close_alt:
        # Exit alt screen
        _safe_stdout_write("\033[?1049l\033[?25h")
    else:
        _safe_stdout_write("\033[2J\033[H\033[?25l")
    _player_ui_state["active"] = False


def add_status_line(message: str, color: str = "\033[94m") -> bool:
    """Append a status line to the loading overlay and trigger a redraw.

    Returns ``True`` if the player screen is currently active (and the
    message was appended), ``False`` otherwise.
    """
    s = _player_ui_state
    if not s["active"]:
        return False
    from ..context import FLAGS
    if getattr(FLAGS, "plain_mode", False) or not sys.stdin.isatty():
        if sys.stdout.isatty():
            _safe_stdout_write(f"{color}{message}\033[0m\n")
        else:
            from .plain_picker import strip_ansi
            _safe_stdout_write(f"{strip_ansi(message)}\n")
        return True
    s["status_lines"].append(f"{color}{message}\033[0m")
    if len(s["status_lines"]) > 8:
        s["status_lines"].pop(0)
    render()
    return True


def get_player_poster(show: dict | None, ui: UiState | None = None) -> str:
    """Return the poster string for *show*, updating the hover state if *ui* is given."""
    if not show:
        return ""
    if ui is not None:
        ui.hovered_show_id = show.get("_id") or show.get("id")
    return ""  # Poster fetching is handled by PosterManager in app.py


def _playback_episode_summary(show: dict | None, player_state: dict, ttype: str = "sub") -> str:
    if not isinstance(show, dict):
        return ""

    fmt = str(show.get("format") or show.get("type") or "").upper()
    total = _positive_int(show.get("episodeCount"))

    # Hide episode count for movies or single-episode titles
    if fmt == "MOVIE" or total == 1:
        return ""

    available = None
    try:
        available = int((show.get("availableEpisodes") or {}).get(ttype))
    except (TypeError, ValueError):
        available = None
    if available is None:
        next_ep = _positive_int(show.get("_next_airing_ep"))
        if next_ep:
            available = max(0, next_ep - 1)
    if available is None:
        available = _positive_int(player_state.get("total_eps"))

    if available is not None:
        return f"Episodes {available}/{total if total else '?'}"
    if total:
        return f"Total {total}"
    return ""


def render(
    poster_manager: PosterManager | None = None,
    ui: UiState | None = None,
    enter_alt_screen_fn=None,
) -> None:
    """Render (or refresh) the player overlay on the terminal.

    Parameters
    ----------
    poster_manager:
        The app's :class:`~media_cli.ui.poster.PosterManager` instance.
        If supplied, the poster is fetched and rendered.
    ui:
        Current :class:`~media_cli.context.UiState`; used to update
        ``hovered_show_id`` for the poster manager.
    enter_alt_screen_fn:
        Callable that switches to the alternate terminal screen.  If
        ``None``, the switch is skipped (caller is responsible).
    """
    s = _player_ui_state
    if not s["active"]:
        return

    from ..context import FLAGS
    if getattr(FLAGS, "plain_mode", False) or not sys.stdin.isatty():
        return

    # Fall back to cached poster_manager / ui if caller didn't pass them
    if poster_manager is None:
        poster_manager = s.get("_poster_manager")
    else:
        s["_poster_manager"] = poster_manager
    if ui is None:
        ui = s.get("_ui")
    else:
        s["_ui"] = ui

    if enter_alt_screen_fn is not None:
        enter_alt_screen_fn()

    try:
        w, h = os.get_terminal_size(sys.stdin.fileno())
    except Exception:
        w, h = 80, 24

    show = s["show"]
    title = get_show_display_title(show) if show else "Unknown"
    alt_title = get_display_titles(show, title) if show else ""

    # Target episode: e.g. "EP 1" or "EP 1/12"
    target_ep = s.get("current_ep_label") or str(s.get("current_ep", "") or "")
    from ..domain.episodes import clean_episode_identifier
    clean_target_ep = clean_episode_identifier(target_ep) if target_ep else ""
    total = _positive_int(show.get("episodeCount")) if isinstance(show, dict) else None

    if clean_target_ep:
        target_ep_str = f"EP {clean_target_ep}/{total}" if total else f"EP {clean_target_ep}"
    else:
        target_ep_str = f"EP {total}" if total else ""

    ttype = s.get("ttype") or (getattr(ui, "ui_ttype_ctx", None) if ui else None) or "sub"

    meta_parts = []
    if isinstance(show, dict):
        status_lbl = anilist_list_status_label(show) or anime_status_label(show)
        if status_lbl:
            meta_parts.append(status_lbl)

    anime_type = str(show.get("type") or show.get("format") or "").upper() if isinstance(show, dict) else ""
    if anime_type and anime_type != "UNKNOWN":
        meta_parts.append(anime_type)

    if ttype:
        meta_parts.append(str(ttype).upper())

    if target_ep_str:
        meta_parts.append(f"\033[38;5;252m{target_ep_str}\033[38;5;248m")

    if isinstance(show, dict):
        from ..domain.metadata import format_available_episodes
        avail_str = format_available_episodes(show, ttype)
        if avail_str:
            meta_parts.append(avail_str)

    def _ext_year(val):
        if isinstance(val, dict):
            return val.get("year")
        if isinstance(val, str):
            import re
            m = re.search(r'\b(20\d{2}|19\d{2})\b', val)
            return m.group(1) if m else val
        return None

    if isinstance(show, dict):
        status = show.get("_anilist_status") or show.get("status")
        if any(term in str(show.get("status") or "").upper() for term in ("FINISHED", "COMPLETED", "ENDED")):
            status = "FINISHED"
        years = format_years(_ext_year(show.get("airedStart")), _ext_year(show.get("airedEnd")), status)
        if years:
            meta_parts.append(years)
        score = show.get("score")
        if score:
            meta_parts.append(f"★ {score}")

    meta_line = " \u2022 ".join(meta_parts)
    next_air = format_next_airing(show) if isinstance(show, dict) else ""
    next_air_line = f"\033[38;5;117m{next_air}\033[0m" if next_air else ""

    si = s.get("stream_info", {})
    mirror = si.get("mirror")
    stream_str = ""
    if mirror:
        pref_star = " \u2022 \033[33mPreferred \u2605" if si.get("is_pref") else ""
        stream_str = f"{mirror}{pref_star}\033[0m"

    props = s.get("mpv_props")
    is_playing = props is not None

    # ── Check for terminal resize — invalidates cached row positions ──
    try:
        w, h = os.get_terminal_size(sys.stdin.fileno())
    except Exception:
        w, h = 80, 24
    if w != s.get("_cached_w") or h != s.get("_cached_h"):
        s["_row_state"] = None  # force full render

    # --- Poster ---
    poster_raw = ""
    if poster_manager is not None and show and getattr(poster_manager, "enabled", lambda: True)():
        if ui is not None:
            ui.hovered_show_id = show.get("_id") or show.get("id")
            ui.hovered_show_obj = show
        poster_raw = poster_manager.get(show) or ""

    native_poster = poster_raw if _poster_uses_native_protocol(poster_raw) else ""
    poster_key = (
        hashlib.sha256(poster_raw.encode("utf-8", errors="ignore")).hexdigest()
        if poster_raw
        else None
    )
    poster_changed = poster_key != s.get("_last_poster_key")
    s["_last_poster_key"] = poster_key
    if native_poster and poster_changed:
        terminal_images.mark_active()

    content = []

    try:
        from ..core.storage import is_incognito as is_incognito_fn  # lazy import to avoid circular
        if is_incognito_fn():
            content.append("")
            content.append("\033[38;2;155;125;185mINCOGNITO\033[0m")
    except Exception:
        pass

    # ── Unified Header ──
    content.append("")
    for tl in _wrap_title(title, w - 4, 2).splitlines():
        content.append(f"{TITLE_COLOR}{tl}{RESET}")
    if alt_title:
        for atl in _wrap_title(alt_title, w - 4, 2).splitlines():
            content.append(f"{TEXT_COLOR}{atl}\033[0m")
    if meta_line:
        content.append(f"{TEXT_COLOR}{meta_line}\033[0m")
    if next_air_line:
        content.append(next_air_line)

    if is_playing:
        state_str = "\u23f8 Paused" if props.get("pause") else "\u25b6 Playing"
        pt_sec = props.get("playback-time", 0) or 0
        dur_sec = props.get("duration", 0) or 0
        rem_sec = dur_sec - pt_sec if dur_sec > 0 else 0

        bar = _thin_progress_bar(pt_sec, dur_sec, w - 4)
        time_line = f"{_fmt_time(pt_sec)} / {_fmt_time(dur_sec)}"
        if dur_sec > 0:
            time_line += f"  •  -{_fmt_time(rem_sec)}"

        content.append("")
        content.append(f"{SECTION_LABEL}Currently Playing{RESET}")
        label = s.get("current_ep_label") or str(s["current_ep"])
        from media_cli.domain.episodes import episode_label
        ep_str = episode_label(label)
        if stream_str:
            content.append(f"{TEXT_COLOR}{ep_str} • {stream_str}\033[0m")
        else:
            content.append(f"{TEXT_COLOR}{ep_str}\033[0m")
        content.append("")
        _idx_state = len(content)
        content.append(f"\033[1;36m{state_str}\033[0m")
        _idx_bar = len(content)
        content.append(bar)
        _idx_time = len(content)
        content.append(f"{TEXT_COLOR}{time_line}\033[0m")
        detail_lines = []
        genres = show.get("genres") if isinstance(show, dict) else None
        if not genres and isinstance(show, dict):
            genres = show.get("_provider_genres")
        if isinstance(genres, list):
            genre_text = ", ".join(str(item) for item in genres[:5] if item)
        else:
            genre_text = str(genres or "").strip()
        if genre_text:
            detail_lines.append(f"{SECTION_LABEL}Genres{RESET}")
            detail_lines.append(f"{TEXT_COLOR}" + genre_text.replace(", ", " • ") + "\033[0m")
        description = str(show.get("description") or "").strip() if isinstance(show, dict) else ""
        if description:
            import html
            description = html.unescape(description)
            description = re.sub(r"<[^>]+>", " ", description)
            description = re.sub(r"\s+", " ", description).strip()

            poster_rows = POSTER_HEIGHT if poster_raw else 0
            extra_padding = 5 if detail_lines else 4
            used_lines = poster_rows + len(content) + len(detail_lines) + extra_padding
            max_desc_lines = max(1, h - used_lines)

            if max_desc_lines > 0:
                if detail_lines:
                    detail_lines.append("")
                detail_lines.append(f"{SECTION_LABEL}Description{RESET}")
                detail_lines.extend(
                    f"{TEXT_COLOR}{line}\033[0m"
                    for line in _wrap_title(description, w - 4, max_desc_lines).splitlines()
                )
        if detail_lines:
            content.append("")
            content.extend(detail_lines)
        content.append("")
        content.append("\033[38;5;244mQ Quit   Shift+Left Previous   Shift+Right Next\033[0m")
    else:
        content.append("")
        content.append(f"{SECTION_LABEL}Status{RESET}")
        p_name = s.get("loading_provider_name", "")
        p_str = f" from {p_name}" if p_name else ""
        start_t = s.get("loading_start_time")
        elapsed = f" ({int(time.time() - start_t)}s)" if start_t else ""
        try:
            from .display import _spinner_style
            spin = spinner_frame(_spinner_style)
        except Exception:
            spin = spinner_frame()
        _idx_loading_spinner = len(content)

        content.append(f"\033[1;36m{spin} Loading stream{p_str}...{elapsed}\033[0m")
        content.append("\033[38;5;244mq/Esc cancel and return to details\033[0m")
        content.append("")
        for sl in s["status_lines"]:
            content.append(sl)
        if s.get("countdown_message"):
            content.append("")
            content.append(s["countdown_message"])

    out = []
    if poster_raw:
        poster_lines = _poster_symbol_lines(poster_raw, POSTER_HEIGHT, w)
        for row in range(POSTER_HEIGHT):
            line = poster_lines[row] if row < len(poster_lines) else ""
            if line:
                out.append(f"\033[2K{line}")
            elif poster_changed:
                out.append("\033[2K")
            else:
                out.append("")

    for line in content:
        out.append(f"\033[2K{_fit_terminal_line(line, w)}")

    if out:
        clear_images = ""
        if not s.get("_cleared_terminal_image"):
            clear_images = terminal_images.clear_if_active()
            s["_cleared_terminal_image"] = True
        overlay = f"\033[1;1H{native_poster}" if native_poster and poster_changed else ""
        _safe_stdout_write(
            clear_images + "\033[H" + "\r\n".join(out) + "\033[J"
            + overlay + "\033[1;1H\033[?25l"
        )

    # ── Cache terminal size and dynamic row positions for partial render ──
    s["_cached_w"] = w
    s["_cached_h"] = h
    poster_row_count = POSTER_HEIGHT if poster_raw else 0
    content_start = poster_row_count + 1
    if is_playing:
        s["_row_state"] = content_start + _idx_state
        s["_row_bar"]   = content_start + _idx_bar
        s["_row_time"]  = content_start + _idx_time
        s["_row_loading_spinner"] = None
    else:
        s["_row_state"] = None
        s["_row_bar"]   = None
        s["_row_time"]  = None
        s["_row_loading_spinner"] = content_start + _idx_loading_spinner


def update_mpv_props(props: dict | None) -> None:
    """Store the latest mpv property snapshot and trigger a redraw.

    Called from the ``MpvIpc`` redraw callback.
    """
    s = _player_ui_state
    s["mpv_props"] = props

    # ── Attempt a cheap partial render first ──────────────────────────
    row_state = s.get("_row_state")
    row_bar   = s.get("_row_bar")
    row_time  = s.get("_row_time")
    w         = s.get("_cached_w", 0)

    if props is not None and row_state and row_bar and row_time and w:
        # Check terminal hasn't been resized since the last full render
        try:
            cur_w, cur_h = os.get_terminal_size(sys.stdin.fileno())
        except Exception:
            cur_w, cur_h = w, s.get("_cached_h", 0)

        if cur_w == w and cur_h == s.get("_cached_h", 0):
            state_str = "\u23f8 Paused" if props.get("pause") else "\u25b6 Playing"
            pt_sec  = props.get("playback-time", 0) or 0
            dur_sec = props.get("duration", 0) or 0
            rem_sec = dur_sec - pt_sec if dur_sec > 0 else 0
            bar = _thin_progress_bar(pt_sec, dur_sec, w - 4)
            time_line = f"{_fmt_time(pt_sec)} / {_fmt_time(dur_sec)}"
            if dur_sec > 0:
                time_line += f"  \u2022  -{_fmt_time(rem_sec)}"

            _safe_stdout_write(
                f"\033[{row_state};1H\033[2K\033[1;36m{_fit_terminal_line(state_str, w)}\033[0m"
                f"\033[{row_bar};1H\033[2K{_fit_terminal_line(bar, w)}"
                f"\033[{row_time};1H\033[2K\033[38;5;250m{_fit_terminal_line(time_line, w)}\033[0m"
                "\033[?25l"
            )
            return

    # Fall back to full render (resize, first tick, or no cached rows)
    render()
