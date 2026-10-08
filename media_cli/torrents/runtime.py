"""Runtime support for preparing torrent streams for playback."""

from __future__ import annotations

import shutil
import tempfile
import time
import re
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


VIDEO_EXTS = {".mkv", ".mp4", ".webm", ".avi", ".mov", ".m4v", ".ts"}
NON_VIDEO_EXTS = {
    ".zip", ".rar", ".7z", ".tar", ".gz", ".xz",
    ".iso", ".exe", ".msi", ".apk", ".dmg", ".pkg",
    ".pdf", ".epub", ".mobi", ".cbz", ".cbr",
    ".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav",
    ".srt", ".ass", ".ssa", ".vtt", ".txt", ".nfo", ".jpg", ".jpeg", ".png", ".webp",
}
VIDEO_NAME_HINT_RE = re.compile(
    r"\b(?:2160p|1440p|1080p|720p|480p|web[-_. ]?dl|webrip|bluray|bdrip|hdtv|hevc|h[ ._-]?264|h[ ._-]?265|x264|x265|av1|aac|opus)\b",
    re.I,
)
_logger = logging.getLogger(__name__)
ProgressCallback = Callable[[str], None]
CancelCheck = Callable[[], bool]


class TorrentCancelled(RuntimeError):
    """Raised when the user cancels torrent preparation before mpv opens."""


@dataclass(frozen=True)
class TorrentRuntimeConfig:
    cache_root: Path | str | None = None
    metadata_timeout: int = 90
    buffer_mb: int = 64
    tail_buffer_mb: int = 0
    tick_seconds: float = 2.0
    stalled_ticks: int = 6
    stalled_min_ready_mb: float = 2.0
    start_on_stall: bool = False
    no_swarm_timeout: float = 60.0


@dataclass
class PreparedTorrentStream:
    path: str
    runtime: "TorrentRuntime"

    def close(self) -> None:
        self.runtime.close()


def import_libtorrent():
    try:
        import libtorrent as lt
    except Exception as exc:  # pragma: no cover - depends on host install
        raise RuntimeError(f"libtorrent import failed: {exc}") from exc
    return lt


def has_metadata(handle) -> bool:
    if hasattr(handle, "has_metadata"):
        return bool(handle.has_metadata())
    return bool(getattr(handle.status(), "has_metadata", False))


def add_magnet(lt, session, magnet: str, out_dir: Path):
    if hasattr(lt, "add_magnet_uri"):
        params = {
            "save_path": str(out_dir),
            "storage_mode": lt.storage_mode_t.storage_mode_sparse,
            "flags": lt.torrent_flags.auto_managed,
        }
        if hasattr(lt.torrent_flags, "sequential_download"):
            params["flags"] |= lt.torrent_flags.sequential_download
        return lt.add_magnet_uri(session, magnet, params)

    params = lt.parse_magnet_uri(magnet)
    params.save_path = str(out_dir)
    params.storage_mode = lt.storage_mode_t.storage_mode_sparse
    params.flags |= lt.torrent_flags.auto_managed
    if hasattr(lt.torrent_flags, "sequential_download"):
        params.flags |= lt.torrent_flags.sequential_download
    return session.add_torrent(params)


def file_piece_span(info, file_index: int, file_size: int) -> tuple[int, int]:
    first = info.map_file(file_index, 0, 1).piece
    last = info.map_file(file_index, max(0, file_size - 1), 1).piece
    return int(first), int(last)


def contiguous_ready_mib(handle, first_piece: int, last_piece: int, piece_length: int) -> float:
    pieces = getattr(handle.status(), "pieces", None)
    if pieces is None:
        return 0.0
    ready = 0
    for piece in range(first_piece, last_piece + 1):
        try:
            if not pieces[piece]:
                break
        except Exception:
            break
        ready += 1
    return (ready * piece_length) / 1024 / 1024


def _speed_text(bytes_per_second: int | float) -> str:
    value = float(bytes_per_second or 0)
    for unit in ("B/s", "KiB/s", "MiB/s", "GiB/s"):
        if value < 1024 or unit == "GiB/s":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GiB/s"


def _status_line(prefix: str, handle, *, ready_mib: float | None = None, target_mib: int | None = None) -> str:
    status = handle.status()
    progress = float(getattr(status, "progress", 0.0) or 0.0) * 100.0
    rate = _speed_text(getattr(status, "download_rate", 0) or 0)
    peers = int(getattr(status, "num_peers", 0) or 0)
    seeds = int(getattr(status, "num_seeds", 0) or 0)
    parts = [f"Torrent: {prefix}", f"{progress:.1f}%", rate, f"peers {peers}", f"seeds {seeds}"]
    if ready_mib is not None and target_mib is not None:
        parts.insert(2, f"ready {ready_mib:.1f}/{target_mib}MiB")
    return " • ".join(parts)


def _emit(progress_callback: ProgressCallback | None, message: str) -> None:
    if not progress_callback:
        return
    try:
        progress_callback(message)
    except TorrentCancelled:
        raise
    except Exception:
        pass


def _raise_if_cancelled(cancel_check: CancelCheck | None) -> None:
    if cancel_check and cancel_check():
        raise TorrentCancelled("Torrent loading cancelled.")


def _interruptible_sleep(duration: float, sleep: Callable[[float], None], cancel_check: CancelCheck | None) -> None:
    remaining = max(0.0, float(duration or 0.0))
    if remaining <= 0:
        _raise_if_cancelled(cancel_check)
        sleep(0)
        return
    step = 0.1
    while remaining > 0:
        _raise_if_cancelled(cancel_check)
        chunk = min(step, remaining)
        sleep(chunk)
        remaining -= chunk
    _raise_if_cancelled(cancel_check)


def first_missing_piece(handle, first_piece: int, last_piece: int) -> int:
    pieces = getattr(handle.status(), "pieces", None)
    if pieces is None:
        return first_piece
    for piece in range(first_piece, last_piece + 1):
        try:
            if not pieces[piece]:
                return piece
        except Exception:
            return piece
    return last_piece + 1


def prioritize_window(handle, first_piece: int, last_piece: int, start_piece: int, count: int, *, urgent: bool) -> None:
    if count <= 0:
        return
    deadline_base = 0 if urgent else 1000
    priority = 7 if urgent else 5
    end_piece = min(last_piece + 1, start_piece + count)
    for offset, piece in enumerate(range(max(first_piece, start_piece), end_piece)):
        try:
            handle.piece_priority(piece, priority)
            handle.set_piece_deadline(piece, deadline_base + offset * 100)
        except Exception:
            pass


def prioritize_stream(handle, first_piece: int, last_piece: int, piece_length: int, buffer_mb: int, tail_mb: int) -> None:
    if hasattr(handle, "clear_piece_deadlines"):
        handle.clear_piece_deadlines()
    first_count = max(1, (buffer_mb * 1024 * 1024 + piece_length - 1) // piece_length)
    prioritize_window(handle, first_piece, last_piece, first_piece, first_count, urgent=True)
    if tail_mb > 0:
        tail_count = max(1, (tail_mb * 1024 * 1024 + piece_length - 1) // piece_length)
        prioritize_window(handle, first_piece, last_piece, max(first_piece, last_piece - tail_count + 1), tail_count, urgent=False)


def select_video_file(info) -> tuple[int, int, str]:
    files = info.files()
    video_candidates = []
    plausible_candidates = []
    for idx in range(files.num_files()):
        path = files.file_path(idx)
        size = files.file_size(idx)
        path_text = str(path)
        ext = Path(path_text).suffix.casefold()
        if ext in VIDEO_EXTS:
            video_candidates.append((idx, int(size), str(path)))
        elif ext not in NON_VIDEO_EXTS and int(size) >= 50 * 1024 * 1024 and VIDEO_NAME_HINT_RE.search(path_text):
            plausible_candidates.append((idx, int(size), str(path)))
    if not video_candidates:
        if plausible_candidates:
            return max(plausible_candidates, key=lambda row: row[1])
        raise RuntimeError("No playable video file found in this torrent.")
    return max(video_candidates, key=lambda row: row[1])


class TorrentRuntime:
    def __init__(self, *, cache_root: Path, session: Any, handle: Any, selected_path: str):
        self.cache_root = cache_root
        self.session = session
        self.handle = handle
        self.selected_path = selected_path
        self.closed = False

    @property
    def file_path(self) -> Path:
        return self.cache_root / self.selected_path

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            shutil.rmtree(self.cache_root, ignore_errors=True)
        except Exception:
            pass
        self.session = None
        self.handle = None


def _make_cache_dir(config: TorrentRuntimeConfig) -> Path:
    root = Path(config.cache_root) if config.cache_root else Path(tempfile.gettempdir()) / "anyplay-torrents"
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="stream-", dir=str(root)))


def _wait_for_metadata(
    handle,
    *,
    timeout: int,
    sleep: Callable[[float], None],
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> None:
    deadline = time.monotonic() + max(0, timeout)
    while not has_metadata(handle) and time.monotonic() < deadline:
        _raise_if_cancelled(cancel_check)
        _emit(progress_callback, _status_line("fetching metadata", handle))
        _interruptible_sleep(2, sleep, cancel_check)
    _raise_if_cancelled(cancel_check)
    if not has_metadata(handle):
        raise RuntimeError("torrent metadata timeout")
    _emit(progress_callback, _status_line("metadata ready", handle))


def _wait_for_start_buffer(
    handle,
    *,
    first_piece: int,
    last_piece: int,
    piece_length: int,
    config: TorrentRuntimeConfig,
    sleep: Callable[[float], None],
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
    now: Callable[[], float] = time.monotonic,
) -> None:
    last_ready = -1.0
    stagnant_ticks = 0
    no_swarm_since: float | None = None
    while True:
        _raise_if_cancelled(cancel_check)
        ready_mib = contiguous_ready_mib(handle, first_piece, last_piece, piece_length)
        if ready_mib >= config.buffer_mb:
            _emit(progress_callback, _status_line("buffer ready", handle, ready_mib=ready_mib, target_mib=config.buffer_mb))
            return
        status = handle.status()
        peers = int(getattr(status, "num_peers", 0) or 0)
        seeds = int(getattr(status, "num_seeds", 0) or 0)
        rate = float(getattr(status, "download_rate", 0) or 0)
        if config.no_swarm_timeout > 0 and ready_mib < config.stalled_min_ready_mb and peers <= 0 and seeds <= 0 and rate < 1024:
            current_time = now()
            if no_swarm_since is None:
                no_swarm_since = current_time
            elif current_time - no_swarm_since >= config.no_swarm_timeout:
                message = "torrent stalled: no active peers while buffering"
                _logger.warning("%s ready=%.1fMiB target=%sMiB", message, ready_mib, config.buffer_mb)
                raise RuntimeError(message)
        else:
            no_swarm_since = None
        missing_piece = first_missing_piece(handle, first_piece, last_piece)
        prioritize_window(handle, first_piece, last_piece, missing_piece, 8, urgent=True)
        _emit(progress_callback, _status_line("buffering", handle, ready_mib=ready_mib, target_mib=config.buffer_mb))
        if ready_mib <= last_ready + 0.1:
            stagnant_ticks += 1
        else:
            stagnant_ticks = 0
            last_ready = ready_mib
        if config.start_on_stall and ready_mib >= config.stalled_min_ready_mb and stagnant_ticks >= config.stalled_ticks:
            _emit(progress_callback, _status_line("starting with partial buffer", handle, ready_mib=ready_mib, target_mib=config.buffer_mb))
            return
        _interruptible_sleep(config.tick_seconds, sleep, cancel_check)


def prepare_torrent_stream(
    stream: dict,
    *,
    config: TorrentRuntimeConfig | None = None,
    lt_module: Any | None = None,
    sleep: Callable[[float], None] = time.sleep,
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> PreparedTorrentStream:
    cfg = config or TorrentRuntimeConfig()
    magnet = str(stream.get("link") or stream.get("sourceUrl") or "").strip()
    if str(stream.get("type") or "").lower() != "torrent" or not magnet.startswith("magnet:?"):
        raise ValueError("stream is not a torrent magnet")

    _raise_if_cancelled(cancel_check)
    lt = lt_module or import_libtorrent()
    cache_dir = _make_cache_dir(cfg)
    try:
        session = lt.session({
            "user_agent": "AnyPlay torrent runtime",
            "listen_interfaces": "0.0.0.0:0",
            "enable_dht": True,
            "enable_lsd": True,
            "enable_upnp": True,
            "enable_natpmp": True,
        })
        handle = add_magnet(lt, session, magnet, cache_dir)
        _wait_for_metadata(
            handle,
            timeout=cfg.metadata_timeout,
            sleep=sleep,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )

        info = handle.torrent_file()
        files = info.files()
        selected_idx, selected_size, selected_path = select_video_file(info)
        _emit(progress_callback, f"Torrent: selected {Path(selected_path).name}")
        for idx in range(files.num_files()):
            handle.file_priority(idx, 0)
        handle.file_priority(selected_idx, 7)
        if hasattr(handle, "set_sequential_download"):
            handle.set_sequential_download(True)

        first_piece, last_piece = file_piece_span(info, selected_idx, selected_size)
        piece_length = int(info.piece_length())
        prioritize_stream(handle, first_piece, last_piece, piece_length, cfg.buffer_mb, cfg.tail_buffer_mb)
        _wait_for_start_buffer(
            handle,
            first_piece=first_piece,
            last_piece=last_piece,
            piece_length=piece_length,
            config=cfg,
            sleep=sleep,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
        _raise_if_cancelled(cancel_check)
        runtime = TorrentRuntime(cache_root=cache_dir, session=session, handle=handle, selected_path=selected_path)
        return PreparedTorrentStream(path=str(runtime.file_path), runtime=runtime)
    except Exception:
        shutil.rmtree(cache_dir, ignore_errors=True)
        raise
