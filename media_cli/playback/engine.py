"""Playback engine coordinator, MPV IPC player lifecycle, and summary formatters."""

from __future__ import annotations

import atexit
import os
import sys
from typing import Callable

from ..core.reporting import err
from ..core.storage import get_preferred_mirror, get_resume_time
from ..domain.metadata import positive_int as _positive_int
from ..playback import desktop as desktop_playback
from ..playback import local as local_playback
from ..playback.mpv import MpvIpc
from ..ui.display import _get_poster, exit_alt_screen
from ..ui.player_screen import _player_ui_state


def _exit_player_screen(close_alt: bool = False) -> None:
    try:
        from media_cli.ui.player_screen import stop_loading_ticker
        stop_loading_ticker()
    except Exception:
        pass
    try:
        from media_cli.ui.picker import invalidate_screen_buffer
        invalidate_screen_buffer()
    except Exception:
        pass
    try:
        from media_cli.ui import terminal_images
        terminal_images.clear_now()
    except Exception:
        pass
    from ..context import FLAGS
    if getattr(FLAGS, "plain_mode", False) or not sys.stdin.isatty():
        _player_ui_state["active"] = False
        return
    if close_alt:
        exit_alt_screen()
    else:
        sys.stdout.write("\033[2J\033[H\033[?25l")
        sys.stdout.flush()
    _player_ui_state["active"] = False


def _get_player_poster(show: dict) -> str:
    if not show:
        return ""
    return _get_poster(show) or ""


def _playback_episode_summary(show: dict, player_state: dict, ttype: str = "sub") -> str:
    if not isinstance(show, dict):
        return ""

    fmt = str(show.get("format") or show.get("type") or "").upper()
    total = _positive_int(show.get("episodeCount"))
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
        if total is not None and total > available:
            return f"{available}/{total}"
        return str(available)

    if total is not None:
        return str(total)
    return ""


def _redraw_player(props: dict) -> None:
    try:
        from media_cli.ui.player_screen import update_mpv_props
        update_mpv_props(props)
    except ImportError:
        pass


_ipc_player = MpvIpc(_redraw_player)
atexit.register(_ipc_player.quit)


def is_termux() -> bool:
    return (os.environ.get("PREFIX", "").startswith("/data/data/com.termux")
            or os.path.exists("/data/data/com.termux"))


def play_desktop(
    title: str,
    ep: str,
    stream: dict,
    fetch_callback: Callable | None = None,
    total_eps: int = 1,
    is_binge: bool = False,
    show_id: str | None = None,
    osd_msg: str = "",
    episode_index: int = 0,
    next_episode: str | None = None,
    mal_id: int | None = None,
    aniskip_enabled: bool = True,
    aniskip_auto: bool = True,
    start_time_override: float | None = None,
):
    return desktop_playback.play_desktop(
        _ipc_player,
        title,
        ep,
        stream,
        get_resume_time=get_resume_time,
        get_preferred_mirror=get_preferred_mirror,
        update_stream_info=lambda info: _player_ui_state.__setitem__(
            "stream_info", info
        ),
        fetch_callback=fetch_callback,
        total_eps=total_eps,
        is_binge=is_binge,
        show_id=show_id,
        osd_msg=osd_msg,
        episode_index=episode_index,
        next_episode=next_episode,
        mal_id=mal_id,
        aniskip_enabled=aniskip_enabled,
        aniskip_auto=aniskip_auto,
        start_time_override=start_time_override,
    )


def play_local_video(path: str, player: str = "mpv"):
    return local_playback.play_local_video(
        path,
        player,
        termux=is_termux(),
        error=err,
    )
