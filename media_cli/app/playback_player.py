"""Playback handler coordinating stream resolution, player runtime, and sync."""

from __future__ import annotations

import os
from decimal import Decimal, InvalidOperation
import re
import sys
import time
import logging
from typing import Any, Callable

from .. import app_core
from ..context import CliFlags, MachineState, UiState
from ..core import streams as streams_mod
from ..domain.episodes import (
    clean_episode_identifier,
    episode_id_at,
    episode_index_for_id,
    episode_progress_number,
    parse_episode_dual_numbers,
)
from ..domain.titles import get_show_display_title
from ..domain.tracking import tracking_status_for_progress
from ..playback.rules import (
    marked_watched_osd,
    mirror_failed_osd,
    mirror_loading_osd,
    mirror_switch_osd,
    pending_completion_osd,
    playback_looks_complete,
    playback_updates_history,
    sync_queued_osd,
)
from ..providers import title_provider_id, title_provider_key
from ..media.episode_identity import download_identity_conflicts
from ..ui.display import suppress_terminal_echo
from . import playback as playback_mod

GREEN = "\033[1;32m"
YELLOW = "\033[1;33m"
RED = "\033[1;31m"
RESET = "\033[0m"
_logger = logging.getLogger(__name__)

_AUTO_DOWNLOAD_OFF_VALUES = {"", "0", "false", "no", "off", "none", "disabled"}
_AUTO_DOWNLOAD_ADAPTIVE_VALUES = {"1", "true", "yes", "on", "adaptive", "enabled"}
_ADAPTIVE_PAUSE_CACHE_SECONDS = 30.0
_ADAPTIVE_RESUME_CACHE_SECONDS = 90.0
_ADAPTIVE_RESUME_GRACE_SECONDS = 5.0
_ADAPTIVE_MONITOR_INTERVAL_SECONDS = 1.0


def auto_download_mode(cfg: dict | None, session_value=None) -> str:
    value = session_value
    if value is None and cfg is not None:
        value = cfg.get("auto_download_next", "adaptive")
    if isinstance(value, bool):
        return "adaptive" if value else "off"
    norm = str(value if value is not None else "adaptive").strip().lower()
    if norm in _AUTO_DOWNLOAD_OFF_VALUES:
        return "off"
    if norm in _AUTO_DOWNLOAD_ADAPTIVE_VALUES:
        return "adaptive"
    return "adaptive"


def initial_auto_download_mode(cfg: dict, *, incognito: bool, requested: bool | None) -> str:
    if requested is not None:
        return auto_download_mode(cfg, requested)
    return "off" if incognito else auto_download_mode(cfg)


def set_auto_download_enabled(ms: MachineState, enabled: bool) -> None:
    """Change session policy and stop automatic work without touching manual jobs."""
    ms.auto_download_next = "adaptive" if enabled else "off"
    if enabled:
        return
    from ..media.download_worker import DownloadStatus, get_global_worker_if_started

    worker = get_global_worker_if_started()
    if worker is None:
        return
    unfinished = {DownloadStatus.QUEUED, DownloadStatus.RESOLVING, DownloadStatus.DOWNLOADING, DownloadStatus.PAUSED}
    for item in worker.get_items():
        if item.automatic and item.status in unfinished:
            worker.cancel_item(item.id)
            worker.discard_finished_item(item.id)
            tasks = getattr(ms, "_bg_download_tasks", None)
            if tasks is not None:
                tasks.discard((item.title, str(item.ep_id)))
            _logger.debug("Automatic download cancelled after session toggle: item=%s", item.id)


def _stream_is_local(stream: dict | None) -> bool:
    if not isinstance(stream, dict):
        return False
    if stream.get("is_local"):
        return True
    if str(stream.get("source_name") or "").lower() == "local file":
        return True
    link = str(stream.get("link") or stream.get("streamUrl") or stream.get("sourceUrl") or "")
    return bool(link and "://" not in link)


def should_start_adaptive_auto_download(stream: dict | None, mode: str) -> bool:
    if mode != "adaptive":
        return False
    if _stream_is_local(stream):
        return True
    return not app_core.is_termux()


def _numeric_prop(props: dict, *names: str) -> float | None:
    for name in names:
        try:
            val = props.get(name)
            if val is None:
                continue
            num = float(val)
            if num >= 0:
                return num
        except (TypeError, ValueError):
            continue
    return None


class AdaptiveAutoDownloadController:
    """Hysteresis controller for background downloads during online playback."""

    def __init__(
        self,
        *,
        start: Callable[[], None],
        pause: Callable[[], None],
        resume: Callable[[], None],
        log: Callable[[str], None] | None = None,
    ):
        self._start = start
        self._pause = pause
        self._resume = resume
        self._log = log or (lambda _msg: None)
        self.state = "waiting"
        self._last_resume_at = 0.0

    def _effective_resume_threshold(self, props: dict) -> float:
        threshold = _ADAPTIVE_RESUME_CACHE_SECONDS
        duration = _numeric_prop(props, "duration")
        current = _numeric_prop(props, "playback-time", "time-pos")
        if duration is not None and current is not None and duration > current:
            threshold = min(threshold, max(5.0, duration - current))
        return threshold

    @staticmethod
    def _effective_pause_threshold(resume_threshold: float) -> float:
        return min(_ADAPTIVE_PAUSE_CACHE_SECONDS, max(5.0, resume_threshold / 3.0))

    @staticmethod
    def _fmt_cache(cache_seconds: float) -> str:
        return f"{int(round(cache_seconds))}s"

    def update(self, props: dict | None) -> None:
        props = props or {}
        cache_seconds = _numeric_prop(props, "demuxer-cache-duration", "cache-duration")
        paused_for_cache = bool(props.get("paused-for-cache"))
        now = time.monotonic()
        resume_threshold = self._effective_resume_threshold(props)
        pause_threshold = self._effective_pause_threshold(resume_threshold)

        if self.state == "waiting":
            if cache_seconds is not None and cache_seconds >= resume_threshold:
                self._start()
                self.state = "running"
                self._last_resume_at = now
                self._log(f"Auto-download resumed, cache {self._fmt_cache(cache_seconds)}")
            return

        if self.state == "running":
            if paused_for_cache:
                self._pause()
                self.state = "paused"
                self._log("Auto-download paused, player is buffering")
                return
            if now - self._last_resume_at < _ADAPTIVE_RESUME_GRACE_SECONDS:
                return
            if cache_seconds is not None and cache_seconds < pause_threshold:
                self._pause()
                self.state = "paused"
                self._log(f"Auto-download paused, cache {self._fmt_cache(cache_seconds)}")
            return

        if self.state == "paused":
            if cache_seconds is not None and cache_seconds >= resume_threshold:
                self._resume()
                self.state = "running"
                self._last_resume_at = now
                self._log(f"Auto-download resumed, cache {self._fmt_cache(cache_seconds)}")




def trigger_background_auto_download(show_ctx, current_ep_index, episode_ids, ttype, cfg, ms, current_stream=None):
    mode = auto_download_mode(cfg, getattr(ms, "auto_download_next", None))
    if mode == "off":
        _logger.debug("Auto-download skipped: session mode=off")
        return
    if not app_core.is_online():
        return
    if current_ep_index is None or not episode_ids:
        return
    next_idx = current_ep_index + 1
    if next_idx >= len(episode_ids):
        return
    next_ep = episode_id_at(episode_ids, next_idx)
    if not next_ep:
        return

    from . import playback_episodes as playback_mod
    lbl_next = playback_mod._display_episode_label(show_ctx, next_ep, ttype)
    show_title = getattr(ms, "show_title", "") or (show_ctx.get("name") if show_ctx else "")
    if not show_title:
        return

    dl_files = getattr(ms, "_download_files", {})
    if str(next_ep) in dl_files or str(lbl_next) in dl_files:
        return
    if app_core.find_offline_file_for_episode(show_title, lbl_next, cfg) or app_core.find_offline_file_for_episode(show_title, str(next_ep), cfg):
        return

    bg_tasks = getattr(ms, "_bg_download_tasks", None)
    if bg_tasks is None:
        ms._bg_download_tasks = set()
        bg_tasks = ms._bg_download_tasks
    task_key = (show_title, str(next_ep))
    if task_key in bg_tasks:
        return
    if not should_start_adaptive_auto_download(current_stream, mode):
        return

    from media_cli.media.download import sanitize_filename
    from media_cli.media.download_worker import DownloadItem, DownloadStatus, get_global_worker

    bg_tasks.add(task_key)
    provider_title_id = title_provider_id(show_ctx)
    provider_id = title_provider_key(show_ctx)
    quality = cfg.get("quality", "best")
    downloader_choice = cfg.get("downloader", "auto")
    download_dir = cfg.get("download_dir", "")
    safe_title = sanitize_filename(show_title) or show_title
    base_dir = download_dir or app_core.storage.get_default_download_dir()
    target_dir = os.path.join(base_dir, safe_title)
    dest_path = os.path.join(target_dir, f"{safe_title} - Episode {lbl_next}.mkv")

    def build_offline_metadata(s):
        meta = {}
        keys_to_keep = [
            "id", "_id", "provider_id", "name", "englishName", "nativeName", "altNames",
            "thumbnail", "description", "type", "format",
            "status", "season", "airedStart", "airedEnd", "startDate",
            "endDate", "score", "genres", "tags", "aniListId", "malId",
            "_display_name", "_display_english_name", "_anilist_list",
            "_anilist_progress", "_anilist_score", "anilistMatch",
            "originalEpisodeCount", "episodeCount", "_provider", "provider",
            "_provider_name", "_provider_data", "_episode_ids", "_episode_labels",
            "_episode_ids_by_ttype", "availableEpisodes", "availableEpisodesDetail",
            "_local_progress", "_local_episode_label", "watched_episodes"
        ]
        for k in keys_to_keep:
            if k in s:
                meta[k] = s[k]
        if "originalEpisodeCount" not in meta and "episodeCount" in s:
            meta["originalEpisodeCount"] = s["episodeCount"]
        return meta

    def _resolve(item):
        ep_d = (show_ctx.get("_provider_data") or {}).get(str(next_ep))
        res = app_core.fetch_episode_stream(
            provider_title_id,
            next_ep,
            ttype,
            quality,
            provider_id=provider_id,
            ep_data=ep_d,
            exclude_sources=getattr(item, "failed_mirrors", set()),
        )
        if not res or not res[0]:
            raise RuntimeError(f"No stream found for episode {next_ep}")
        selected_stream, first_source_name = res[0], res[1]
        raw_url = selected_stream.get("link") or selected_stream.get("streamUrl") or selected_stream.get("sourceUrl") or ""
        if not raw_url:
            raise RuntimeError("No stream URL in resolved stream")
        item.current_mirror_name = first_source_name or selected_stream.get("source_name") or selected_stream.get("source_parent_name") or "selected mirror"
        item.current_mirror_url = raw_url
        headers = dict(selected_stream.get("headers") or {})
        referer = selected_stream.get("referer", "")
        if referer and "Referer" not in headers:
            headers["Referer"] = referer
        item.audio_tracks = list(selected_stream.get("audio_tracks") or [])
        item.audio_url = selected_stream.get("audio_url", "")
        item.subtitles = list(selected_stream.get("subtitles") or selected_stream.get("vtt") or [])
        item.subtitle_url = selected_stream.get("subtitle_url", "")
        return raw_url, headers, None

    def _on_complete(item):
        try:
            from media_cli.core.storage import collect_downloaded_episode_files, register_downloaded_episode_files
            folder_key = sanitize_filename(item.title) or item.title
            ep_str = str(getattr(item, "ep_id", item.episode))
            files = list(getattr(item, "output_files", []) or collect_downloaded_episode_files(item.destination_path))
            register_downloaded_episode_files(
                folder_key,
                ep_str,
                files,
                metadata=getattr(item, "show_metadata", None),
            )
            new_f = app_core.find_offline_file_for_episode(show_title, lbl_next, cfg)
            if new_f and hasattr(ms, "_download_files") and isinstance(ms._download_files, dict):
                ms._download_files[str(next_ep)] = new_f
                ms._download_files[str(lbl_next)] = new_f
            app_core.set_action_feedback(show_ctx, f"✔ Auto-downloaded EP {lbl_next}")
        except Exception:
            pass
        finally:
            bg_tasks.discard(task_key)

    item = DownloadItem(
        id=f"{provider_title_id or ms.show_id or safe_title}:{next_ep}",
        title=show_title,
        episode=f"Episode {lbl_next}",
        destination_path=dest_path,
        engine=downloader_choice if downloader_choice in ("yt-dlp", "aria2c", "ffmpeg") else "auto",
        resolve_fn=_resolve,
        on_complete=_on_complete,
        ep_id=next_ep,
        show_metadata=build_offline_metadata(show_ctx),
        automatic=True,
    )
    worker = get_global_worker(download_dir)

    def _log(message: str) -> None:
        try:
            item.logs.append(message)
            app_core.info(message)
        except Exception:
            pass

    if _stream_is_local(current_stream):
        worker.add_item(item)
        _log(f"Auto-download started for {playback_mod._fmt_ep(lbl_next)}")
        return

    item.status = DownloadStatus.PAUSED
    worker.add_item(item)
    controller = AdaptiveAutoDownloadController(
        start=lambda: worker.resume_item(item.id, message=""),
        pause=lambda: worker.pause_item(item.id, message=""),
        resume=lambda: worker.resume_item(item.id, message=""),
        log=_log,
    )

    def _monitor():
        try:
            player = app_core._ipc_player
            missing_player_ticks = 0
            while True:
                proc = getattr(player, "process", None)
                if proc is None:
                    import threading as _threading
                    if _threading.current_thread() is _threading.main_thread():
                        break
                    missing_player_ticks += 1
                    if missing_player_ticks > 15:
                        break
                    time.sleep(_ADAPTIVE_MONITOR_INTERVAL_SECONDS)
                    continue
                if proc is not None and proc.poll() is not None:
                    break
                with worker.lock:
                    if item.status in (
                        DownloadStatus.COMPLETED,
                        DownloadStatus.FAILED,
                        DownloadStatus.CANCELLED,
                        DownloadStatus.SKIPPED,
                    ):
                        break
                    controller.update(dict(getattr(player, "props", {}) or {}))
                time.sleep(_ADAPTIVE_MONITOR_INTERVAL_SECONDS)
        except Exception:
            _logger.exception("Adaptive auto-download monitor failed")

    import threading
    threading.Thread(target=_monitor, daemon=True).start()


def _mark_watched_in_show(show_ctx, episode) -> None:
    watched = show_ctx.get("watched_episodes", [])
    if not isinstance(watched, list):
        watched = []
    if str(episode) not in watched:
        watched.append(str(episode))
    show_ctx["watched_episodes"] = watched


def _auto_delete_watched_downloads(show_ctx, episode_ids, ttype, cfg, ms) -> list[str]:
    if not getattr(ms, "auto_delete_watched", cfg.get("auto_delete_watched", False)):
        return []
    buf = int(getattr(ms, "auto_delete_buffer", cfg.get("auto_delete_buffer", 1)))
    if buf < 0:
        return []

    folder_name = show_ctx.get("_folder_name", getattr(ms, "show_title", ""))
    watched_list = show_ctx.get("watched_episodes", [])
    watched_keys = {str(ep) for ep in watched_list}
    labels = show_ctx.get("_episode_labels") or {}
    watched_indexes = {
        index for watched in watched_keys
        if (index := episode_index_for_id(episode_ids, watched, labels=labels)) is not None
    }
    # Persisted local history is authoritative over stale display-state caches.
    local_progress = app_core.get_local_progress(show_ctx, ttype)
    if local_progress is None:
        local_progress = show_ctx.get("_local_progress")
    try:
        progress = Decimal(str(local_progress))
        if not progress.is_finite() or progress < 0:
            progress = None
    except (InvalidOperation, ValueError):
        progress = None
    episode_indexes = {str(eid): index for index, eid in enumerate(episode_ids)}
    dl_files = getattr(ms, "_download_files", {})
    watched_dl = []

    def _episode_is_watched(eid, eid_lbl) -> bool:
        index = episode_indexes[str(eid)]
        if str(eid) in watched_keys or str(eid_lbl) in watched_keys or index in watched_indexes:
            return True
        if progress is None:
            return False
        primary, _ = parse_episode_dual_numbers(str(eid_lbl))
        number = primary or clean_episode_identifier(str(eid_lbl))
        try:
            episode_progress = Decimal(number)
        except InvalidOperation:
            episode_progress = Decimal(index + 1)
        return episode_progress.is_finite() and 0 < episode_progress <= progress

    for eid in episode_ids:
        eid_lbl = playback_mod._display_episode_label(show_ctx, eid, ttype)
        if not _episode_is_watched(eid, eid_lbl):
            continue
        is_downloaded = (
            str(eid) in dl_files
            or str(eid_lbl) in dl_files
            or bool(app_core.find_offline_file_for_episode(folder_name, eid_lbl, cfg))
            or bool(app_core.find_offline_file_for_episode(folder_name, str(eid), cfg))
        )
        if is_downloaded:
            watched_dl.append((eid, eid_lbl))

    status_str = str(show_ctx.get("status") or "").upper()
    is_finished = status_str in {"FINISHED", "COMPLETED", "ENDED"}
    all_available_watched = bool(episode_ids) and all(
        _episode_is_watched(eid, playback_mod._display_episode_label(show_ctx, eid, ttype))
        for eid in episode_ids
    )
    effective_buf = 0 if is_finished and all_available_watched else buf
    _logger.debug("Watched-download cleanup: title=%s watched_downloads=%s buffer=%s",
                  folder_name, len(watched_dl), effective_buf)

    if len(watched_dl) <= effective_buf:
        return []

    to_del = watched_dl[:-effective_buf] if effective_buf > 0 else watched_dl
    deleted_labels = []
    for deid, dlbl in to_del:
        if app_core.delete_offline_episode(folder_name, dlbl, cfg, require_recorded=True) or app_core.delete_offline_episode(folder_name, str(deid), cfg, require_recorded=True):
            for key in (str(deid), str(dlbl), deid, dlbl):
                dl_files.pop(key, None)
            dl_eps = show_ctx.get("_downloaded_episodes", [])
            if str(dlbl) in dl_eps:
                dl_eps.remove(str(dlbl))
            if str(deid) in dl_eps:
                dl_eps.remove(str(deid))
            deleted_labels.append(str(dlbl))
        else:
            _logger.debug("Watched-download cleanup skipped: title=%s episode=%s no recorded file removed",
                          folder_name, dlbl)
    if deleted_labels:
        app_core.set_action_feedback(
            show_ctx,
            f"Auto-deleted watched EP {', '.join(deleted_labels)}"
        )
    return deleted_labels


def _valid_local_download_path(path: str | None) -> str | None:
    if not path or not os.path.exists(path):
        return None
    try:
        from ..core.storage import is_valid_download_video
        if not is_valid_download_video(path):
            return None
    except Exception:
        return None
    return path


def _local_stream_for_episode(show_ctx, show_title, episode, ttype, cfg, ms):
    label = playback_mod._display_episode_label(show_ctx or {}, episode, ttype)
    dl_files = getattr(ms, "_download_files", {}) or {}
    lookup_keys = [str(episode), str(label)]
    clean_label = clean_episode_identifier(str(label))
    if clean_label:
        lookup_keys.append(str(clean_label))

    for key in lookup_keys:
        filepath = _valid_local_download_path(dl_files.get(key))
        if filepath and not download_identity_conflicts(label, filepath):
            return (
                {"link": filepath, "resolution": "Offline (Local)", "is_local": True},
                "Local File",
                {"is_local": True, "filepath": filepath},
                [],
            )

    title_candidates = [
        (show_ctx or {}).get("_folder_name"),
        show_title,
        (show_ctx or {}).get("name"),
    ]
    seen_titles = set()
    for title in title_candidates:
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)
        for ep_key in lookup_keys:
            filepath = app_core.find_offline_file_for_episode(str(title), ep_key, cfg)
            filepath = _valid_local_download_path(filepath)
            if filepath:
                if isinstance(dl_files, dict):
                    for key in lookup_keys:
                        dl_files[str(key)] = filepath
                    ms._download_files = dl_files
                return (
                    {"link": filepath, "resolution": "Offline (Local)", "is_local": True},
                    "Local File",
                    {"is_local": True, "filepath": filepath},
                    [],
                )
    return None


def _lookup_player_sources(
    data: dict | None, show: dict | None, episode_id: object, ttype: str,
    *, poster_manager=None, ui=None,
) -> dict | None:
    from copy import deepcopy
    from threading import Event

    from ..ui.player_screen import run_loading_work

    episode_data, title = deepcopy(data), deepcopy(show)
    retired = Event()

    def lookup():
        return playback_mod._augment_episode_sources_with_torrents(
            episode_data, title, episode_id, ttype, cancel_check=retired.is_set,
        )

    try:
        return run_loading_work(lookup, poster_manager=poster_manager, ui=ui)
    finally:
        # Abandoned work may finish, but cannot change the caller's cache/show.
        retired.set()


def _wait_for_player_streams(
    data: dict | None, key: tuple, *, download: bool = False, poster_manager=None, ui=None,
) -> list:
    from copy import deepcopy
    from threading import Event

    from ..ui.player_screen import run_loading_work

    episode_data = deepcopy(data)
    retired = Event()

    def wait_for_streams():
        deadline = time.monotonic() + 5
        while not retired.is_set() and time.monotonic() < deadline:
            ready = app_core._stream_snapshot(*key, episode_data)
            if ready:
                return ready
            retired.wait(0.15)
        return []

    try:
        if download:
            return wait_for_streams()
        return run_loading_work(wait_for_streams, poster_manager=poster_manager, ui=ui)
    finally:
        retired.set()


def _probe_player_mirrors(
    candidates: tuple[dict, ...], key: tuple, *, download: bool = False, poster_manager=None, ui=None,
    recovery: bool = False,
) -> dict | None:
    from copy import deepcopy
    from threading import Event

    from ..ui.player_screen import run_loading_work

    snapshot = deepcopy(candidates)
    retired = Event()

    def probe() -> tuple[int | None, float | None, tuple]:
        now = time.time()
        dead_links = []
        for index, candidate in enumerate(snapshot if recovery else snapshot[:3]):
            if retired.is_set():
                return None, None, ()
            if not recovery and now - float(candidate.get("validated_at") or 0) < 30:
                return index, None, tuple(dead_links)
            if app_core.ping_stream_liveness(candidate, timeout=0.8):
                return index, None if recovery else now, tuple(dead_links)
            if not recovery:
                dead_links.append(candidate.get("link") or candidate.get("streamUrl"))
        return (0 if snapshot else None), None, tuple(dead_links)

    try:
        if download:
            selected, validated_at, dead_links = probe()
        else:
            selected, validated_at, dead_links = run_loading_work(
                probe, poster_manager=poster_manager, ui=ui,
            )
    finally:
        retired.set()
    for link in dead_links:
        app_core._prune_dead_stream(key, link)
    if selected is None:
        return None
    candidate = candidates[selected]
    if validated_at is not None:
        candidate["validated_at"] = validated_at
    return candidate


def _fetch_player_fallback(
    data: dict | None, key: tuple, *, download: bool = False, poster_manager=None, ui=None,
    exclude_sources: frozenset[str] = frozenset(),
):
    from copy import deepcopy
    from threading import Event

    from ..ui.player_screen import run_loading_work

    if download:
        return app_core.fetch_episode_stream(
            *key[:3], key[4], provider_id=key[3], ep_data=data, exclude_sources=exclude_sources,
        )
    episode_data = deepcopy(data)
    excluded = frozenset(exclude_sources)
    retired = Event()

    def fetch():
        return app_core.fetch_episode_stream(
            *key[:3], key[4], provider_id=key[3], ep_data=episode_data, cancel_check=retired.is_set,
            exclude_sources=excluded,
        )

    try:
        return run_loading_work(fetch, poster_manager=poster_manager, ui=ui)
    finally:
        retired.set()


def _load_player_cached_streams(data: dict | None, key: tuple, *, poster_manager=None, ui=None):
    from copy import deepcopy
    from threading import Event

    from ..ui.player_screen import run_loading_work

    episode_data = deepcopy(data)
    retired = Event()

    def load():
        return app_core._stream_snapshot(*key, episode_data, cancel_check=retired.is_set)

    try:
        return run_loading_work(load, poster_manager=poster_manager, ui=ui)
    finally:
        retired.set()


def _load_player_mal_id(show: dict | None, *, poster_manager=None, ui=None) -> int | None:
    from copy import deepcopy
    from threading import Event

    from ..ui.player_screen import run_loading_work
    from ..ui.transition import PreparationCancelled

    known = app_core.get_show_mal_id(show, fetch_missing=False)
    anilist_id = app_core.get_show_anilist_id(show)
    if known is not None or not anilist_id or show is None:
        return known
    identity = (show.get("_id"), title_provider_key(show), anilist_id)
    active_show = getattr(ui, "ui_show_ctx", show)
    draft = deepcopy(show)
    retired = Event()

    def lookup():
        if retired.is_set():
            return None
        return app_core.get_show_mal_id(draft)

    try:
        resolved = run_loading_work(lookup, poster_manager=poster_manager, ui=ui)
    finally:
        retired.set()
    current_identity = (show.get("_id"), title_provider_key(show), app_core.get_show_anilist_id(show))
    if current_identity != identity or getattr(ui, "ui_show_ctx", show) is not active_show:
        raise PreparationCancelled
    current = app_core.get_show_mal_id(show, fetch_missing=False)
    if current is not None:
        return current
    if resolved is not None:
        show["malId"] = resolved
    return resolved


def _load_player_skips(mal_id: int, episode: int, future=None, *, poster_manager=None, ui=None):
    from ..media.aniskip import fetch_skip_times
    from ..ui.player_screen import run_loading_work

    def load():
        if future is not None:
            try:
                return future.result(timeout=4.0)
            except Exception:
                return []
        return fetch_skip_times(mal_id, episode)

    return run_loading_work(load, poster_manager=poster_manager, ui=ui)


def _select_android_player(chosen: str | None, *, poster_manager=None, ui=None) -> str:
    if chosen:
        return "rex" if chosen == "mpvrex" else chosen

    from threading import Event

    from ..ui.player_screen import run_loading_work
    from ..ui.transition import PreparationCancelled

    retired = Event()

    def installed(package):
        if retired.is_set():
            raise PreparationCancelled
        result = app_core.pkg_installed(package)
        if retired.is_set():
            raise PreparationCancelled
        return result

    def detect():
        if installed("app.gyrolet.mpvrx") and not installed("is.xyz.mpv"):
            return "mpvrx"
        if installed("xyz.mpv.rex") and not installed("is.xyz.mpv"):
            return "rex"
        return "mpv"

    try:
        return run_loading_work(detect, poster_manager=poster_manager, ui=ui)
    finally:
        retired.set()


def _wait_for_android_start(stream_name: str, res_str: str, *, poster_manager=None, ui=None) -> None:
    from threading import Event

    from ..core.terminal import sanitize_terminal_text
    from ..ui.actions import ActionBindings, ResolvedAction
    from ..ui.loading_model import LoadingInteraction, LoadingOwner, LoadingPlacement, LoadingTaskSpec
    from ..ui.message_choices import MAX_MESSAGE_LENGTH
    from ..ui.player_screen import run_loading_work
    from ..ui.transition import set_preparation_status
    from ..ui.view_model import ViewAction

    prefix = sanitize_terminal_text(f"Starting '{stream_name}'{res_str}")
    budget = MAX_MESSAGE_LENGTH - len(" in 3s...")
    if len(prefix) > budget:
        prefix = prefix[:budget - 3] + "..."
    messages = tuple(f"{prefix} in {second}s..." for second in (3, 2, 1))
    retired = Event()

    def countdown():
        for message in messages:
            if retired.is_set():
                return
            set_preparation_status(message)
            if retired.wait(1):
                return

    task = LoadingTaskSpec(
        "player-countdown", LoadingOwner("player", 0), messages[0],
        LoadingInteraction.BLOCKING, LoadingPlacement.PLAYER_STATUS,
        ActionBindings((ResolvedAction(ViewAction("back", "Cancel", ("q", "Q", "ESC"))),)), delay=0,
    )
    try:
        run_loading_work(countdown, poster_manager=poster_manager, ui=ui, task=task)
    finally:
        retired.set()


def _android_skip_hint_choice(flags: CliFlags) -> str:
    from ..ui.player_screen import prompt_input_scope

    with prompt_input_scope():
        interactive = not getattr(flags, "plain_mode", False) and sys.stdin.isatty()
        if interactive:
            sys.stdout.write("\033[?1049h\033[2J\033[H\033[?25h")
            sys.stdout.flush()
        try:
            print("\033[1;36mAniSkip on Android (MPV)\033[0m\n")
            print("  \033[38;5;250m• Auto-skip is not supported via Android intents.\033[0m")
            print("  \033[38;5;250m• To view chapter marks on the seekbar, add this line to /storage/emulated/0/Mpv/mpv.conf:\033[0m")
            print("    \033[1;33mchapters-file=/storage/emulated/0/Mpv/chapters.txt\033[0m\n")
            try:
                return input("\033[1;97m[y = Don't show again, n = Disable AniSkip, s = Skip for now] (s): \033[0m").strip().lower()
            except EOFError:
                return "s"
        finally:
            if interactive:
                sys.stdout.write("\033[2J\033[H\033[?25l")
                sys.stdout.flush()


def _pending_skip_validation_status(*, android: bool) -> str:
    if not android:
        return "Awaiting video duration to validate skip times"
    return "Skip times ready; duration not checked"


def handle_play_state(
    flags: CliFlags,
    ui: UiState,
    ms: MachineState,
    cfg: dict,
    args: Any,
    ttype: str,
    quality: str,
    resolve_tracking_fn,
) -> str:
    s_ctx = ui.ui_show_ctx
    sync_enabled = resolve_tracking_fn(ui.search_prev_state, args, cfg, s_ctx)
    from copy import deepcopy
    from ..ui.transition import PreparationCancelled
    from .details_preparation import PreparedDetails, commit_details_transition

    original = deepcopy(s_ctx)
    def prepare_entry():
        show = deepcopy(original)
        app_core.prepare_show_display_state(show, ttype, sync_enabled)
        ids = app_core.ensure_episode_ids(show, ttype)
        return PreparedDetails(show, tuple(str(ep) for ep in ids), original)

    try:
        prepared = app_core.with_loading("Preparing playback…", prepare_entry)
    except PreparationCancelled:
        return "DETAILS"
    commit_details_transition(s_ctx, prepared)
    ms.show_title = get_show_display_title(s_ctx, sync_enabled=sync_enabled)
    ms.show_id = s_ctx.get("_id")
    al_id = app_core.get_show_anilist_id(s_ctx)
    episode_ids = prepared.episode_ids

    if not episode_ids:
        app_core._exit_player_screen()
        app_core.err(app_core.episode_catalog_error(s_ctx))
        return "DETAILS"

    ms.total_eps = len(episode_ids) or ms.total_eps
    ms.current_ep_index = episode_index_for_id(
        episode_ids, ms.current_ep, labels=ui.ui_show_ctx.get("_episode_labels")
    )

    if ms.current_ep_index is None:
        app_core._exit_player_screen()
        app_core.err(f"EP {clean_episode_identifier(str(ms.current_ep)) or ms.current_ep} is not present in the provider catalog.")
        return "EPISODE"

    ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
    current_ep_label = playback_mod._display_episode_label(s_ctx, ms.current_ep, ttype)

    is_download_mode = bool(getattr(args, 'download', False))

    if is_download_mode:
        app_core.restore_terminal()
        print(
            f"\n\033[1;36mDownloading episode {playback_mod._fmt_ep(current_ep_label)} for '{ms.show_title}' ({ttype.upper()})…\033[0m\n"
        )
        print(
            f"\033[1;97mResolving stream for EP {playback_mod._fmt_ep(current_ep_label)}…\033[0m"
        )
    else:
        app_core.enter_alt_screen()
        from ..ui.player_screen import _player_ui_state
        _player_ui_state.update({
            "active": True,
            "show": ui.ui_show_ctx,
            "ttype": ttype,
            "current_ep": ms.current_ep,
            "current_ep_label": current_ep_label,
            "total_eps": ms.total_eps,
            "status_lines": [],
            "stream_info": {},
            "mpv_props": None,
            "_cleared_terminal_image": False,
            "_last_poster_key": None,
        })

    with suppress_terminal_echo():
        provider_id = title_provider_key(ui.ui_show_ctx)
        p_name = (ui.ui_show_ctx.get("_provider_name") or ui.ui_show_ctx.get("provider_name") or provider_id).title()

        def _with_torrent_sources(data, *, shared_loading=False):
            if is_download_mode or app_core.is_termux():
                return data
            if str(provider_id or "").lower() == "torrent" or str((ui.ui_show_ctx or {}).get("_provider") or "").lower() == "torrent":
                return data
            if shared_loading:
                return _lookup_player_sources(
                    data, ui.ui_show_ctx, ms.current_ep, ttype,
                    poster_manager=getattr(app_core, "_poster_manager", None), ui=ui,
                )
            try:
                from ..ui.player_screen import loading_cancel_requested
                cancel_check = loading_cancel_requested
            except Exception:
                cancel_check = None
            return playback_mod._augment_episode_sources_with_torrents(
                data,
                ui.ui_show_ctx,
                ms.current_ep,
                ttype,
                cancel_check=cancel_check,
            )

        _ipc_player = app_core._ipc_player
        is_prefetched = (
            _ipc_player is not None
            and getattr(_ipc_player, "prefetched_res", None) is not None
            and (
                str(getattr(_ipc_player, "prefetched_ep", "")) == str(ms.current_ep)
                or getattr(_ipc_player, "prefetched_ep_id", None) == str(ms.current_ep)
                or (isinstance(getattr(_ipc_player, "prefetched_ep", None), int) and ms.current_ep_index is not None and _ipc_player.prefetched_ep == ms.current_ep_index + 1)
                or (getattr(_ipc_player, "prefetched_ep", None) is not None and episode_progress_number(_ipc_player.prefetched_ep) == episode_progress_number(ms.current_ep))
            )
        )

        first_source_name = None
        ep_data = None
        aniskip_future = None
        _skip_pool = None
        _cache_key = (ms.show_id, ms.current_ep, ttype, provider_id)
        mal_id_loaded = False
        playback_mal_id: int | None = None

        def load_mal_id() -> int | None:
            nonlocal mal_id_loaded, playback_mal_id
            if not mal_id_loaded:
                playback_mal_id = _load_player_mal_id(
                    ui.ui_show_ctx, poster_manager=getattr(app_core, "_poster_manager", None), ui=ui,
                )
                mal_id_loaded = True
            return playback_mal_id

        if is_prefetched:
            res = _ipc_player.prefetched_res
            _ipc_player.prefetched_ep = None
            _ipc_player.prefetched_stream = None
            _ipc_player.prefetched_res = None
            _ipc_player.prefetched_ep_id = None
            ms.selected_stream, first_source_name, ep_data, resolved_streams = res
            ms.ep_cache_key = _cache_key
            ms.ep_cache_data = ep_data
            app_core._extend_streams(resolved_streams, key=_cache_key)
        else:
            if not is_download_mode:
                _player_ui_state["loading_provider_name"] = p_name
                _player_ui_state["loading_start_time"] = time.time()

                from ..ui.player_screen import reset_loading_cancel
                reset_loading_cancel()

            aniskip_enabled = getattr(args, "aniskip", None)
            if aniskip_enabled is None:
                aniskip_enabled = cfg.get("aniskip", cfg.get("aniskip_enabled", True))

            if aniskip_enabled and not is_download_mode:
                action_show = ui.ui_show_ctx or {}
                try:
                    mal_id = load_mal_id()
                except PreparationCancelled:
                    app_core._exit_player_screen()
                    app_core.set_action_feedback(ui.ui_show_ctx, "Loading cancelled.")
                    return "DETAILS"
                ep_num = episode_progress_number(ms.current_ep)
                if mal_id:
                    from ..core.workers import DaemonExecutor

                    from ..media.aniskip import fetch_skip_times
                    _skip_pool = DaemonExecutor(max_workers=1)
                    aniskip_future = _skip_pool.submit(fetch_skip_times, mal_id, ep_num)

            if getattr(ms, "_is_downloads", False) and not is_download_mode:
                ep_data = {"is_local": True}
            elif _cache_key == ms.ep_cache_key and ms.ep_cache_data:
                try:
                    ep_data = _with_torrent_sources(ms.ep_cache_data, shared_loading=True)
                except PreparationCancelled:
                    from ..ui.player_screen import request_loading_cancel
                    request_loading_cancel()
                else:
                    ms.ep_cache_data = ep_data
            else:
                show_id, episode_id = ms.show_id, ms.current_ep

                def fetch_episode():
                    app_core.info(f"[{p_name}] Requesting {playback_mod._fmt_ep(current_ep_label)} metadata...")
                    return app_core.get_episode_data(
                        show_id, episode_id, ttype, provider_id=provider_id,
                    )

                try:
                    if is_download_mode:
                        ep_data = fetch_episode()
                    else:
                        from ..ui.player_screen import run_loading_work
                        ep_data = run_loading_work(
                            fetch_episode, poster_manager=getattr(app_core, "_poster_manager", None), ui=ui,
                        )
                    ep_data = _with_torrent_sources(ep_data, shared_loading=True)
                except PreparationCancelled:
                    from ..ui.player_screen import request_loading_cancel
                    request_loading_cancel()
                else:
                    ms.ep_cache_key = _cache_key
                    ms.ep_cache_data = ep_data

            try:
                from ..ui.player_screen import loading_cancel_requested
                cancelled_loading = loading_cancel_requested()
            except Exception:
                cancelled_loading = False
            if cancelled_loading:
                if _skip_pool:
                    try:
                        _skip_pool.shutdown(wait=False)
                    except Exception:
                        pass
                app_core._exit_player_screen()
                app_core.set_action_feedback(ui.ui_show_ctx, "Loading cancelled.")
                return "DETAILS"

            if not ep_data:
                if _skip_pool:
                    try:
                        _skip_pool.shutdown(wait=False)
                    except Exception:
                        pass
                app_core._exit_player_screen()
                playback_mod._clear_episode_source_state(ms)
                p_name = (ui.ui_show_ctx.get("_provider_name") or (ui.ui_show_ctx.get("_provider") or "").title() or "this provider") if ui.ui_show_ctx else "this provider"
                if ttype == "dub":
                    msg = f"No DUB stream available for {playback_mod._fmt_ep(current_ep_label)} on {p_name}."
                else:
                    msg = f"No stream available for {playback_mod._fmt_ep(current_ep_label)} on {p_name}."
                app_core.set_action_feedback(
                    ui.ui_show_ctx,
                    msg,
                )
                return "DETAILS"

        if ms.selected_stream is None:
            _ipc_player = app_core._ipc_player
            target_quality = getattr(args, "quality", None) or cfg.get("quality", "best")
            res = None
            if not getattr(args, 'download', False) and not getattr(args, 'sources', False):
                res = _local_stream_for_episode(ui.ui_show_ctx, ms.show_title, ms.current_ep, ttype, cfg, ms)
                if res:
                    ms.selected_stream = res[0]
                    first_source_name = "Local File"

            if not res:
                if getattr(ms, "_is_downloads", False) and not is_download_mode:
                    res = None
                elif str(_ipc_player.prefetched_ep) == str(ms.current_ep) and _ipc_player.prefetched_res:
                    res = _ipc_player.prefetched_res
                    _ipc_player.prefetched_ep = None
                    _ipc_player.prefetched_stream = None
                    _ipc_player.prefetched_res = None
                else:
                    cur_key = (ms.show_id, ms.current_ep, ttype, provider_id, target_quality)
                    cached_ep_data = app_core._get_cached_ep_data(cur_key)

                    try:
                        if not cached_ep_data:
                            def fetch_resolver_episode(key=cur_key):
                                return app_core.get_episode_data(key[0], key[1], key[2], provider_id=key[3])

                            if is_download_mode:
                                cached_ep_data = fetch_resolver_episode()
                            else:
                                from ..ui.player_screen import run_loading_work
                                cached_ep_data = run_loading_work(
                                    fetch_resolver_episode,
                                    poster_manager=getattr(app_core, "_poster_manager", None), ui=ui,
                                )
                        cached_ep_data = _with_torrent_sources(cached_ep_data, shared_loading=True)

                        # Only accepted preparation results may start resolution.
                        streams_mod.start_bg_resolve(cached_ep_data, set(), ms.show_id, ms.current_ep, ttype, provider_id, target_quality)
                        cached_streams = _wait_for_player_streams(
                            cached_ep_data, cur_key, download=is_download_mode,
                            poster_manager=getattr(app_core, "_poster_manager", None), ui=ui,
                        )
                    except PreparationCancelled:
                        if _skip_pool:
                            _skip_pool.shutdown(wait=False)
                        app_core._exit_player_screen()
                        app_core.set_action_feedback(ui.ui_show_ctx, "Loading cancelled.")
                        return "DETAILS"

                    res = None

                    if cached_streams:
                        pref = app_core.get_preferred_mirror(ms.show_id)
                        pref_name = pref.get("source_name", "")
                        pref_res = pref.get("resolution", "")
                        pref_name_lower = pref_name.lower()
                        pref_res_lower = pref_res.lower() if pref_res else ""

                        # 1. Check if preferred mirror is available - use it immediately
                        preferred_stream = None
                        if pref_name:
                            for s in cached_streams:
                                src_name = s.get("source_name") or ""
                                src_res = (s.get("resolution") or "?").lower()
                                if src_name.lower().startswith(pref_name_lower) and (not pref_res_lower or src_res == pref_res_lower):
                                    preferred_stream = s
                                    break

                        if preferred_stream:
                            # Preferred mirror found - use it directly
                            src_name = preferred_stream.get("source_name", "Stream")
                            res = (preferred_stream, src_name, None, cached_streams)
                            if not ep_data:
                                ep_data = app_core._get_cached_ep_data(cur_key)
                            # Log the connection (same as fetch_episode_stream does)
                            from ..core import reporting
                            reporting.ok(f"Connected: {preferred_stream.get('source_name', src_name)} ({preferred_stream.get('resolution', '?')})")
                        else:
                            # 2. No preferred mirror or not available - fall back to normal logic
                            from ..media.sources import quality_preference_key
                            def _cached_sort_key(s):
                                sname = (s.get("source_name") or "").lower()
                                res = s.get("resolution") or ""
                                prio = s.get("source_priority", 4)
                                is_sub = "sub" in sname
                                is_dub = ("dub" in sname) or ((" eng" in sname or "english" in sname) and not is_sub)
                                audio_penalty = 1 if (ttype == "sub" and is_dub) or (ttype == "dub" and not is_dub) else 0
                                is_hard = "hardsub" in sname or "hard-sub" in sname or "hard sub" in sname
                                is_soft = "softsub" in sname or "all sub" in sname or "multi sub" in sname
                                is_donghua = str(provider_id or "").lower() in {"animexin", "lucifer", "animekhor"}
                                if is_donghua:
                                    sub_rank = 0 if is_hard else (2 if is_soft else 1)
                                else:
                                    sub_rank = 0 if is_soft else (2 if is_hard else 1)

                                # Preferred mirror gets highest priority (for sorting fallback candidates)
                                pref_boost = 0
                                if pref_name:
                                    src_name = s.get("source_name") or ""
                                    if src_name.lower().startswith(pref_name_lower) and (not pref_res_lower or res.lower() == pref_res_lower):
                                        pref_boost = -100

                                q_key = quality_preference_key(res, target_quality)
                                return (
                                    pref_boost,
                                    streams_mod.torrent_fallback_sort_value(s),
                                    sub_rank,
                                    audio_penalty,
                                    q_key,
                                    prio,
                                    streams_mod.torrent_size_sort_value(s),
                                )

                            sorted_cached = sorted(cached_streams, key=_cached_sort_key)
                            candidates = []
                            for s in sorted_cached:
                                if s not in candidates:
                                    candidates.append(s)

                            try:
                                selected = _probe_player_mirrors(
                                    tuple(candidates), cur_key, download=is_download_mode,
                                    poster_manager=getattr(app_core, "_poster_manager", None), ui=ui,
                                )
                            except PreparationCancelled:
                                if _skip_pool:
                                    _skip_pool.shutdown(wait=False)
                                app_core._exit_player_screen()
                                app_core.set_action_feedback(ui.ui_show_ctx, "Loading cancelled.")
                                return "DETAILS"

                            if selected:
                                src_name = selected.get("source_name", "Stream")
                                res = (selected, src_name, None, cached_streams)
                                if not ep_data:
                                    ep_data = app_core._get_cached_ep_data(cur_key)
                                with streams_mod._bg_lock:
                                    bg_alive = bool(streams_mod._bg_thread and streams_mod._bg_thread.is_alive())
                                if not bg_alive and ep_data:
                                    exclude_names = set()
                                    for s in cached_streams:
                                        if s.get("source_parent_name"):
                                            exclude_names.add(s["source_parent_name"])
                                        if s.get("source_name"):
                                            raw_sname = s["source_name"]
                                            base_name = re.sub(r"\s*(?:\(\s*)?\b(?:\d+p|\d+k|adaptive|source)\b.*$", "", raw_sname, flags=re.I).strip()
                                            if base_name:
                                                exclude_names.add(base_name)
                                            exclude_names.add(raw_sname.split(" (")[0].strip())
                                    sources_total = len(ep_data.get("episode", {}).get("sourceUrls", []))
                                    if len(exclude_names) < sources_total:
                                        app_core.start_bg_resolve(ep_data, exclude_names, show_id=ms.show_id, ep=ms.current_ep, ttype=ttype, provider_id=provider_id, quality=target_quality)

                    # Fallback: fetch episode stream if no cached stream was selected or no cached streams available
                    if res is None:
                        try:
                            res = _fetch_player_fallback(
                                ep_data, (ms.show_id, ms.current_ep, ttype, provider_id, cfg.get("quality", "best")),
                                download=is_download_mode,
                                poster_manager=getattr(app_core, "_poster_manager", None), ui=ui,
                            )
                        except PreparationCancelled:
                            if _skip_pool:
                                _skip_pool.shutdown(wait=False)
                            app_core._exit_player_screen()
                            app_core.set_action_feedback(ui.ui_show_ctx, "Loading cancelled.")
                            return "DETAILS"
                        if ep_data:
                            app_core._set_cached_ep_data(ep_data, (ms.show_id, ms.current_ep, ttype, provider_id, target_quality))

                if res:
                    ms.selected_stream, first_source_name, _, resolved_streams = res
                    app_core._extend_streams(resolved_streams, key=(ms.show_id, ms.current_ep, ttype, provider_id, target_quality))
        if not getattr(args, 'download', False) and not getattr(args, 'print_url', False) and not (getattr(args, 'sources', False) and not ui.initial_sources_prompted):
            stream_name = ms.selected_stream.get("source_name", first_source_name or "Stream") if ms.selected_stream else (first_source_name or "Stream")
            resolution = ms.selected_stream.get("resolution", "") if ms.selected_stream else ""
            res_str = f" ({resolution})" if resolution else ""

            aniskip_enabled = getattr(args, "aniskip", None)
            if aniskip_enabled is None:
                aniskip_enabled = cfg.get("aniskip", cfg.get("aniskip_enabled", True))

            if aniskip_enabled:
                action_show = ui.ui_show_ctx or {}
                try:
                    mal_id = load_mal_id()
                except PreparationCancelled:
                    if _skip_pool:
                        _skip_pool.shutdown(wait=False)
                    app_core._exit_player_screen()
                    app_core.set_action_feedback(ui.ui_show_ctx, "Loading cancelled.")
                    return "DETAILS"
                ep_num = episode_progress_number(ms.current_ep)
                from ..media import custom_skips as custom_skip_mod
                custom_skip = None
                title_keys = custom_skip_mod.stable_title_keys(action_show, ms.show_id)
                disabled_custom = any(str(key) in getattr(ms, "custom_skip_disabled_titles", set()) for key in title_keys)
                if not disabled_custom:
                    custom_skip = custom_skip_mod.resolve_custom_skips(
                        show=action_show,
                        fallback_show_id=ms.show_id,
                        provider_id=provider_id,
                        session_store=getattr(ms, "custom_skip_sessions", {}),
                        saved_entries=app_core.get_custom_skips(action_show, ms.show_id),
                    )
                if custom_skip:
                    from ..ui.player_screen import _fmt_time
                    skip_parts = []
                    for s in custom_skip["intervals"]:
                        end = "end" if s.get("end") is None else _fmt_time(s["end"])
                        skip_parts.append(f"{s.get('label', 'Skip')} ({_fmt_time(s['start'])} → {end})")
                    skip_summary = " · ".join(skip_parts)
                    skip_msg = f"\033[38;5;120m✔ Custom Skips ({custom_skip['mode']}): {skip_summary}\033[0m"
                elif mal_id:
                    try:
                        skips = _load_player_skips(
                            mal_id, ep_num, aniskip_future,
                            poster_manager=getattr(app_core, "_poster_manager", None), ui=ui,
                        )
                    except PreparationCancelled:
                        app_core._exit_player_screen()
                        app_core.set_action_feedback(ui.ui_show_ctx, "Loading cancelled.")
                        return "DETAILS"
                    finally:
                        if _skip_pool:
                            try:
                                _skip_pool.shutdown(wait=False)
                            except Exception:
                                pass
                    if skips:
                        # Desktop validates candidates; Android exports them as-is.
                        skip_status = _pending_skip_validation_status(
                            android=app_core.is_termux(),
                        )
                        skip_msg = f"\033[38;5;244m• AniSkip: {skip_status}\033[0m"
                    else:
                        skip_msg = f"\033[38;5;244m• AniSkip: No skip times found for EP {ep_num}\033[0m"
                else:
                    skip_msg = "\033[38;5;244m• AniSkip: No MAL ID available for this title\033[0m"

                if skip_msg not in _player_ui_state["status_lines"]:
                    if _player_ui_state["status_lines"] and _player_ui_state["status_lines"][-1] != "":
                        _player_ui_state["status_lines"].append("")
                    _player_ui_state["status_lines"].append(skip_msg)
                    try:
                        from ..ui.player_screen import render as _render
                        _render(poster_manager=getattr(app_core, "_poster_manager", None), ui=ui)
                    except Exception:
                        pass

            if app_core.is_termux() and aniskip_enabled:
                if not cfg.get("aniskip_android_hint_dismissed", False) and not getattr(ui, "aniskip_android_hint_shown", False):
                    try:
                        choice = _android_skip_hint_choice(flags)
                    except PreparationCancelled:
                        app_core._exit_player_screen()
                        app_core.set_action_feedback(ui.ui_show_ctx, "Loading cancelled.")
                        return "DETAILS"
                    ui.aniskip_android_hint_shown = True

                    if choice in ("y", "yes"):
                        cfg["aniskip_android_hint_dismissed"] = True
                        app_core.save_config(cfg)
                    elif choice in ("n", "no"):
                        cfg["aniskip"] = False
                        cfg["aniskip_enabled"] = False
                        aniskip_enabled = False
                        app_core.save_config(cfg)

                    try:
                        from ..ui.player_screen import render as _render
                        _render(poster_manager=getattr(app_core, "_poster_manager", None), ui=ui)
                    except Exception:
                        pass

                    if not is_download_mode:
                        try:
                            _wait_for_android_start(
                                stream_name, res_str,
                                poster_manager=getattr(app_core, "_poster_manager", None), ui=ui,
                            )
                        except PreparationCancelled:
                            app_core._exit_player_screen()
                            app_core.set_action_feedback(ui.ui_show_ctx, "Loading cancelled.")
                            return "DETAILS"


        if getattr(args, "sources", False) and not ui.initial_sources_prompted:
            ui.initial_sources_prompted = True
            if first_source_name is not None and first_source_name != "Local File" and not getattr(ms, "_is_downloads", False):
                app_core.start_bg_resolve(ep_data, {first_source_name}, show_id=ms.show_id, ep=ms.current_ep, ttype=ttype, provider_id=provider_id, quality=quality)
            app_core._exit_player_screen()
            return "MIRRORS"

        if ms.selected_stream is None:
            app_core._exit_player_screen()
            playback_mod._clear_episode_source_state(ms)
            p_name = (ui.ui_show_ctx.get("_provider_name") or (ui.ui_show_ctx.get("_provider") or "").title() or "this provider") if ui.ui_show_ctx else "this provider"
            ep_label = playback_mod._display_episode_label(ui.ui_show_ctx, ms.current_ep, ttype)
            if getattr(ms, "_is_downloads", False) and not is_download_mode:
                msg = f"✘ Episode {playback_mod._fmt_ep(ep_label)} is not downloaded locally."
            elif ttype == "dub":
                msg = f"No DUB stream available for {playback_mod._fmt_ep(ep_label)} on {p_name}."
            else:
                msg = f"No stream available for {playback_mod._fmt_ep(ep_label)} on {p_name}."
            app_core.set_action_feedback(
                ui.ui_show_ctx,
                msg,
            )
            if is_download_mode:
                args.download = False
            return "DETAILS"

    if args.print_url:
        app_core._exit_player_screen(close_alt=True)
        print(ms.selected_stream["link"])
        audio_url = ms.selected_stream.get("audio_url", "")
        if audio_url:
            print(f"Audio: {audio_url}")
        sub_url = ms.selected_stream.get("subtitle_url", "")
        if sub_url:
            print(f"Subtitle: {sub_url}")
        subtitles = ms.selected_stream.get("subtitles") or ms.selected_stream.get("vtt") or []
        if isinstance(subtitles, list) and len(subtitles) > 1:
            for sub in subtitles:
                s_url = sub.get("url") or sub.get("file")
                s_lbl = sub.get("label", "Unknown")
                if s_url and s_url != sub_url:
                    print(f"Subtitle ({s_lbl}): {s_url}")
        ref = ms.selected_stream.get("referer", "")
        if ref:
            print(f"Referer: {ref}")
        return "QUIT"


    if getattr(args, 'download', False):
        app_core._exit_player_screen(close_alt=True)

        if not getattr(args, "no_tui", False) and not getattr(flags, "plain_mode", False) and sys.stdin.isatty() and sys.stdout.isatty():
            downloader_choice = getattr(args, "downloader", cfg.get("downloader", "auto"))
            download_dir = cfg.get("download_dir", "")
            from media_cli.media.download_worker import DownloadItem, get_global_worker
            from media_cli.ui.download_manager import run_download_manager
            from media_cli.media.download import sanitize_filename

            safe_title = sanitize_filename(ms.show_title) or ms.show_title
            target_dir = os.path.join(download_dir, safe_title)
            try:
                os.makedirs(target_dir, exist_ok=True)
            except Exception:
                pass

            worker = get_global_worker(download_dir)

            target_eps = []
            if ms.download_batch_end is not None:
                for idx in range(ms.current_ep_index, ms.total_eps):
                    epid = episode_id_at(episode_ids, idx)
                    eplbl = playback_mod._display_episode_label(s_ctx, epid, ttype)
                    target_eps.append((epid, eplbl))
                    try:
                        if int(float(str(eplbl))) >= int(float(str(ms.download_batch_end))):
                            break
                    except ValueError:
                        if idx >= ms.total_eps - 1:
                            break
            else:
                target_eps.append((ms.current_ep, current_ep_label))

            def _make_resolver(show_id, ep_id, ttype, provider_id, quality):
                def _res(item):
                    stream_res = app_core.fetch_episode_stream(
                        show_id,
                        ep_id,
                        ttype,
                        quality,
                        provider_id=provider_id,
                        exclude_sources=getattr(item, "failed_mirrors", set()),
                    )
                    if not stream_res or not stream_res[0]:
                        raise RuntimeError(f"No stream found for episode {ep_id}")
                    stream, first_source_name = stream_res[0], stream_res[1]
                    raw_url = stream.get("link") or stream.get("streamUrl") or stream.get("sourceUrl") or ""
                    if not raw_url:
                        raise RuntimeError("No stream URL in resolved stream")
                    item.current_mirror_name = first_source_name or stream.get("source_name") or stream.get("source_parent_name") or "selected mirror"
                    item.current_mirror_url = raw_url
                    item.logs.append(f"▶ Testing mirror: {item.current_mirror_name}")
                    headers = dict(stream.get("headers") or {})
                    referer = stream.get("referer", "")
                    if referer and "Referer" not in headers:
                        headers["Referer"] = referer
                    proxy_srv = None
                    if (
                        stream.get("requires_proxy")
                        or "uwucdn.top" in raw_url
                        or "owocdn.top" in raw_url
                        or "kwik." in referer
                        or "megap." in raw_url
                        or "akirax.buzz" in raw_url
                    ):
                        try:
                            from media_cli.media.local_proxy import start_local_proxy
                            raw_url, proxy_srv = start_local_proxy(raw_url, headers=headers, referer=referer)
                        except Exception:
                            pass
                    item.audio_tracks = list(stream.get("audio_tracks") or [])
                    item.audio_url = stream.get("audio_url", "")
                    item.subtitles = list(stream.get("subtitles") or stream.get("vtt") or [])
                    item.subtitle_url = stream.get("subtitle_url", "")
                    return raw_url, headers, proxy_srv
                return _res

            def _on_complete(item):
                try:
                    from media_cli.core.storage import collect_downloaded_episode_files, register_downloaded_episode_files
                    f_key = sanitize_filename(item.title) or item.title
                    ep_str = str(getattr(item, "ep_id", item.episode))
                    files = list(getattr(item, "output_files", []) or collect_downloaded_episode_files(item.destination_path))
                    register_downloaded_episode_files(
                        f_key,
                        ep_str,
                        files,
                        metadata=getattr(item, "show_metadata", None),
                    )
                except Exception:
                    pass

            quality = cfg.get("quality", "best")
            ext = ".mkv"

            for epid, eplbl in target_eps:
                item_id = f"{ms.show_id or safe_title}:{epid}"
                dest_path = os.path.join(target_dir, f"{safe_title} - Episode {eplbl}{ext}")
                item = DownloadItem(
                    id=item_id,
                    title=ms.show_title,
                    episode=f"Episode {eplbl}",
                    destination_path=dest_path,
                    engine=downloader_choice if downloader_choice in ("yt-dlp", "aria2c", "ffmpeg") else "auto",
                    resolve_fn=_make_resolver(ms.show_id, epid, ttype, provider_id, quality),
                    on_complete=_on_complete,
                    ep_id=epid,
                    show_metadata=s_ctx,
                    download_sub_langs=getattr(args, "download_sub_langs", None),
                    download_audio_langs=getattr(args, "download_audio_langs", None),
                    subtitle_mode=getattr(args, "subtitle_mode", None),
                )
                worker.add_item(item)

            manager_result = run_download_manager(worker, standalone=True)
            args.download = False
            ms.selected_stream = None
            app_core._clear_streams()
            if manager_result == "QUIT":
                return "QUIT"
            if getattr(flags, "plain_mode", False) or not sys.stdin.isatty() or getattr(args, "_cli_episode_passed", False):
                return "QUIT"
            return "DETAILS"

        exclude_sources = set()
        download_ok = False
        downloader_choice = getattr(args, "downloader", cfg.get("downloader", "auto"))

        extra_args = getattr(args, "extra_args", [])
        if extra_args and extra_args[0] == "--":
            extra_args = extra_args[1:]

        while True:
            download_ok = app_core.download_episode(
                ms.show_title, current_ep_label, ms.selected_stream,
                cfg.get("download_dir", ""), downloader=downloader_choice,
                extra_args=extra_args,
                download_sub_langs=getattr(args, "download_sub_langs", None),
                download_audio_langs=getattr(args, "download_audio_langs", None),
                subtitle_mode=getattr(args, "subtitle_mode", None),
            )
            if download_ok:
                try:
                    from media_cli.core.storage import collect_downloaded_episode_files, register_downloaded_episode_files
                    from media_cli.media.download import sanitize_filename
                    title = ms.show_title
                    folder_key = sanitize_filename(title) or title

                    def build_offline_metadata(s):
                        meta = {}
                        keys_to_keep = [
                            "id", "_id", "provider_id", "name", "englishName", "nativeName", "altNames",
                            "thumbnail", "description", "type", "format",
                            "status", "season", "airedStart", "airedEnd", "startDate",
                            "endDate", "score", "genres", "tags", "aniListId", "malId",
                            "_display_name", "_display_english_name", "_anilist_list",
                            "_anilist_progress", "_anilist_score", "anilistMatch",
                            "originalEpisodeCount", "episodeCount", "_provider", "provider",
                            "_provider_name", "_provider_data", "_episode_ids", "_episode_labels",
                            "_episode_ids_by_ttype", "availableEpisodes", "availableEpisodesDetail",
                            "_local_progress", "_local_episode_label", "watched_episodes"
                        ]
                        for k in keys_to_keep:
                            if k in s:
                                meta[k] = s[k]
                        if "originalEpisodeCount" not in meta and "episodeCount" in s:
                            meta["originalEpisodeCount"] = s["episodeCount"]
                        return meta

                    offline_file = app_core.find_offline_file_for_episode(title, current_ep_label, cfg)
                    if offline_file:
                        register_downloaded_episode_files(
                            folder_key,
                            str(ms.current_ep),
                            collect_downloaded_episode_files(offline_file),
                            metadata=build_offline_metadata(s_ctx),
                        )
                except Exception:
                    pass
                break

            exclude_sources.add(first_source_name)
            print(f"{YELLOW}Download failed on mirror '{first_source_name}'. Trying next mirror...{RESET}")

            res = app_core.fetch_episode_stream(
                ms.show_id,
                ms.current_ep,
                ttype,
                cfg.get("quality", "best"),
                provider_id=provider_id,
                exclude_sources=exclude_sources,
                ep_data=ep_data,
            )
            if not res:
                break
            ms.selected_stream, first_source_name, _, streams = res
            app_core._extend_streams(streams)

        if not download_ok:
            print(f"\n{RED}All mirrors failed for EP {current_ep_label}. Skipping to next episode...{RESET}")

        download_batch_end = ms.download_batch_end if ms.download_batch_end is not None else current_ep_label
        try:
            is_less = int(float(str(download_batch_end))) > int(float(str(current_ep_label)))
        except ValueError:
            is_less = ms.current_ep_index < (ms.total_eps - 1)

        if is_less and ms.current_ep_index + 1 < ms.total_eps:
            ms.current_ep_index += 1
            ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
            ms.selected_stream = None
            app_core._clear_streams()
            return "PLAY"
        else:
            print(f"\n{GREEN}Downloads complete.{RESET}")
            if download_ok:
                app_core.set_action_feedback(ui.ui_show_ctx, f"✔ Downloaded {playback_mod._fmt_ep(current_ep_label)}")
                if getattr(ms, "_is_downloads", False):
                    new_f = app_core.find_offline_file_for_episode(ms.show_title, current_ep_label, cfg)
                    if new_f:
                        ms._download_files[str(ms.current_ep)] = new_f
                        ms._download_files[str(current_ep_label)] = new_f
            else:
                app_core.set_action_feedback(ui.ui_show_ctx, f"✘ Download failed for {playback_mod._fmt_ep(current_ep_label)}")
            args.download = False
            if hasattr(args, "_from_action_menu_download"):
                args._from_action_menu_download = False
            ms.selected_stream = None
            app_core._clear_streams()
            if getattr(ms, "_binge_auto_downloading", False):
                ms._binge_auto_downloading = False
                if download_ok:
                    return "PLAY"
            if getattr(flags, "plain_mode", False) or not sys.stdin.isatty() or getattr(args, "_cli_episode_passed", False):
                return "QUIT"
            time.sleep(1.0)
            return "DETAILS"



    # ── Launch player ─────────────────────────────────────────────
    def fetch_cb(ep_num):
        if isinstance(ep_num, int) and 1 <= ep_num <= len(episode_ids):
            ep_idx = ep_num - 1
        else:
            ep_idx = episode_index_for_id(
                episode_ids, ep_num, labels=ui.ui_show_ctx.get("_episode_labels")
            )
        if ep_idx is None:
            return None

        target_ep = episode_id_at(episode_ids, ep_idx)
        try:
            app_core._ipc_player.prefetched_ep_id = str(target_ep)
        except Exception:
            pass

        local_res = _local_stream_for_episode(ui.ui_show_ctx, ms.show_title, target_ep, ttype, cfg, ms)
        if local_res:
            return local_res

        if getattr(ms, "_is_downloads", False):
            return None

        if aniskip_enabled and mal_id:
            from ..core.workers import DaemonExecutor

            from ..media.aniskip import fetch_skip_times
            with DaemonExecutor(max_workers=2) as pool:
                pool.submit(fetch_skip_times, mal_id, episode_progress_number(target_ep))
                fut_stream = pool.submit(
                    app_core.fetch_episode_stream,
                    ms.show_id,
                    target_ep,
                    ttype,
                    cfg.get("quality", "best"),
                    provider_id=provider_id,
                )
                return fut_stream.result()

        return app_core.fetch_episode_stream(
            ms.show_id,
            target_ep,
            ttype,
            cfg.get("quality", "best"),
            provider_id=provider_id,
        )

    is_binge = args.binge or cfg.get("binge", False)
    custom_skip_intervals = None

    if app_core.is_termux():
        try:
            player = _select_android_player(
                args.player or cfg.get("player"),
                poster_manager=getattr(app_core, "_poster_manager", None), ui=ui,
            )
        except PreparationCancelled:
            app_core._exit_player_screen()
            app_core.set_action_feedback(ui.ui_show_ctx, "Loading cancelled.")
            return "DETAILS"

        aniskip_enabled = getattr(args, "aniskip", None)
        if aniskip_enabled is None:
            aniskip_enabled = cfg.get("aniskip", cfg.get("aniskip_enabled", True))

        action_show = ui.ui_show_ctx or {}
        try:
            mal_id = load_mal_id()
        except PreparationCancelled:
            app_core._exit_player_screen()
            app_core.set_action_feedback(ui.ui_show_ctx, "Loading cancelled.")
            return "DETAILS"
        from ..media import custom_skips as custom_skip_mod
        custom_skip = None
        title_keys = custom_skip_mod.stable_title_keys(action_show, ms.show_id)
        disabled_custom = any(str(key) in getattr(ms, "custom_skip_disabled_titles", set()) for key in title_keys)
        if not disabled_custom:
            custom_skip = custom_skip_mod.resolve_custom_skips(
                show=action_show,
                fallback_show_id=ms.show_id,
                provider_id=provider_id,
                session_store=getattr(ms, "custom_skip_sessions", {}),
                saved_entries=app_core.get_custom_skips(action_show, ms.show_id),
            )
        custom_skip_intervals = custom_skip["intervals"] if custom_skip else None

        trigger_background_auto_download(action_show, ms.current_ep_index, episode_ids, ttype, cfg, ms, ms.selected_stream)

        from ..playback.engine import (
            _fetch_android_skip_times,
            _launch_android_player,
            _prepare_android_proxy,
            _prepare_android_subtitles,
        )

        result = app_core.play_android(
            ms.show_title,
            current_ep_label,
            ms.selected_stream,
            fetch_cb,
            player,
            ms.total_eps,
            ms.show_id,
            is_binge,
            mal_id=mal_id,
            aniskip_enabled=aniskip_enabled,
            action_show=action_show,
            ttype=ttype,
            cfg={**cfg, "auto_download_next": auto_download_mode(cfg, ms.auto_download_next)},
            ep_index=ms.current_ep_index,
            fetch_skip_times_fn=_fetch_android_skip_times,
            prepare_subtitles_fn=_prepare_android_subtitles,
            prepare_proxy_fn=_prepare_android_proxy,
            launch_command_fn=_launch_android_player,
        )

        if first_source_name is not None and first_source_name != "Local File" and not getattr(ms, "_is_downloads", False):
            exclude = {first_source_name}
            app_core.start_bg_resolve(ep_data, exclude, show_id=ms.show_id, ep=ms.current_ep, ttype=ttype, provider_id=provider_id, quality=quality)

        if result == "NEXT" and ms.current_ep_index + 1 < ms.total_eps:
            ms.current_ep_index += 1
            ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
            ms.selected_stream = None
            app_core._clear_streams()
            return "PLAY"

        elif result == "PREV" and ms.current_ep_index > 0:
            ms.current_ep_index -= 1
            ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
            ms.selected_stream = None
            app_core._clear_streams()
            return "PLAY"

        if result:
            app_core.touch_history(action_show, ttype)
            cur_resume = app_core.get_resume_time(ms.show_id, ms.current_ep) or 0
            if cur_resume <= 0:
                app_core.save_resume_time(ms.show_id, ms.current_ep, 1)

        app_core._exit_player_screen()
        return "DETAILS"

    else:
        if first_source_name is not None and first_source_name != "Local File" and not getattr(ms, "_is_downloads", False):
            exclude = {first_source_name}
            app_core.start_bg_resolve(ep_data, exclude, show_id=ms.show_id, ep=ms.current_ep, ttype=ttype, provider_id=provider_id, quality=quality)

        next_episode = episode_id_at(episode_ids, ms.current_ep_index + 1) if ms.current_ep_index + 1 < ms.total_eps else None

        aniskip_enabled = getattr(args, "aniskip", None)
        if aniskip_enabled is None:
            aniskip_enabled = cfg.get("aniskip", cfg.get("aniskip_enabled", True))

        aniskip_auto = getattr(args, "auto_skip", None)
        if aniskip_auto is None:
            aniskip_auto = cfg.get("auto_skip", cfg.get("aniskip_auto", True))


        action_show = ui.ui_show_ctx or {}
        try:
            mal_id = load_mal_id()
        except PreparationCancelled:
            app_core._exit_player_screen()
            app_core.set_action_feedback(ui.ui_show_ctx, "Loading cancelled.")
            return "DETAILS"
        from ..media import custom_skips as custom_skip_mod
        custom_skip = custom_skip_mod.resolve_custom_skips(
            show=action_show,
            fallback_show_id=ms.show_id,
            provider_id=provider_id,
            session_store=getattr(ms, "custom_skip_sessions", {}),
            saved_entries=app_core.get_custom_skips(action_show, ms.show_id),
        )
        custom_skip_intervals = custom_skip["intervals"] if custom_skip else None

        trigger_background_auto_download(action_show, ms.current_ep_index, episode_ids, ttype, cfg, ms, ms.selected_stream)

        start_time_override = getattr(ms, "pending_resume_time", None)
        ms.pending_resume_time = None

        try:
            from ..ui.player_screen import loading_cancel_requested
            cancel_check = loading_cancel_requested
        except Exception:
            cancel_check = None

        result, percent, time_pos, duration, played_seconds = app_core.play_desktop(
            ms.show_title, current_ep_label, ms.selected_stream, fetch_cb,
            ms.total_eps, is_binge, ms.show_id, ms.pending_osd_msg,
            ms.current_ep_index, next_episode,
            mal_id=mal_id, aniskip_enabled=aniskip_enabled, aniskip_auto=aniskip_auto,
            custom_skip_intervals=custom_skip_intervals,
            start_time_override=start_time_override,
            cancel_check=cancel_check,
        )

        ms.pending_osd_msg = ""
        app_core._exit_player_screen()

        try:
            from ..ui.player_screen import loading_cancel_requested
            cancelled_after_playback = loading_cancel_requested()
        except Exception:
            cancelled_after_playback = False

        if result == "CANCELLED" or cancelled_after_playback:
            _logger.debug("Playback loading cancelled; returning to details result=%r", result)
            app_core.set_action_feedback(ui.ui_show_ctx, "Loading cancelled.")
            return "DETAILS"

        if result in ("ERROR", "NEXT_MIRROR"):
            is_manual_switch = (result == "NEXT_MIRROR")
            failed_stream = ms.selected_stream
            failed_name = (failed_stream.get("source_name") or "selected mirror") if failed_stream else "selected mirror"
            failed_name.split(" (")[0].strip() if failed_name else ""

            if is_manual_switch:
                app_core.info(f"Switching from '{failed_name}' to next mirror...")
                app_core._ipc_player.send_cmd("show-text", mirror_switch_osd(), 3000)
            else:
                app_core.warn(f"Playback failed on '{failed_name}'. Attempting fallback to next mirror...")
                app_core._ipc_player.send_cmd("show-text", mirror_failed_osd(failed_name), 10000)

            # Preserve playback position so fallback mirror resumes right where the stream stalled/switched
            if time_pos > 2:
                ms.pending_resume_time = max(0.0, time_pos - 1.0)
                app_core.save_resume_time(ms.show_id, ms.current_ep, int(time_pos))
                app_core.save_resume_time(ms.show_id, current_ep_label, int(time_pos))

            cur_key = (ms.show_id, ms.current_ep, ttype, provider_id, quality)
            if failed_stream and not is_manual_switch:
                app_core._prune_dead_stream(cur_key, failed_stream.get("link") or failed_stream.get("streamUrl"))

            # Track all failed/skipped mirrors across fallback attempts for this episode
            failed_mirrors = getattr(ms, "_failed_mirrors", None)
            if failed_mirrors is None:
                failed_mirrors = set()
            if failed_name:
                failed_mirrors.add(failed_name)
            if failed_stream and (failed_stream.get("link") or failed_stream.get("streamUrl")):
                failed_mirrors.add(failed_stream.get("link") or failed_stream.get("streamUrl"))
            ms._failed_mirrors = failed_mirrors

            from ..ui.player_screen import activate, reset_loading_cancel
            app_core.enter_alt_screen()
            activate(ui.ui_show_ctx, ms.current_ep, ms.total_eps)
            _player_ui_state.update({
                "ttype": ttype, "current_ep_label": current_ep_label,
                "loading_provider_name": p_name, "loading_start_time": time.time(),
            })
            reset_loading_cancel()
            next_stream = None
            fallback_res = None
            try:
                cached_ep_data = app_core._get_cached_ep_data(cur_key)
                cached_streams = _load_player_cached_streams(
                    cached_ep_data, cur_key, poster_manager=getattr(app_core, "_poster_manager", None), ui=ui,
                )
                remaining = [
                    s for s in cached_streams
                    if (s.get("link") or s.get("streamUrl")) not in failed_mirrors
                    and (s.get("source_name") or "") not in failed_mirrors
                    and (s.get("source_parent_name") or "") not in failed_mirrors
                ]
                if remaining:
                    from ..media.sources import quality_preference_key
                    target_quality = getattr(args, "quality", None) or cfg.get("quality", "best")

                    def _failover_sort_key(s):
                        sname = (s.get("source_name") or "").lower()
                        res = s.get("resolution") or sname
                        is_hard = "hardsub" in sname or "hard-sub" in sname or "hard sub" in sname
                        is_soft = "softsub" in sname or "all sub" in sname or "multi sub" in sname
                        is_donghua = str(provider_id or "").lower() in {"animexin", "lucifer", "animekhor"}
                        if is_donghua:
                            sub_rank = 0 if is_hard else (2 if is_soft else 1)
                        else:
                            sub_rank = 0 if is_soft else (2 if is_hard else 1)
                        q_key = quality_preference_key(res, target_quality)
                        return (
                            streams_mod.torrent_fallback_sort_value(s),
                            sub_rank,
                            q_key,
                            s.get("source_priority", 4),
                            streams_mod.torrent_size_sort_value(s),
                        )

                    sorted_remaining = sorted(remaining, key=_failover_sort_key)
                    next_stream = _probe_player_mirrors(
                        tuple(sorted_remaining), cur_key, recovery=True,
                        poster_manager=getattr(app_core, "_poster_manager", None), ui=ui,
                    )

                if not next_stream:
                    if not ep_data:
                        ep_data = app_core._get_cached_ep_data(cur_key)
                    fallback_res = _fetch_player_fallback(
                        ep_data, (ms.show_id, ms.current_ep, ttype, provider_id, cfg.get("quality", "best")),
                        exclude_sources=frozenset(failed_mirrors),
                        poster_manager=getattr(app_core, "_poster_manager", None), ui=ui,
                    )
            except PreparationCancelled:
                ms.selected_stream = None
                app_core.set_action_feedback(ui.ui_show_ctx, "Loading cancelled.")
                return "DETAILS"
            finally:
                app_core._exit_player_screen()
            if fallback_res:
                next_stream, fb_src_name, _, fb_streams = fallback_res
                app_core._extend_streams(fb_streams, key=cur_key)

            if next_stream:
                next_name = next_stream.get("source_name", "alternate mirror")
                if is_manual_switch:
                    switch_msg = f"Switched from {failed_name} • Loading mirror '{next_name}'..."
                    ms.pending_osd_msg = mirror_loading_osd(next_name)
                    app_core._ipc_player.send_cmd("show-text", mirror_loading_osd(next_name), 3000)
                else:
                    switch_msg = f"Playback failed on {failed_name} • Trying fallback mirror '{next_name}'..."
                    ms.pending_osd_msg = f"{failed_name} failed\n{mirror_loading_osd(next_name)}"
                    app_core._ipc_player.send_cmd("show-text", ms.pending_osd_msg, 5000)
                app_core.info(switch_msg)
                ms.selected_stream = next_stream
                app_core.set_action_feedback(ui.ui_show_ctx, switch_msg)
                return "PLAY"

            # All mirrors exhausted
            app_core._ipc_player.quit()
            ms._failed_mirrors = set()
            if is_manual_switch:
                app_core.set_action_feedback(
                    ui.ui_show_ctx,
                    f"No other alternate mirrors available after {failed_name}."
                )
            else:
                app_core.set_action_feedback(
                    ui.ui_show_ctx,
                    f"Playback failed on {failed_name} and no alternate mirrors were available."
                )
            return "DETAILS"

        # Smart Auto-Scrobble & Timestamping (80% OR <150s remaining)
        start_time = getattr(app_core._ipc_player, "resume_time", 0) or 0
        auto_scrobbled = playback_looks_complete(
            result, percent, time_pos, duration, played_seconds, start_time=start_time
        )
        pending_completion = app_core.get_pending_completion(ms.show_id)

        if (pending_completion
                and str(pending_completion.get("next_episode")) == str(ms.current_ep)
                and str(pending_completion.get("episode")) != str(ms.current_ep)
                and time_pos >= 120):
            pending_ep = pending_completion.get("episode")
            pending_progress = int(pending_completion.get("progress") or 0)
            sync_pending = resolve_tracking_fn(ui.search_prev_state, args, cfg, ui.ui_show_ctx)

            if sync_pending:
                tkn = cfg.get("anilist_token")
                if tkn and pending_progress:
                    show_ctx = ui.ui_show_ctx
                    new_status = tracking_status_for_progress(show_ctx, pending_progress)

                    def _pending_sync_success(ep=pending_progress, ctx=show_ctx):
                        app_core.set_action_feedback(ctx, f"✔ Synced EP {ep} to AniList")
                    def _pending_sync_failure(ctx=show_ctx):
                        app_core.set_action_feedback(ctx, "Saved offline • AniList sync pending")

                    queued = app_core.queue_anilist_progress(
                        tkn, ms.show_title, pending_progress, al_id,
                        show_ctx, ttype, new_status,
                        on_success=_pending_sync_success,
                        on_failure=_pending_sync_failure,
                        pending_completion={
                            "show_id": str(ms.show_id),
                            "episode": str(pending_ep),
                        },
                    )
                    if queued:
                        app_core.set_action_feedback(show_ctx, f"Sync queued: EP {pending_progress}")
                else:
                    sync_pending = False

            if not sync_pending:
                app_core.save_resume_time(ms.show_id, pending_ep, 0)
                app_core.clear_pending_completion(ms.show_id)

        completed_for_history = playback_updates_history(
            result, percent, time_pos, duration, played_seconds, start_time=start_time
        )
        should_update_history = (
            not getattr(ms, "_is_downloads", False)
            and completed_for_history
        )
        if should_update_history:
            app_core.save_history(ui.ui_show_ctx, ms.current_ep, ttype)
            _mark_watched_in_show(ui.ui_show_ctx, ms.current_ep)
        elif not getattr(ms, "_is_downloads", False) and played_seconds >= 5:
            app_core.touch_history(ui.ui_show_ctx, ttype)

        if getattr(ms, "_is_downloads", False) and completed_for_history:
            try:
                folder_name = ui.ui_show_ctx.get("_folder_name", ms.show_title)
                app_core.record_download_watch_progress(
                    ui.ui_show_ctx,
                    folder_name,
                    ms.current_ep,
                    ttype,
                    sync_enabled=sync_enabled,
                )
                _mark_watched_in_show(ui.ui_show_ctx, ms.current_ep)
            except Exception:
                pass
        if completed_for_history:
            try:
                _auto_delete_watched_downloads(ui.ui_show_ctx, episode_ids, ttype, cfg, ms)
            except Exception:
                _logger.exception("Automatic watched-download cleanup failed")

        if auto_scrobbled or (duration > 0 and time_pos >= duration - 30):
            app_core.save_resume_time(ms.show_id, ms.current_ep, 0)
        elif time_pos > 30:
            app_core.save_resume_time(ms.show_id, ms.current_ep, time_pos)
        else:
            app_core.save_resume_time(ms.show_id, ms.current_ep, 0)

        if result == "QUIT" or result == "EOF":
            if auto_scrobbled and resolve_tracking_fn(ui.search_prev_state, args, cfg, ui.ui_show_ctx):
                tkn = cfg.get("anilist_token")
                if tkn:
                    show_ctx = ui.ui_show_ctx
                    progress_ep = episode_progress_number(ms.current_ep, ms.current_ep_index + 1)
                    new_status = tracking_status_for_progress(show_ctx, progress_ep)
                    queued = app_core.queue_anilist_progress(
                        tkn, ms.show_title, progress_ep, al_id,
                        show_ctx, ttype, new_status,
                        on_success=lambda ep=progress_ep, ctx=show_ctx:
                            app_core.set_action_feedback(ctx, f"✔ Synced EP {ep} to AniList"),
                        on_failure=lambda ctx=show_ctx:
                            app_core.set_action_feedback(ctx, "Saved offline • AniList sync pending"),
                    )
                    if queued:
                        app_core.set_action_feedback(show_ctx, f"Sync queued: EP {progress_ep}")

            if result == "EOF" and (args.binge or cfg.get("binge")) and auto_scrobbled:
                if ms.current_ep_index + 1 < ms.total_eps:
                    next_ep = episode_id_at(episode_ids, ms.current_ep_index + 1)
                    if getattr(ms, "_is_downloads", False):
                        lbl_next = playback_mod._display_episode_label(ui.ui_show_ctx, next_ep, ttype)
                        dl_files = getattr(ms, "_download_files", {})
                        is_next_dl = str(next_ep) in dl_files or str(lbl_next) in dl_files or bool(app_core.find_offline_file_for_episode(ms.show_title, lbl_next, cfg))
                        if not is_next_dl:
                            if auto_download_mode(cfg, getattr(ms, "auto_download_next", None)) == "adaptive":
                                if app_core.is_online():
                                    ms.current_ep_index += 1
                                    ms.current_ep = next_ep
                                    ms.selected_stream = None
                                    ms._failed_mirrors = set()
                                    app_core._clear_streams()
                                    ms._binge_auto_downloading = True
                                    args.download = True
                                    app_core._ipc_player.quit()
                                    print(f"\n\033[1;36mAuto-downloading next episode ({lbl_next}) for binge playback…\033[0m")
                                    return "PLAY"
                                else:
                                    app_core._ipc_player.quit()
                                    app_core.set_action_feedback(
                                        ui.ui_show_ctx,
                                        f"Offline: cannot download EP {lbl_next}. Reached end of downloaded episodes."
                                    )
                                    return "DETAILS"
                            else:
                                app_core._ipc_player.quit()
                                app_core.set_action_feedback(
                                    ui.ui_show_ctx,
                                    "Reached end of downloaded episodes."
                                )
                                return "DETAILS"

                    if flags.incognito_mode:
                        ms.pending_osd_msg = None
                    else:
                        ms.pending_osd_msg = marked_watched_osd(
                            ms.current_ep,
                            resolve_tracking_fn(ui.search_prev_state, args, cfg, ui.ui_show_ctx)
                        )

                    ms.current_ep_index += 1
                    ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
                    ms.selected_stream = None
                    ms._failed_mirrors = set()
                    app_core._clear_streams()
                    return "PLAY"
                else:
                    app_core._ipc_player.quit()
                    print(f"\n{GREEN}Finished the last episode.{RESET}")
                    return "QUIT"
            else:
                app_core._ipc_player.quit()
                if not auto_scrobbled:
                    if played_seconds < 5.0 and duration <= 0:
                        ep_fmt = playback_mod._fmt_ep(
                            current_ep_label or clean_episode_identifier(str(ms.current_ep)) or ms.current_ep
                        )
                        app_core.set_action_feedback(
                            ui.ui_show_ctx,
                            f"Playback failed or was interrupted on {ep_fmt}."
                        )
                    elif time_pos > 0:
                        next_ep = episode_id_at(episode_ids, ms.current_ep_index + 1) if (ms.current_ep_index is not None and ms.current_ep_index + 1 < ms.total_eps) else ms.current_ep
                        progress_ep = episode_progress_number(ms.current_ep, (ms.current_ep_index or 0) + 1)
                        app_core.save_pending_completion(
                            ms.show_id, ms.current_ep, progress_ep,
                            next_ep, time_pos, duration
                        )
                        app_core.save_resume_time(ms.show_id, ms.current_ep, time_pos)
                if auto_scrobbled:
                    ms.current_ep_index = None
                    ms.current_ep = None
                return "DETAILS"

        elif result == "NEXT":
            if ms.current_ep_index + 1 < ms.total_eps:
                next_ep = episode_id_at(episode_ids, ms.current_ep_index + 1)
                if flags.incognito_mode:
                    ms.pending_osd_msg = None
                elif auto_scrobbled and resolve_tracking_fn(ui.search_prev_state, args, cfg, ui.ui_show_ctx):
                    tkn = cfg.get("anilist_token")
                    if tkn:
                        show_ctx = ui.ui_show_ctx
                        progress_ep = episode_progress_number(ms.current_ep, ms.current_ep_index + 1)
                        new_status = tracking_status_for_progress(show_ctx, progress_ep)
                        queued = app_core.queue_anilist_progress(
                            tkn, ms.show_title, progress_ep, al_id,
                            show_ctx, ttype, new_status,
                            on_success=lambda ep=progress_ep, ctx=show_ctx:
                                app_core.set_action_feedback(ctx, f"✔ Synced EP {ep} to AniList"),
                            on_failure=lambda ctx=show_ctx:
                                app_core.set_action_feedback(ctx, "Saved offline • AniList sync pending"),
                        )
                        ms.pending_osd_msg = (
                            sync_queued_osd(ms.current_ep)
                            if queued else marked_watched_osd(ms.current_ep, False)
                        )
                elif not auto_scrobbled:
                    progress_ep = episode_progress_number(ms.current_ep, ms.current_ep_index + 1)
                    app_core.save_pending_completion(ms.show_id, ms.current_ep, progress_ep, next_ep, time_pos, duration)
                    if time_pos > 0:
                        app_core.save_resume_time(ms.show_id, ms.current_ep, time_pos)
                    ms.pending_osd_msg = pending_completion_osd(ms.current_ep, next_ep)

                ms.current_ep_index += 1
                ms.current_ep = next_ep
                ms.selected_stream = None
                app_core._clear_streams()
                return "PLAY"
            else:
                app_core._ipc_player.quit()
                print(f"\n{GREEN}Finished the last episode.{RESET}")
                return "QUIT"

        elif result == "PREV":
            if ms.current_ep_index > 0:
                ms.current_ep_index -= 1
                ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
                ms.selected_stream = None
                app_core._clear_streams()
                return "PLAY"
            else:
                app_core._ipc_player.quit()
                return "DETAILS"
        else:
            app_core._ipc_player.quit()
            return "DETAILS"
