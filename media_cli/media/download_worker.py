#!/usr/bin/env python3
"""Unified Download Worker Prototype for Anyplay CLI with Multi-Slot Concurrency and Lazy Resolution.

Features:
- Configurable concurrent download slots (sequential 1, or parallel 2-3)
- Lazy just-in-time stream resolution (prevents URL expiration for items later in queue)
- Internal local proxy support for obfuscated anime CDNs
- Real-time progress parsing (speed, ETA, %, bytes) for yt-dlp, aria2c, and ffmpeg
- Thread-safe SIGSTOP/SIGCONT pause/resume per item
- Pre-download file existence check (skips if file already exists)
- Clean cancellation and proxy server cleanup
- Stderr isolation (no terminal pollution)
"""

from dataclasses import dataclass, field
from enum import Enum
import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from typing import Any, Callable
import urllib.parse
import urllib.request

from media_cli.core.storage import is_valid_download_video
from media_cli.media.languages import filter_tracks_by_language, normalize_lang_code
from media_cli.media.subtitles import download_sidecar_subtitles
from media_cli.media.urls import validate_stream_url

TEST_URL = "https://raw.githubusercontent.com/bower-media-samples/big-buck-bunny-1080p-30s/master/video.mp4"
TMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp_worker")
os.makedirs(TMP_DIR, exist_ok=True)
_logger = logging.getLogger(__name__)


class _DownloadAttemptFailed(RuntimeError):
    """Expected engine failure that should use retry/fallback policy."""


_PROGRESS_LOG_PREFIXES = ("yt-dlp:", "aria2c:", "ffmpeg:")
_MAX_PROGRESS_LOG_LINES = 20


def _append_progress_log(item: DownloadItem, message: str) -> None:
    """Append a progress tick without discarding setup/error history."""
    item.logs.append(message)
    progress_indices = [
        idx for idx, line in enumerate(item.logs)
        if str(line).startswith(_PROGRESS_LOG_PREFIXES)
    ]
    overflow = len(progress_indices) - _MAX_PROGRESS_LOG_LINES
    if overflow <= 0:
        return
    for idx in reversed(progress_indices[:overflow]):
        item.logs.pop(idx)


def _partial_output_path(final_path: str) -> str:
    final_abs = os.path.abspath(os.path.expanduser(final_path))
    final_dir = os.path.dirname(final_abs)
    return os.path.join(final_dir, ".anyplay-partials", os.path.basename(final_abs))


def _commit_partial_output(partial_path: str, final_path: str) -> None:
    if not os.path.isfile(partial_path) or os.path.getsize(partial_path) <= 0:
        raise RuntimeError("ffmpeg did not produce a complete output file")
    os.makedirs(os.path.dirname(os.path.abspath(final_path)), exist_ok=True)
    os.replace(partial_path, final_path)
    try:
        partial_dir = os.path.dirname(partial_path)
        if not os.listdir(partial_dir):
            os.rmdir(partial_dir)
    except OSError:
        pass


def _discard_partial_output(partial_path: str) -> None:
    try:
        if partial_path and os.path.exists(partial_path):
            os.remove(partial_path)
    except OSError:
        pass


def _is_hls_url(url: str) -> bool:
    return ".m3u8" in str(url or "").casefold()


def _output_is_hls_playlist(path: str) -> bool:
    try:
        if not path or not os.path.isfile(path) or os.path.getsize(path) > 1024 * 1024:
            return False
        with open(path, "rb") as fh:
            head = fh.read(512)
        return head.lstrip().startswith(b"#EXTM3U")
    except OSError:
        return False


def probe_stream_duration(url: str, default_duration: float = 1440.0) -> float:
    """Accurately determine stream duration in seconds.
    1. If URL is an m3u8 playlist, fetch it and sum #EXTINF.
    2. Fallback to ffprobe if it's an MP4 or remote stream.
    3. Fallback to default_duration (e.g. 24 mins).
    """
    if "m3u8" in url.lower():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                content = resp.read().decode("utf-8", errors="ignore")
                durations = re.findall(r"#EXTINF:([\d.]+)", content)
                if durations:
                    return sum(float(d) for d in durations)
                # If master playlist with sub-playlists, try first sub-playlist
                sub_urls = [line.strip() for line in content.splitlines() if line.strip() and not line.startswith("#")]
                if sub_urls:
                    sub_url = urllib.parse.urljoin(url, sub_urls[0])
                    sub_req = urllib.request.Request(sub_url, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(sub_req, timeout=3) as sub_resp:
                        sub_content = sub_resp.read().decode("utf-8", errors="ignore")
                        sub_durs = re.findall(r"#EXTINF:([\d.]+)", sub_content)
                        if sub_durs:
                            return sum(float(d) for d in sub_durs)
        except Exception:
            pass

    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", url]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        if res.returncode == 0 and res.stdout.strip():
            dur = float(res.stdout.strip())
            if dur > 0:
                return dur
    except Exception:
        pass

    return default_duration


class DownloadStatus(str, Enum):
    QUEUED = "queued"
    RESOLVING = "resolving"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


@dataclass
class DownloadItem:
    id: str
    title: str
    episode: str
    destination_path: str
    url: str = ""
    engine: str = "auto"  # "yt-dlp", "aria2c", "ffmpeg", or "auto"
    headers: dict = field(default_factory=dict)
    status: DownloadStatus = DownloadStatus.QUEUED
    progress: float = 0.0  # 0.0 to 100.0
    speed_str: str = ""
    eta_str: str = ""
    downloaded_bytes: int = 0
    total_bytes: int = 0
    error: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float = 0.0
    completed_at: float = 0.0
    process: subprocess.Popen | None = None
    _proc_paused: bool = False
    auto_paused: bool = False
    _user_paused: bool = False
    resolve_fn: Callable[["DownloadItem"], tuple[str, dict, Any]] | None = None
    proxy_server: Any = None
    retry_count: int = 0
    max_retries: int = 2
    failed_mirrors: set[str] = field(default_factory=set)
    current_mirror_name: str = ""
    current_mirror_url: str = ""
    logs: list[str] = field(default_factory=list)
    on_complete: Any = None
    ep_id: Any = None
    show_metadata: dict = field(default_factory=dict)
    audio_tracks: list[dict] = field(default_factory=list)
    audio_url: str = ""
    subtitles: list[dict] = field(default_factory=list)
    subtitle_url: str = ""
    download_sub_langs: str | None = None
    download_audio_langs: str | None = None
    subtitle_mode: str | None = None


# Helper regex for aria2c
ARIA2_PATTERN = re.compile(
    r"\[#\w+\s+([^(]+)\(([\d.]+)%\)\s+CN:\d+\s+DL:([^\]\s]+)(?:\s+ETA:([^\]]+))?\]"
)


def format_bytes(b):
    if b is None or b <= 0:
        return "0B"
    for unit in ["B", "KiB", "MiB", "GiB"]:
        if b < 1024:
            return f"{b:.1f}{unit}"
        b /= 1024
    return f"{b:.1f}TiB"


def parse_bytes(s):
    """Parse size string like '21.7MiB' or raw integer bytes."""
    if not s:
        return 0
    s = s.strip()
    try:
        return int(float(s))
    except ValueError:
        pass
    m = re.match(r"^([\d.]+)\s*([A-Za-z]+)$", s)
    if not m:
        return 0
    val, unit = float(m.group(1)), m.group(2).lower()
    mults = {
        "b": 1,
        "k": 1024, "kib": 1024, "kb": 1000,
        "m": 1024**2, "mib": 1024**2, "mb": 1000**2,
        "g": 1024**3, "gib": 1024**3, "gb": 1000**3,
    }
    return int(val * mults.get(unit, 1))


def _stop_proxy_server(proxy_server: Any, *, item_id: str = "", context: str = "") -> None:
    if not proxy_server:
        return
    try:
        from media_cli.media.local_proxy import stop_local_proxy
        stop_local_proxy(proxy_server)
    except Exception:
        detail = f" for download item {item_id}" if item_id else ""
        suffix = f" during {context}" if context else ""
        _logger.warning("Failed to stop local proxy%s%s", detail, suffix, exc_info=True)


def _notify_complete(item: "DownloadItem") -> None:
    if not item.on_complete:
        return
    try:
        item.on_complete(item)
    except Exception:
        _logger.exception("Download completion callback failed for item %s (%s)", item.id, item.title)


def _mark_current_mirror_failed(item: "DownloadItem") -> bool:
    markers = [
        str(getattr(item, "current_mirror_name", "") or "").strip(),
        str(getattr(item, "current_mirror_url", "") or "").strip(),
        str(getattr(item, "url", "") or "").strip(),
    ]
    added = False
    for marker in markers:
        if marker and marker not in item.failed_mirrors:
            item.failed_mirrors.add(marker)
            added = True
    if added:
        mirror = str(getattr(item, "current_mirror_name", "") or "current mirror").strip()
        item.logs.append(f"✖ {mirror} failed after retries.")
    item.current_mirror_name = ""
    item.current_mirror_url = ""
    return added


def _select_download_engine(preferred: str, url: str = "") -> str:
    """Choose the download engine for worker auto mode.

    yt-dlp is the default workhorse for ordinary direct media URLs. Direct
    HLS playlists go through ffmpeg because yt-dlp can save the playlist text
    itself for some provider CDN URLs.
    """
    engine = str(preferred or "auto").strip() or "auto"
    if engine != "auto":
        return engine
    if _is_hls_url(url) and shutil.which("ffmpeg"):
        return "ffmpeg"
    if shutil.which("yt-dlp"):
        return "yt-dlp"
    if shutil.which("aria2c"):
        return "aria2c"
    return "ffmpeg"


def _item_download_preferences(item: DownloadItem) -> tuple[str, str, str]:
    try:
        from media_cli.core.storage import load_config
        cfg = load_config()
    except Exception:
        cfg = {}
    sub_langs = item.download_sub_langs if item.download_sub_langs is not None else cfg.get("download_sub_langs", "multi")
    audio_langs = item.download_audio_langs if item.download_audio_langs is not None else cfg.get("download_audio_langs", "multi")
    subtitle_mode = item.subtitle_mode if item.subtitle_mode is not None else cfg.get("subtitle_mode", "embed")
    return str(sub_langs or "multi"), str(audio_langs or "multi"), str(subtitle_mode or "embed")


def _base_output_path(path: str) -> str:
    return os.path.splitext(os.path.abspath(os.path.expanduser(path)))[0]


class DownloadWorker:
    MIN_CONCURRENT_CAP = 1
    MAX_CONCURRENT_CAP = 4

    def __init__(self, download_dir=TMP_DIR, max_concurrent=1, max_cap=4):
        self.download_dir = download_dir
        self.MAX_CONCURRENT_CAP = max(1, max_cap)
        self.max_concurrent = max(self.MIN_CONCURRENT_CAP, min(self.MAX_CONCURRENT_CAP, max_concurrent))
        self.items: list[DownloadItem] = []
        self.active_items: dict[str, DownloadItem] = {}
        self.lock = threading.RLock()
        self._dismissed_when_idle = False
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._worker_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._worker_thread.start()

    def start(self):
        """Worker thread starts automatically in __init__, this is a compatibility no-op."""
        pass

    def set_max_concurrent(self, slots: int):
        self.set_concurrency(slots)

    def set_concurrency(self, slots: int):
        with self.lock:
            self.max_concurrent = max(self.MIN_CONCURRENT_CAP, min(self.MAX_CONCURRENT_CAP, slots))
            # Immediate reaction: check ALL in-flight items (both DOWNLOADING and RESOLVING)
            active_in_flight = [
                it for it in self.items
                if it.status in (DownloadStatus.DOWNLOADING, DownloadStatus.RESOLVING)
                and it.id in self.active_items
            ]
            active_in_flight.sort(key=lambda it: it.started_at, reverse=True)
            excess = len(active_in_flight) - self.max_concurrent
            if excess > 0:
                for item in active_in_flight[:excess]:
                    if item.process and item.process.poll() is None:
                        try:
                            os.kill(item.process.pid, signal.SIGSTOP)
                            item._proc_paused = True
                        except ProcessLookupError:
                            pass
                    if item.status == DownloadStatus.RESOLVING:
                        if item.proxy_server:
                            _stop_proxy_server(item.proxy_server, item_id=item.id, context="concurrency change")
                            item.proxy_server = None
                        item.url = ""
                    # Excess items become QUEUED (waiting for an available slot)
                    item.status = DownloadStatus.QUEUED
                    item.speed_str = ""
                    item.eta_str = ""
            self._wake_event.set()

    def get_concurrency(self) -> int:
        with self.lock:
            return self.max_concurrent

    def add_item(self, item: DownloadItem) -> DownloadItem:
        with self.lock:
            self._dismissed_when_idle = False
            for existing in self.items:
                if existing.id == item.id or (existing.destination_path and existing.destination_path == item.destination_path):
                    return existing
            self.items.append(item)
            self._wake_event.set()
        return item

    def dismiss_when_idle(self) -> bool:
        with self.lock:
            unfinished = any(
                item.status in (
                    DownloadStatus.DOWNLOADING,
                    DownloadStatus.RESOLVING,
                    DownloadStatus.PAUSED,
                    DownloadStatus.QUEUED,
                )
                for item in self.items
            )
            self._dismissed_when_idle = not unfinished
            return self._dismissed_when_idle

    def get_items(self) -> list[DownloadItem]:
        with self.lock:
            return list(self.items)

    def pause_item(self, item_id: str) -> bool:
        with self.lock:
            for item in self.items:
                if item.id == item_id:
                    if item.status == DownloadStatus.DOWNLOADING and item.process and item.process.poll() is None:
                        try:
                            os.kill(item.process.pid, signal.SIGSTOP)
                            item._proc_paused = True
                        except ProcessLookupError:
                            pass
                    if item.status == DownloadStatus.RESOLVING or (item.process is None and item.resolve_fn):
                        if item.proxy_server:
                            _stop_proxy_server(item.proxy_server, item_id=item.id, context="pause")
                            item.proxy_server = None
                        item.url = ""
                    item.status = DownloadStatus.PAUSED
                    item.speed_str = ""
                    item.eta_str = ""
                    item.logs.append("⏸ Download paused by user.")
                    self._wake_event.set()
                    return True
        return False

    def resume_item(self, item_id: str) -> bool:
        with self.lock:
            for item in self.items:
                if item.id == item_id:
                    if item.status == DownloadStatus.PAUSED:
                        # User unpauses: item transitions to QUEUED (waiting for its turn)
                        if item.process is None and item.resolve_fn:
                            if item.proxy_server:
                                _stop_proxy_server(item.proxy_server, item_id=item.id, context="resume")
                                item.proxy_server = None
                            item.url = ""
                        item.status = DownloadStatus.QUEUED
                        item.speed_str = ""
                        item.eta_str = ""
                        item.logs.append("▶ Download resumed by user.")
                        self._wake_event.set()
                        return True
        return False

    def resume_paused_items(self) -> int:
        resumed = 0
        with self.lock:
            for item in self.items:
                if item.status != DownloadStatus.PAUSED:
                    continue
                if item.process is None and item.resolve_fn:
                    if item.proxy_server:
                        _stop_proxy_server(item.proxy_server, item_id=item.id, context="resume paused before finish")
                        item.proxy_server = None
                    item.url = ""
                item.status = DownloadStatus.QUEUED
                item.speed_str = ""
                item.eta_str = ""
                item.logs.append("▶ Download resumed before quit-after-finish.")
                resumed += 1
            if resumed:
                self._wake_event.set()
        return resumed

    def cancel_item(self, item_id: str) -> bool:
        with self.lock:
            for item in self.items:
                if item.id == item_id:
                    if item.process and item.process.poll() is None:
                        try:
                            if item._proc_paused:
                                os.kill(item.process.pid, signal.SIGCONT)
                            item.process.terminate()
                            try:
                                item.process.wait(timeout=1.0)
                            except subprocess.TimeoutExpired:
                                item.process.kill()
                        except Exception:
                            _logger.warning("Failed to terminate download process for item %s during cancel", item.id, exc_info=True)
                    if item.proxy_server:
                        _stop_proxy_server(item.proxy_server, item_id=item.id, context="cancel")
                        item.proxy_server = None
                    item.status = DownloadStatus.CANCELLED
                    item.logs.append("✖ Download stopped/cancelled.")
                    item.completed_at = time.time()
                    self._wake_event.set()
                    return True
        return False

    def retry_item(self, item_id: str) -> bool:
        with self.lock:
            for item in self.items:
                if item.id == item_id:
                    if item.status in (DownloadStatus.FAILED, DownloadStatus.CANCELLED):
                        item.status = DownloadStatus.QUEUED
                        item.error = ""
                        item.progress = 0.0
                        item.downloaded_bytes = 0
                        item.speed_str = ""
                        item.eta_str = ""
                        item.retry_count = 0
                        item.failed_mirrors.clear()
                        item.current_mirror_name = ""
                        item.current_mirror_url = ""
                        item._proc_paused = False
                        item.logs.append("↺ Manual retry queued by user.")
                        if item.resolve_fn:
                            item.url = ""
                        self._wake_event.set()
                        return True
        return False

    def stop(self):
        self._stop_event.set()
        self._wake_event.set()
        with self.lock:
            for item in list(self.active_items.values()):
                if item.process and item.process.poll() is None:
                    try:
                        if item._proc_paused:
                            os.kill(item.process.pid, signal.SIGCONT)
                        item.process.terminate()
                    except Exception:
                        _logger.warning("Failed to terminate download process for item %s during worker stop", item.id, exc_info=True)
                if item.proxy_server:
                    _stop_proxy_server(item.proxy_server, item_id=item.id, context="worker stop")
                    item.proxy_server = None
        if self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)

    def _run_loop(self):
        while not self._stop_event.is_set():
            with self.lock:
                current_active_count = sum(
                    1 for it in self.items
                    if it.status in (DownloadStatus.DOWNLOADING, DownloadStatus.RESOLVING) and it.id in self.active_items
                )
                available_slots = max(0, self.max_concurrent - current_active_count)

            if available_slots > 0:
                with self.lock:
                    for item in self.items:
                        if available_slots <= 0:
                            break
                        if item.status == DownloadStatus.QUEUED:
                            if item.process and item.process.poll() is None and item._proc_paused:
                                # Suspended item resuming
                                try:
                                    os.kill(item.process.pid, signal.SIGCONT)
                                    item._proc_paused = False
                                    item.status = DownloadStatus.DOWNLOADING
                                    self.active_items[item.id] = item
                                    available_slots -= 1
                                except ProcessLookupError:
                                    item._proc_paused = False
                                    item.process = None
                            elif item.id not in self.active_items:
                                # Fresh queued item starting
                                self.active_items[item.id] = item
                                if not item.url and item.resolve_fn:
                                    item.status = DownloadStatus.RESOLVING
                                else:
                                    item.status = DownloadStatus.DOWNLOADING
                                item.started_at = time.time()
                                available_slots -= 1
                                threading.Thread(target=self._process_item, args=(item,), daemon=True).start()

            self._wake_event.wait(timeout=0.3)
            self._wake_event.clear()

    def _process_item(self, item: DownloadItem):
        try:
            # 1. Pre-check: File existence check
            dest = item.destination_path
            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                if not is_valid_download_video(dest):
                    try:
                        os.remove(dest)
                    except OSError:
                        pass
                else:
                    with self.lock:
                        item.status = DownloadStatus.SKIPPED
                        item.progress = 100.0
                        item.downloaded_bytes = os.path.getsize(dest)
                        item.total_bytes = item.downloaded_bytes
                        item.completed_at = time.time()
                        file_sz = format_bytes(item.downloaded_bytes)
                        item.logs.append(f"✔ Episode already downloaded ({file_sz}).")
                        item.logs.append(f"File: {os.path.basename(dest)}")
                    _notify_complete(item)
                    return
            os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)

            # 2. Lazy resolution if needed
            if not item.url and item.resolve_fn:
                with self.lock:
                    if item.status in (DownloadStatus.PAUSED, DownloadStatus.CANCELLED):
                        return
                    item.status = DownloadStatus.RESOLVING
                    item.logs.append("⚡ Resolving stream URL on the fly…")
                try:
                    url, headers, proxy_srv = item.resolve_fn(item)
                    with self.lock:
                        # Guard: Check if status was changed to PAUSED, QUEUED, or CANCELLED while resolving
                        if item.status in (DownloadStatus.PAUSED, DownloadStatus.QUEUED, DownloadStatus.CANCELLED):
                            if proxy_srv:
                                _stop_proxy_server(proxy_srv, item_id=item.id, context="resolution status change")
                            item.url = ""
                            return

                        # Guard: Check if slot is available right now
                        current_active_downloads = sum(
                            1 for it in self.items
                            if it.status in (DownloadStatus.DOWNLOADING, DownloadStatus.RESOLVING)
                            and it.id in self.active_items
                            and it.id != item.id
                        )
                        if current_active_downloads >= self.max_concurrent:
                            item.status = DownloadStatus.QUEUED
                            item.logs.append("⏸ Slot limit reached, waiting in queue.")
                            if proxy_srv:
                                _stop_proxy_server(proxy_srv, item_id=item.id, context="slot limit")
                            item.url = ""
                            return

                        item.url = url
                        if headers:
                            item.headers.update(headers)
                        item.proxy_server = proxy_srv
                        item.status = DownloadStatus.DOWNLOADING
                        item.logs.append(f"✔ Stream resolved ({url[:45]}…)")
                except Exception as exc:
                    _logger.warning(
                        "Stream resolution failed for download item %s (%s): %s",
                        item.id,
                        item.title,
                        exc,
                    )
                    with self.lock:
                        if item.status in (DownloadStatus.PAUSED, DownloadStatus.CANCELLED, DownloadStatus.QUEUED):
                            return
                        item.status = DownloadStatus.FAILED
                        item.error = f"Stream resolution failed: {exc}"
                        item.logs.append(f"✖ Stream resolution failed: {exc}")
                        item.completed_at = time.time()
                    return

            with self.lock:
                if item.status in (DownloadStatus.PAUSED, DownloadStatus.QUEUED, DownloadStatus.CANCELLED):
                    return
                if not item.url:
                    item.status = DownloadStatus.FAILED
                    item.error = "No stream URL available"
                    item.completed_at = time.time()
                    return
                item.status = DownloadStatus.DOWNLOADING

            # 3. Determine engine
            engine = _select_download_engine(item.engine, item.url)
            _, _, subtitle_mode = _item_download_preferences(item)
            has_separate_audio = bool(item.audio_tracks) or bool(item.audio_url and item.audio_url != item.url)
            if item.engine == "auto" and shutil.which("ffmpeg") and (has_separate_audio or (subtitle_mode == "embed" and item.subtitles)):
                engine = "ffmpeg"
            with self.lock:
                item.logs.append(f"Using {engine} download engine.")

            if engine == "yt-dlp":
                self._download_ytdlp(item)
            elif engine == "aria2c":
                self._download_aria2c(item)
            elif engine == "ffmpeg":
                self._download_ffmpeg(item)
            else:
                raise ValueError(f"Unknown download engine: {engine}")

            if item.status == DownloadStatus.FAILED:
                raise _DownloadAttemptFailed(item.error or f"{engine} download failed")

        except Exception as e:
            if isinstance(e, _DownloadAttemptFailed):
                _logger.warning(
                    "Download attempt failed for item %s (%s): %s",
                    item.id,
                    item.title,
                    e,
                )
            else:
                _logger.exception("Download processing failed for item %s (%s)", item.id, item.title)
            with self.lock:
                if item.status != DownloadStatus.CANCELLED and item.retry_count < item.max_retries:
                    item.retry_count += 1
                    item.status = DownloadStatus.QUEUED
                    item.error = f"Retrying ({item.retry_count}/{item.max_retries}): {e}"
                    item.speed_str = ""
                    item.eta_str = ""
                    item.logs.append(f"↺ Retrying same mirror ({item.retry_count}/{item.max_retries}) after: {e}")
                    if item.resolve_fn:
                        item.url = ""
                elif item.status != DownloadStatus.CANCELLED and item.resolve_fn and _mark_current_mirror_failed(item):
                    item.retry_count = 0
                    item.status = DownloadStatus.QUEUED
                    item.error = f"Trying next mirror after: {e}"
                    item.speed_str = ""
                    item.eta_str = ""
                    item.url = ""
                    item.logs.append("↪ Trying next mirror after repeated failure.")
                else:
                    item.status = DownloadStatus.FAILED
                    item.error = str(e)
                    item.completed_at = time.time()
        finally:
            if item.proxy_server:
                _stop_proxy_server(item.proxy_server, item_id=item.id, context="download cleanup")
                item.proxy_server = None
            with self.lock:
                self.active_items.pop(item.id, None)
                self._wake_event.set()

    def _download_ytdlp(self, item: DownloadItem):
        cmd = [
            "yt-dlp",
            "--newline",
            "--progress-template",
            "%(progress._percent_str)s\t%(progress.downloaded_bytes)s\t%(progress.total_bytes_estimate)s\t%(progress._speed_str)s\t%(progress._eta_str)s",
            "-o", item.destination_path,
            "--force-overwrites",
            "--audio-multistreams",
        ]
        if item.headers:
            for k, v in item.headers.items():
                cmd.extend(["--add-header", f"{k}:{v}"])
        if shutil.which("aria2c") and not _is_hls_url(item.url):
            cmd.extend([
                "--downloader", "aria2c",
                "--downloader-args", "aria2c:-x 16 -s 16 -k 1M",
            ])
        cmd.append(item.url)

        sub_langs, _, subtitle_mode = _item_download_preferences(item)
        if item.subtitles and sub_langs not in ("none", "no", "false", "0"):
            try:
                saved = download_sidecar_subtitles(
                    item.subtitles,
                    _base_output_path(item.destination_path),
                    headers=item.headers,
                    requested_langs=sub_langs,
                    quiet=True,
                )
                if saved:
                    item.logs.append(f"Saved {len(saved)} subtitle sidecar(s).")
                    if subtitle_mode == "embed":
                        item.logs.append("Subtitle embedding requires ffmpeg; saved sidecars with yt-dlp.")
            except Exception as exc:
                item.logs.append(f"⚠ Could not download subtitles: {exc}")

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        with self.lock:
            item.process = proc
            item.logs.append("Starting yt-dlp download engine…")

        last_log_t = 0.0
        try:
            for line in iter(proc.stdout.readline, ""):
                if self._stop_event.is_set() or item.status == DownloadStatus.CANCELLED:
                    break
                parts = line.strip().split("\t")
                if len(parts) >= 4:
                    pct_str, dl_bytes, tot_bytes, speed_str = parts[:4]
                    eta_str = parts[4] if len(parts) > 4 else ""
                    now_t = time.time()
                    with self.lock:
                        try:
                            item.progress = min(99.0, float(pct_str.replace("%", "").strip()))
                        except ValueError:
                            pass
                        item.speed_str = speed_str.strip()
                        item.eta_str = eta_str.strip()
                        item.downloaded_bytes = parse_bytes(dl_bytes)
                        item.total_bytes = parse_bytes(tot_bytes)
                        if now_t - last_log_t >= 1.5 and item.progress > 0:
                            last_log_t = now_t
                            tot_str = format_bytes(item.total_bytes) if item.total_bytes > 0 else format_bytes(item.downloaded_bytes)
                            _append_progress_log(item, f"yt-dlp: {item.progress:.1f}% | {tot_str} | {item.speed_str} | ETA: {item.eta_str}")
            proc.wait()
            with self.lock:
                if item.status != DownloadStatus.CANCELLED:
                    if proc.returncode == 0 and _output_is_hls_playlist(item.destination_path):
                        _discard_partial_output(item.destination_path)
                        item.status = DownloadStatus.FAILED
                        item.error = "yt-dlp saved an HLS playlist instead of a video file"
                        item.logs.append("✖ Download produced playlist text, not video data.")
                        item.completed_at = time.time()
                    elif proc.returncode == 0:
                        item.status = DownloadStatus.COMPLETED
                        item.progress = 100.0
                        item.speed_str = ""
                        item.eta_str = ""
                        item.logs.append("✔ Episode complete.")
                        item.completed_at = time.time()
                        _notify_complete(item)
                    else:
                        item.status = DownloadStatus.FAILED
                        item.error = f"yt-dlp exited with code {proc.returncode}"
                        item.completed_at = time.time()
        finally:
            if proc.poll() is None:
                proc.kill()

    def _download_aria2c(self, item: DownloadItem):
        dest_dir = os.path.dirname(os.path.abspath(item.destination_path))
        dest_file = os.path.basename(item.destination_path)
        cmd = [
            "aria2c",
            "--summary-interval=1",
            "--file-allocation=none",
            f"--dir={dest_dir}",
            f"--out={dest_file}",
            "--allow-overwrite=true",
            "--auto-file-renaming=false",
        ]
        if item.headers:
            for k, v in item.headers.items():
                cmd.append(f"--header={k}: {v}")
        cmd.append(item.url)

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        with self.lock:
            item.process = proc
            item.logs.append("Starting aria2c download engine…")

        last_log_t = 0.0
        try:
            for line in iter(proc.stdout.readline, ""):
                if self._stop_event.is_set() or item.status == DownloadStatus.CANCELLED:
                    break
                m = ARIA2_PATTERN.search(line)
                if m:
                    downloaded_total, pct_str, speed_str, eta_str = m.groups()
                    now_t = time.time()
                    with self.lock:
                        try:
                            item.progress = min(99.0, float(pct_str))
                        except ValueError:
                            pass
                        item.speed_str = speed_str.strip()
                        item.eta_str = eta_str.strip() if eta_str else ""
                        if "/" in downloaded_total:
                            dl_part, tot_part = downloaded_total.split("/", 1)
                            item.downloaded_bytes = parse_bytes(dl_part)
                            item.total_bytes = parse_bytes(tot_part)
                        if now_t - last_log_t >= 1.5:
                            last_log_t = now_t
                            _append_progress_log(item, f"aria2c: {pct_str}% | {downloaded_total} | {item.speed_str} | ETA: {item.eta_str}")
            proc.wait()
            with self.lock:
                if item.status != DownloadStatus.CANCELLED:
                    if proc.returncode == 0:
                        item.status = DownloadStatus.COMPLETED
                        item.progress = 100.0
                        item.speed_str = ""
                        item.eta_str = ""
                        item.logs.append("✔ Episode complete. Embedded MKV remux verified.")
                        item.completed_at = time.time()
                        _notify_complete(item)
                    else:
                        item.status = DownloadStatus.FAILED
                        item.error = f"aria2c exited with code {proc.returncode}"
                        item.completed_at = time.time()
        finally:
            if proc.poll() is None:
                proc.kill()

    def _download_ffmpeg(self, item: DownloadItem):
        partial_path = _partial_output_path(item.destination_path)
        os.makedirs(os.path.dirname(partial_path), exist_ok=True)
        sub_langs, audio_langs, subtitle_mode = _item_download_preferences(item)
        audio_tracks = list(item.audio_tracks or [])
        if not audio_tracks and item.audio_url:
            audio_tracks = [{"url": item.audio_url, "label": "Audio", "language": "und", "default": True}]
        if audio_tracks:
            audio_tracks = filter_tracks_by_language(audio_tracks, audio_langs, is_audio=True)

        subtitles = list(item.subtitles or [])
        if not subtitles and item.subtitle_url:
            subtitles = [{"url": item.subtitle_url, "label": "Subtitles", "language": "und", "default": True}]
        embed_subs = subtitle_mode == "embed" and bool(subtitles) and sub_langs not in ("none", "no", "false", "0")
        downloaded_sidecars: list[str] = []
        if subtitles and sub_langs not in ("none", "no", "false", "0"):
            try:
                downloaded_sidecars = download_sidecar_subtitles(
                    subtitles,
                    _base_output_path(item.destination_path),
                    headers=item.headers,
                    requested_langs=sub_langs,
                    quiet=True,
                )
                if downloaded_sidecars and not embed_subs:
                    item.logs.append(f"Saved {len(downloaded_sidecars)} subtitle sidecar(s).")
            except Exception as exc:
                item.logs.append(f"⚠ Could not download subtitles: {exc}")
                if embed_subs:
                    embed_subs = False

        cmd = [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-nostats",
            "-progress", "pipe:1",
        ]
        if item.headers and not item.proxy_server:
            header_str = "".join(f"{k}: {v}\r\n" for k, v in item.headers.items())
            if header_str:
                cmd.extend(["-headers", header_str])
        cmd.extend(["-i", item.url])

        input_idx = 1
        audio_maps: list[tuple[int, str, str]] = []
        if audio_tracks:
            for track in audio_tracks:
                track_url = track.get("url")
                if not track_url or track_url == item.url:
                    continue
                try:
                    track_url = validate_stream_url(track_url)
                except ValueError:
                    continue
                if item.headers and not item.proxy_server:
                    header_str = "".join(f"{k}: {v}\r\n" for k, v in item.headers.items())
                    if header_str:
                        cmd.extend(["-headers", header_str])
                cmd.extend(["-i", track_url])
                lang = normalize_lang_code(track.get("language") or track.get("label"), target="alpha3")
                title = track.get("label") or "Audio"
                audio_maps.append((input_idx, lang, title))
                input_idx += 1
        elif item.audio_url and item.audio_url != item.url:
            try:
                audio_url = validate_stream_url(item.audio_url)
            except ValueError:
                audio_url = ""
            if audio_url:
                if item.headers and not item.proxy_server:
                    header_str = "".join(f"{k}: {v}\r\n" for k, v in item.headers.items())
                    if header_str:
                        cmd.extend(["-headers", header_str])
                cmd.extend(["-i", audio_url])
                audio_maps.append((input_idx, "und", "Audio"))
                input_idx += 1

        sub_maps: list[tuple[int, str, str]] = []
        if embed_subs:
            for sub_path in downloaded_sidecars:
                cmd.extend(["-i", sub_path])
                stem = os.path.splitext(os.path.basename(sub_path))[0]
                parts = stem.split(".")
                raw_lang = parts[-1].split("-")[0] if len(parts) >= 2 else "und"
                lang = normalize_lang_code(raw_lang, target="alpha3")
                title = raw_lang.upper() if raw_lang != "und" else "Subtitles"
                sub_maps.append((input_idx, lang, title))
                input_idx += 1

        cmd.extend(["-map", "0:v:0"])
        if audio_maps:
            for out_idx, (in_idx, lang, title) in enumerate(audio_maps):
                cmd.extend(["-map", f"{in_idx}:a:0"])
                cmd.extend([f"-metadata:s:a:{out_idx}", f"language={lang}"])
                cmd.extend([f"-metadata:s:a:{out_idx}", f"title={title}"])
        else:
            cmd.extend(["-map", "0:a?"])
        if sub_maps:
            for out_idx, (in_idx, lang, title) in enumerate(sub_maps):
                cmd.extend(["-map", f"{in_idx}:s:0"])
                cmd.extend([f"-metadata:s:s:{out_idx}", f"language={lang}"])
                cmd.extend([f"-metadata:s:s:{out_idx}", f"title={title}"])
        elif embed_subs:
            cmd.extend(["-map", "0:s?"])
        cmd.extend(["-c", "copy", partial_path])

        # Probe duration dynamically for accurate progress & ETA calculation
        duration_sec = probe_stream_duration(item.url, default_duration=1440.0)
        duration_us = int(duration_sec * 1_000_000)

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        with self.lock:
            item.process = proc
            item.logs.append("Starting ffmpeg stream downloader…")

        current_block = {}
        last_time = time.time()
        last_log_t = 0.0
        last_bytes = 0
        speed_smoothed = 0.0

        try:
            for line in iter(proc.stdout.readline, ""):
                if self._stop_event.is_set() or item.status == DownloadStatus.CANCELLED:
                    break
                line_str = line.strip()
                if "=" in line_str:
                    k, v = line_str.split("=", 1)
                    current_block[k.strip()] = v.strip()

                if line_str.startswith("progress="):
                    try:
                        total_size = int(current_block.get("total_size", 0) or 0)
                    except (ValueError, TypeError):
                        total_size = 0

                    try:
                        out_time_us = int(current_block.get("out_time_us", 0) or 0)
                    except (ValueError, TypeError):
                        out_time_us = 0

                    if total_size == 0 and os.path.exists(partial_path):
                        try:
                            total_size = os.path.getsize(partial_path)
                        except OSError:
                            pass

                    now = time.time()
                    dt = now - last_time
                    if dt >= 0.5:
                        db = total_size - last_bytes
                        instant_speed = db / dt if dt > 0 else 0
                        if speed_smoothed == 0:
                            speed_smoothed = instant_speed
                        else:
                            speed_smoothed = 0.7 * speed_smoothed + 0.3 * instant_speed
                        last_bytes = total_size
                        last_time = now

                    with self.lock:
                        item.downloaded_bytes = total_size
                        if speed_smoothed > 0:
                            item.speed_str = f"{format_bytes(speed_smoothed)}/s"
                        else:
                            item.speed_str = ""

                        if duration_us > 0:
                            item.progress = min(99.0, (out_time_us / duration_us) * 100.0)
                            rem_sec = max(0.0, (duration_us - out_time_us) / 1_000_000)
                            speed_str = current_block.get("speed", "0x")
                            try:
                                speed_factor = float(speed_str.replace("x", "").strip())
                            except ValueError:
                                speed_factor = 0.0
                            if speed_factor > 0:
                                eta_sec = int(rem_sec / speed_factor)
                                m, s = divmod(eta_sec, 60)
                                item.eta_str = f"{m:02d}:{s:02d}" if m > 0 else f"{s}s"
                            elif speed_smoothed > 0 and total_size > 0 and item.progress > 0:
                                est_total = total_size / (item.progress / 100.0)
                                rem_bytes = max(0, est_total - total_size)
                                eta_sec = int(rem_bytes / speed_smoothed)
                                m, s = divmod(eta_sec, 60)
                                item.eta_str = f"{m:02d}:{s:02d}" if m > 0 else f"{s}s"

                        if now - last_log_t >= 1.5 and item.progress > 0:
                            last_log_t = now
                            dl_str = format_bytes(item.downloaded_bytes)
                            _append_progress_log(item, f"ffmpeg: {item.progress:.1f}% | {dl_str} | {item.speed_str} | ETA: {item.eta_str}")

                    current_block.clear()

            proc.wait()
            with self.lock:
                if item.status != DownloadStatus.CANCELLED:
                    if proc.returncode == 0:
                        _commit_partial_output(partial_path, item.destination_path)
                        if not is_valid_download_video(item.destination_path):
                            _discard_partial_output(item.destination_path)
                            item.status = DownloadStatus.FAILED
                            item.error = "ffmpeg produced an invalid video file"
                            item.logs.append("✖ Download output was not valid video data.")
                            item.completed_at = time.time()
                            return
                        item.status = DownloadStatus.COMPLETED
                        item.progress = 100.0
                        item.downloaded_bytes = os.path.getsize(item.destination_path)
                        item.total_bytes = item.downloaded_bytes
                        item.speed_str = ""
                        item.eta_str = ""
                        item.logs.append("✔ Episode complete. Stream download verified.")
                        item.completed_at = time.time()
                        _notify_complete(item)
                    else:
                        _discard_partial_output(partial_path)
                        item.status = DownloadStatus.FAILED
                        item.error = f"ffmpeg exited with code {proc.returncode}"
                        item.completed_at = time.time()
        finally:
            if proc.poll() is None:
                proc.kill()
            if item.status != DownloadStatus.COMPLETED:
                _discard_partial_output(partial_path)
            if embed_subs:
                for sub_path in downloaded_sidecars:
                    try:
                        if os.path.exists(sub_path):
                            os.remove(sub_path)
                    except OSError:
                        pass

    def wait_all(self, timeout: float | None = None) -> bool:
        """Blocks until all active and queued items are finished, cancelled, or failed."""
        start_t = time.time()
        while not self._stop_event.is_set():
            with self.lock:
                active_queued = sum(
                    1 for it in self.items
                    if it.status in (DownloadStatus.DOWNLOADING, DownloadStatus.RESOLVING, DownloadStatus.QUEUED)
                )
            if active_queued == 0:
                return True
            if timeout is not None and (time.time() - start_t) >= timeout:
                return False
            time.sleep(0.2)
        return True


# =====================================================================
# GLOBAL WORKER SINGLETON & HELPERS
# =====================================================================

_GLOBAL_DOWNLOAD_WORKER: DownloadWorker | None = None
_GLOBAL_WORKER_LOCK = threading.Lock()


def get_global_worker(download_dir: str = "", max_concurrent: int = 2) -> DownloadWorker:
    """Returns the shared application-wide DownloadWorker instance."""
    global _GLOBAL_DOWNLOAD_WORKER
    with _GLOBAL_WORKER_LOCK:
        if _GLOBAL_DOWNLOAD_WORKER is None:
            if not download_dir:
                try:
                    from ..core.storage import get_default_download_dir, load_config
                    cfg = load_config()
                    download_dir = str(cfg.get("download_dir", "") or "").strip() or get_default_download_dir()
                    max_concurrent = int(cfg.get("download_slots", max_concurrent))
                except Exception:
                    _logger.warning("Failed to load download worker configuration; using fallback download directory", exc_info=True)
                    download_dir = os.path.expanduser("~/Videos/anyplay")
            _GLOBAL_DOWNLOAD_WORKER = DownloadWorker(download_dir, max_concurrent=max_concurrent)
            _GLOBAL_DOWNLOAD_WORKER.start()
            import atexit
            atexit.register(stop_global_worker)
        return _GLOBAL_DOWNLOAD_WORKER


def get_global_worker_if_started() -> DownloadWorker | None:
    """Returns the shared worker only if something already created it."""
    global _GLOBAL_DOWNLOAD_WORKER
    with _GLOBAL_WORKER_LOCK:
        return _GLOBAL_DOWNLOAD_WORKER


def has_tracked_downloads() -> bool:
    """Returns True when the existing global worker has any rows to show."""
    worker = get_global_worker_if_started()
    if worker is None:
        return False
    with worker.lock:
        if worker._dismissed_when_idle:
            return False
        return bool(worker.items)


def has_active_downloads() -> tuple[int, int]:
    """Returns (active_count, queued_count) of currently tracked downloads."""
    global _GLOBAL_DOWNLOAD_WORKER
    if _GLOBAL_DOWNLOAD_WORKER is None:
        return 0, 0
    with _GLOBAL_DOWNLOAD_WORKER.lock:
        active = sum(
            1 for it in _GLOBAL_DOWNLOAD_WORKER.items
            if it.status in (DownloadStatus.DOWNLOADING, DownloadStatus.RESOLVING, DownloadStatus.PAUSED)
        )
        queued = sum(
            1 for it in _GLOBAL_DOWNLOAD_WORKER.items
            if it.status == DownloadStatus.QUEUED
        )
        return active, queued


def stop_global_worker() -> None:
    """Stops the shared download worker and terminates active subprocesses."""
    global _GLOBAL_DOWNLOAD_WORKER
    with _GLOBAL_WORKER_LOCK:
        worker = _GLOBAL_DOWNLOAD_WORKER
        _GLOBAL_DOWNLOAD_WORKER = None
        if worker is not None:
            try:
                worker.stop()
            except Exception:
                _logger.warning("Failed to stop global download worker", exc_info=True)


# =====================================================================
# TEST RUNNER & VERIFICATION
# =====================================================================

def verify_video(filepath):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", filepath]
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.returncode == 0 and bool(res.stdout.strip())


def main():
    print("=" * 65)
    print("TESTING UNIFIED DOWNLOAD WORKER (MULTI-SLOT & LAZY RESOLUTION)")
    print("=" * 65)

    worker = DownloadWorker(TMP_DIR, max_concurrent=2)

    try:
        # TEST 1: Parallel downloads with max_concurrent = 2
        print("\n--- TEST 1: Parallel Downloads (2 Slots Concurrently) ---")
        file1 = os.path.join(TMP_DIR, "parallel_1.mp4")
        file2 = os.path.join(TMP_DIR, "parallel_2.mp4")
        for f in [file1, file2]:
            if os.path.exists(f):
                os.remove(f)

        item1 = DownloadItem(
            id="item-p1",
            title="Parallel Show",
            episode="Episode 1",
            url=TEST_URL,
            destination_path=file1,
            engine="yt-dlp",
        )
        item2 = DownloadItem(
            id="item-p2",
            title="Parallel Show",
            episode="Episode 2",
            url=TEST_URL,
            destination_path=file2,
            engine="aria2c",
        )

        worker.add_item(item1)
        worker.add_item(item2)

        # Wait until BOTH items are actively downloading simultaneously
        print("  Waiting for both items to start downloading concurrently...")
        both_active = False
        for _ in range(40):
            time.sleep(0.2)
            if item1.status == DownloadStatus.DOWNLOADING and item2.status == DownloadStatus.DOWNLOADING:
                both_active = True
                print(f"  ✔ Both items active! Item 1: {item1.progress:.1f}% | Item 2: {item2.progress:.1f}%")
                break

        assert both_active, "Worker failed to run 2 downloads concurrently!"
        print("  ✔ Test 1 passed: Multi-slot concurrency verified!")

        # TEST 2: Lazy Resolution Hook
        print("\n--- TEST 2: Lazy Resolution Hook ---")
        resolved_flag = False

        def mock_resolver(it):
            nonlocal resolved_flag
            resolved_flag = True
            print("  >> [Mock Resolver] Resolving stream URL on the fly...")
            return TEST_URL, {}, None

        file3 = os.path.join(TMP_DIR, "lazy_resolved.mp4")
        if os.path.exists(file3):
            os.remove(file3)

        item3 = DownloadItem(
            id="item-lazy",
            title="Lazy Show",
            episode="Episode 3",
            destination_path=file3,
            resolve_fn=mock_resolver,
        )
        worker.add_item(item3)

        # Cancel item 1 and 2 to clean up queue and verify item 3 triggers resolver
        worker.cancel_item(item1.id)
        worker.cancel_item(item2.id)

        for _ in range(30):
            time.sleep(0.2)
            if resolved_flag:
                break

        assert resolved_flag, "Lazy resolver was not called when item was started!"
        print("  ✔ Test 2 passed: Lazy resolution verified!")

        print("\n" + "=" * 65)
        print("ALL TESTS PASSED SUCCESSFULLY! 🎉")
        print("=" * 65)

    finally:
        worker.stop()
        shutil.rmtree(TMP_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
