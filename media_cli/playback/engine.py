"""Playback engine coordinator, MPV IPC player lifecycle, and summary formatters."""

from __future__ import annotations

import atexit
import os
import subprocess
import sys
from contextlib import contextmanager
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
        from ..ui.terminal_output import write_terminal
        write_terminal("\033[2J\033[H\033[?25l")
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


def _torrent_progress(message: str) -> None:
    try:
        from media_cli.ui.player_screen import loading_cancel_requested
        if loading_cancel_requested():
            from media_cli.torrents.runtime import TorrentCancelled
            raise TorrentCancelled("Torrent loading cancelled.")
    except Exception as exc:
        if exc.__class__.__name__ == "TorrentCancelled":
            raise
    try:
        from media_cli.ui.player_screen import add_status_line
        add_status_line(message, "\033[1;92m")
    except Exception:
        pass


_ipc_player = MpvIpc(_redraw_player)
atexit.register(_ipc_player.quit)


def is_termux() -> bool:
    return (os.environ.get("PREFIX", "").startswith("/data/data/com.termux")
            or os.path.exists("/data/data/com.termux"))


def _prepare_owned_playback_resource(prepare, release):
    from threading import Event, Lock

    from ..ui.player_screen import run_loading_work

    retired = Event()
    lock = Lock()
    pending = None

    def work():
        nonlocal pending
        resource = prepare(retired.is_set)
        with lock:
            abandoned = retired.is_set()
            if not abandoned:
                pending = resource
        if abandoned:
            release(resource)
        return resource

    try:
        result = run_loading_work(work)
        with lock:
            pending = None
        return result
    finally:
        with lock:
            retired.set()
            unclaimed = pending
            pending = None
        if unclaimed is not None:
            release(unclaimed)


def _prepare_torrent_for_playback(stream: dict, *, progress_callback=None, cancel_check=None):
    from copy import deepcopy

    from ..core.reporting import progress
    from ..torrents.runtime import TorrentCancelled, prepare_torrent_stream
    from ..ui.transition import PreparationCancelled

    if cancel_check and cancel_check():
        raise TorrentCancelled("Torrent loading cancelled.")
    snapshot = deepcopy(stream)
    try:
        return _prepare_owned_playback_resource(
            lambda cancelled: prepare_torrent_stream(snapshot, progress_callback=progress, cancel_check=cancelled),
            lambda resource: resource.close(),
        )
    except PreparationCancelled as exc:
        raise TorrentCancelled("Torrent loading cancelled.") from exc


def _prepare_proxy_for_playback(url, referer, headers=None, **kwargs):
    from copy import deepcopy

    from ..media.local_proxy import start_local_proxy, stop_local_proxy
    from ..ui.transition import PreparationCancelled

    request_headers, options = deepcopy(headers), deepcopy(kwargs)
    try:
        return _prepare_owned_playback_resource(
            lambda cancelled: start_local_proxy(url, referer, request_headers, **options),
            lambda result: stop_local_proxy(result[1]),
        )
    except PreparationCancelled as exc:
        raise desktop_playback.PlaybackPreparationCancelled from exc


def _launch_android_player(command):
    from threading import Event

    from ..services.requirements import require_executable
    from ..ui.actions import ActionAvailability, ActionBindings, ResolvedAction
    from ..ui.loading_model import LoadingInteraction, LoadingOwner, LoadingPlacement, LoadingTaskSpec
    from ..ui.player_screen import check_loading_handoff_input, playback_input_scope, run_loading_work
    from ..ui.transition import PreparationCancelled
    from ..ui.view_model import ViewAction
    from .android import AndroidPreparationCancelled

    require_executable(command[0], "Android player launch")
    task = LoadingTaskSpec(
        "player-launch", LoadingOwner("player", 0), "Opening player...",
        LoadingInteraction.BLOCKING, LoadingPlacement.PLAYER_STATUS,
        ActionBindings((ResolvedAction(
            ViewAction("back", "Cancel", ("q", "Q", "ESC")), ActionAvailability.BLOCKED,
            "Already opening the player. Ctrl+C stops Anyplay.",
        ),)), delay=0,
    )
    try:
        with playback_input_scope():
            check_loading_handoff_input()
            # Dispatch is irreversible: only the owning thread may start it.
            process = subprocess.Popen(
                tuple(command), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            waiting = Event()

            def wait():
                waiting.set()
                stdout, stderr = process.communicate()
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

            try:
                return run_loading_work(wait, task=task)
            finally:
                try:
                    if process.poll() is None:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
                        process.wait(timeout=1)
                finally:
                    if not waiting.is_set():
                        for pipe in (process.stdout, process.stderr):
                            if pipe is not None:
                                pipe.close()
    except PreparationCancelled as exc:
        raise AndroidPreparationCancelled from exc


def _prepare_android_proxy(start, *args, **kwargs):
    from copy import deepcopy

    from ..media.local_proxy import stop_local_proxy
    from ..ui.transition import PreparationCancelled
    from .android import AndroidPreparationCancelled

    arguments, options = deepcopy(args), deepcopy(kwargs)
    try:
        return _prepare_owned_playback_resource(
            lambda cancelled: start(*arguments, **options),
            lambda result: stop_local_proxy(result[1]),
        )
    except PreparationCancelled as exc:
        raise AndroidPreparationCancelled from exc


def _fetch_android_skip_times(mal_id, episode, *, episode_length):
    from ..media.aniskip import fetch_skip_times
    from ..ui.player_screen import run_loading_work
    from ..ui.transition import PreparationCancelled
    from .android import AndroidPreparationCancelled

    try:
        return run_loading_work(
            lambda: fetch_skip_times(mal_id, episode, episode_length=episode_length)
        )
    except PreparationCancelled as exc:
        raise AndroidPreparationCancelled from exc


def _prepare_subtitles_for_playback(subtitles: list[dict], *, headers=None, referer=None, video_duration_s=0.0):
    from copy import deepcopy
    from threading import Event

    from ..media.subtitle_scorer import filter_subtitles
    from ..ui.player_screen import run_loading_work
    from ..ui.transition import PreparationCancelled

    items, request_headers = deepcopy(subtitles), deepcopy(headers)
    retired = Event()

    def prepare():
        return filter_subtitles(
            items, headers=request_headers, referer=referer,
            video_duration_s=video_duration_s, cancel_check=retired.is_set,
        )

    try:
        return run_loading_work(prepare)
    except PreparationCancelled as exc:
        raise desktop_playback.PlaybackPreparationCancelled from exc
    finally:
        retired.set()


def _prepare_mpv_subtitle_files(subtitles, *, headers=None, referer=None, cancel_check=None):
    from copy import deepcopy

    from ..media.mpv_subtitle_files import SubtitleFilesCancelled, prepare_subtitle_files
    from ..ui.transition import PreparationCancelled

    items, request_headers = deepcopy(subtitles), deepcopy(headers)
    try:
        return _prepare_owned_playback_resource(
            lambda retired: prepare_subtitle_files(
                items, headers=request_headers, referer=referer,
                cancel_check=lambda: retired() or bool(cancel_check and cancel_check()),
            ),
            lambda resource: resource.close(),
        )
    except (PreparationCancelled, SubtitleFilesCancelled) as exc:
        raise desktop_playback.PlaybackPreparationCancelled from exc


def _prepare_android_subtitles(subtitles: list[dict], *, headers=None, referer=None, video_duration_s=0.0):
    from .android import AndroidPreparationCancelled

    try:
        return _prepare_subtitles_for_playback(
            subtitles, headers=headers, referer=referer, video_duration_s=video_duration_s,
        )
    except desktop_playback.PlaybackPreparationCancelled as exc:
        raise AndroidPreparationCancelled from exc


def _start_player_for_playback(ipc_player):
    from ..ui.transition import PreparationCancelled
    from .mpv import MpvStartupCancelled

    if ipc_player.process is not None and ipc_player.process.poll() is None and ipc_player.client is not None:
        return

    def prepare(cancelled):
        candidate = MpvIpc()
        candidate.audio_only = getattr(ipc_player, "audio_only", False)
        try:
            candidate.start(cancel_check=cancelled)
            return candidate
        except BaseException:
            candidate.quit()
            raise

    try:
        candidate = _prepare_owned_playback_resource(prepare, lambda player: player.quit())
    except (PreparationCancelled, MpvStartupCancelled) as exc:
        raise desktop_playback.PlaybackPreparationCancelled from exc
    try:
        ipc_player.adopt_startup(candidate)
    finally:
        candidate.quit()


@contextmanager
def _playback_input_scope():
    from ..ui.player_screen import playback_input_scope
    from ..ui.transition import PreparationCancelled

    try:
        with playback_input_scope() as managed:
            yield managed
    except PreparationCancelled as exc:
        raise desktop_playback.PlaybackPreparationCancelled from exc


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
    custom_skip_intervals: list[dict] | None = None,
    start_time_override: float | None = None,
    cancel_check: Callable[[], bool] | None = None,
):
    from ..domain.titles import title_provider_key

    mirror_provider_id = title_provider_key(_player_ui_state.get("show"), default="")
    if cancel_check is None:
        try:
            from media_cli.ui.player_screen import loading_cancel_requested
            cancel_check = loading_cancel_requested
        except Exception:
            cancel_check = None
    return desktop_playback.play_desktop(
        _ipc_player,
        title,
        ep,
        stream,
        get_resume_time=lambda sid, episode: get_resume_time(sid, episode, mirror_provider_id),
        get_preferred_mirror=lambda sid: get_preferred_mirror(
            sid, mirror_provider_id,
        ),
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
        custom_skip_intervals=custom_skip_intervals,
        start_time_override=start_time_override,
        torrent_progress_callback=_torrent_progress,
        cancel_check=cancel_check,
        prepare_torrent_fn=_prepare_torrent_for_playback,
        prepare_subtitles_fn=_prepare_subtitles_for_playback,
        prepare_subtitle_files_fn=_prepare_mpv_subtitle_files,
        prepare_proxy_fn=_prepare_proxy_for_playback,
        start_player_fn=_start_player_for_playback,
        background_subtitles=True,
        playback_input_scope=_playback_input_scope,
    )


def play_local_video(path: str, player: str = "mpv"):
    return local_playback.play_local_video(
        path,
        player,
        termux=is_termux(),
        error=err,
    )
