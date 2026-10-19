"""Asynchronous cover cache and poster rendering coordination."""

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import replace

from ..core.terminal import sanitize_terminal_text
from .actions import ActionBindings
from .background_loading import BackgroundLoadingStatus
from .covers import (
    chafa_cover_command,
    enforce_cache_limits,
    fetch_cover_bytes,
)
from .footer import Footer, FooterStatus, StatusKind, render_footer, render_legacy_footer, render_legacy_overlay
from .loading_model import LoadingInteraction, LoadingOwner, LoadingPlacement, LoadingTaskSpec

COVER_DOWNLOAD_DWELL_SECONDS = 0.25


class PosterManager:
    def __init__(
            self,
            *,
            enabled,
            cache_dir,
            read_cache_dirs=None,
            hovered_show_id,
            request_redraw,
            loading_frame):
        self.enabled = enabled
        self.cache_dir = cache_dir
        self.read_cache_dirs = read_cache_dirs or (lambda: [cache_dir()])
        self.hovered_show_id = hovered_show_id
        self.request_redraw = request_redraw
        self.loading_frame = loading_frame
        self.poster_lock = threading.Lock()
        self.download_lock = threading.Lock()
        self.active_downloads = set()
        self._raw_cache = {}
        self._cover_loading = None

    def _mark_download(self, url_hash):
        with self.download_lock:
            if url_hash in self.active_downloads:
                return False
            self.active_downloads.add(url_hash)
            return True

    def _unmark_download(self, url_hash):
        with self.download_lock:
            self.active_downloads.discard(url_hash)

    def clear_downloads(self):
        with self.download_lock:
            self.active_downloads.clear()

    def set_status(self, show, status):
        with self.poster_lock:
            if show.get("_poster_status") != status:
                show["_poster_status"] = status
                show["_poster_status_time"] = time.time()

    def footer_line(self, show, default_text, width, *, persistent=False):
        if isinstance(default_text, Footer):
            status = self.footer_status(show, persistent=persistent) if default_text.include_cover_status else None
            if status is not None and not any(existing.message == status.message for existing in default_text.statuses):
                default_text = replace(default_text, statuses=default_text.statuses + (status,))
            return render_footer(default_text, width)

        # Remaining string callers are a compatibility boundary only.
        default_line = render_legacy_footer(default_text, width)
        if not self.enabled() or not show:
            return default_line
        clean_default = re.sub(r'\033\[[0-9;]*m', '', str(default_text or "")).strip()
        if clean_default.startswith('*') or clean_default.startswith('✓') or clean_default.startswith('✔'):
            return default_line
        status = self.footer_status(show, persistent=persistent)
        if status is None:
            return default_line
        navigation = default_text.split("\u2502", 1)[-1].strip()
        return render_legacy_overlay(status, navigation, width)

    def _cover_loading_status(self, show, active):
        identity = (self._get_cover_url(show), show.get("_poster_status_time")) if active else None
        previous = self._cover_loading
        if previous is not None and (
            not active or previous[0] is not show or previous[1] != identity
        ):
            previous[2].read(False)
            self._cover_loading = None
        if not active:
            return None
        if self._cover_loading is None:
            spec = LoadingTaskSpec(
                "cover-download-render", LoadingOwner("poster-cover", 0), "Loading cover",
                LoadingInteraction.BACKGROUND, LoadingPlacement.FOOTER,
                ActionBindings(()), delay=0,
            )
            self._cover_loading = (
                show, identity, BackgroundLoadingStatus(spec, None, frame=self.loading_frame),
            )
        return self._cover_loading[2].read(True)

    def footer_status(self, show, *, persistent=False):
        """Expose cover state as semantic status, never as pre-colored text."""
        if not self.enabled() or not show:
            self._cover_loading_status(None, False)
            return None
        with self.poster_lock:
            status = show.get("_poster_status")
            status_time = float(show.get("_poster_status_time", 0) or 0)
            has_raw = bool(show.get("_poster_raw"))
            if not has_raw:
                url = self._get_cover_url(show)
                if url:
                    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
                    if url_hash in self._raw_cache:
                        has_raw = True
                        show["_poster_raw"] = self._raw_cache[url_hash]
                        show["_poster_status"] = "ready"
                        status = "ready"

        if has_raw or status == "ready":
            self._cover_loading_status(None, False)
            return None
        detail = sanitize_terminal_text(show.get("_image_message"))

        def described(label):
            return f"{label}: {detail}" if detail else label

        if status == "loading":
            if not persistent and status_time and time.time() - status_time > 4:
                self._cover_loading_status(None, False)
                return None
            loading = self._cover_loading_status(show, True)
            return replace(loading, message=described("Loading cover")) if loading is not None else None
        self._cover_loading_status(None, False)
        if not persistent and status_time and time.time() - status_time > 3:
            return None
        if status == "failed":
            return FooterStatus(described("Cover failed"), StatusKind.ERROR)
        if status == "missing":
            return FooterStatus(described("Cover unavailable"), StatusKind.WARNING)
        if status == "no_chafa":
            return FooterStatus(described("Cover needs chafa"), StatusKind.WARNING)
        return None

    def needs_tick(self, show):
        if not self.enabled() or not show:
            return False
        with self.poster_lock:
            return show.get("_poster_status") == "loading"

    @staticmethod
    def _get_cover_url(show):
        url = show.get("thumbnail") or show.get("image") or show.get("cover")
        if not url or not isinstance(url, str):
            return ""
        url = url.strip()
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith("mcovers/") or url.startswith("a_tbs/"):
            return f"https://wp.youtube-anime.com/aln.youtube-anime.com/{url}"
        return url

    def get(self, show):
        if not self.enabled():
            return None
        url = self._get_cover_url(show)
        if not url:
            self.set_status(show, "missing")
            return ""
        if not shutil.which("chafa"):
            self.set_status(show, "no_chafa")
            return ""

        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]

        with self.poster_lock:
            cached_raw = self._raw_cache.get(url_hash)
            if cached_raw is not None:
                show["_poster_raw"] = cached_raw
                show["_poster_status"] = "ready"
                show["_poster_status_time"] = time.time()
                return cached_raw

            if not show.get("_poster_failed"):
                raw = show.get("_poster_raw")
                if raw is not None:
                    self._raw_cache[url_hash] = raw
                    show["_poster_status"] = "ready"
                    show["_poster_status_time"] = time.time()
                    return raw
            failed = show.get("_poster_failed")
        if failed:
            self.set_status(show, "failed")
            return ""

        # Check if the image is already cached on disk; render asynchronously to avoid UI navigation stutter
        found_path = None
        for r_dir in self.read_cache_dirs():
            p = os.path.join(r_dir, f"{url_hash}.jpg")
            if os.path.exists(p):
                found_path = p
                break

        if found_path:
            self.set_status(show, "loading")
            if self._mark_download(url_hash):
                threading.Thread(
                    target=self._render_chafa_async,
                    args=(show, found_path, url_hash),
                    daemon=True,
                ).start()
            return ""

        write_dir = self.cache_dir()
        cached_path = os.path.join(write_dir, f"{url_hash}.jpg")
        self.set_status(show, "loading")
        if self._mark_download(url_hash):
            threading.Thread(
                target=self._download,
                args=(show, url, url_hash, write_dir, cached_path),
                daemon=True,
            ).start()
        return ""

    def _render_chafa_async(self, show, path, url_hash):
        time.sleep(0.06)
        hovered = self.hovered_show_id()
        target_id = show.get("_id") or show.get("id") or show.get("title") or show.get("name")
        if hovered and target_id and str(hovered) != str(target_id):
            self._unmark_download(url_hash)
            return

        try:
            process = subprocess.run(
                chafa_cover_command(path),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if process.returncode == 0 and process.stdout.strip():
                raw = process.stdout.rstrip("\n")
                with self.poster_lock:
                    self._raw_cache[url_hash] = raw
                    show["_poster_raw"] = raw
                    show["_poster_status"] = "ready"
                    show["_poster_status_time"] = time.time()
                hovered = self.hovered_show_id()
                target_id = show.get("_id") or show.get("id") or show.get("title") or show.get("name")
                if not hovered or not target_id or str(hovered) == str(target_id):
                    self.request_redraw()
        except Exception:
            pass
        finally:
            self._unmark_download(url_hash)

    def prewarm_shows(self, shows):
        """Asynchronously pre-renders covers for a list of shows into RAM in the background."""
        if not self.enabled() or not shows:
            return

        def _worker():
            for show in shows:
                if not isinstance(show, dict):
                    continue
                url = self._get_cover_url(show)
                if not url:
                    continue
                url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
                with self.poster_lock:
                    if url_hash in self._raw_cache:
                        continue
                found_path = None
                for r_dir in self.read_cache_dirs():
                    p = os.path.join(r_dir, f"{url_hash}.jpg")
                    if os.path.exists(p):
                        found_path = p
                        break
                if found_path:
                    try:
                        process = subprocess.run(
                            chafa_cover_command(found_path),
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        if process.returncode == 0 and process.stdout.strip():
                            raw = process.stdout.rstrip("\n")
                            with self.poster_lock:
                                self._raw_cache[url_hash] = raw
                                show["_poster_raw"] = raw
                                show["_poster_status"] = "ready"
                    except Exception:
                        pass

        threading.Thread(target=_worker, daemon=True).start()

    def _download(self, show, url, url_hash, cache_dir, cached_path):
        time.sleep(COVER_DOWNLOAD_DWELL_SECONDS)
        hovered = self.hovered_show_id()
        target_id = show.get("_id") or show.get("id") or show.get("title") or show.get("name")
        if hovered and target_id and str(hovered) != str(target_id):
            self._unmark_download(url_hash)
            return

        # Check if jpg already exists in any read cache dir
        existing_jpg = None
        for r_dir in self.read_cache_dirs():
            p = os.path.join(r_dir, f"{url_hash}.jpg")
            if os.path.exists(p):
                existing_jpg = p
                break

        temporary_path = None
        try:
            render_src_jpg = existing_jpg
            if not render_src_jpg:
                os.makedirs(cache_dir, exist_ok=True)
                image_data = fetch_cover_bytes(url)
                descriptor, temporary_path = tempfile.mkstemp(
                    suffix=".jpg",
                    prefix="allmanga_tmp_",
                )
                with os.fdopen(descriptor, "wb") as temporary:
                    temporary.write(image_data)

                if shutil.which("magick"):
                    converted = subprocess.run(
                        [
                            "magick",
                            temporary_path,
                            "-resize",
                            "400x600",
                            cached_path,
                        ],
                        capture_output=True,
                        timeout=10,
                    )
                    if converted.returncode != 0:
                        raise RuntimeError("ImageMagick could not decode cover")
                elif shutil.which("ffmpeg"):
                    converted = subprocess.run(
                        [
                            "ffmpeg",
                            "-y",
                            "-i",
                            temporary_path,
                            "-vf",
                            "scale=400:-1",
                            cached_path,
                        ],
                        capture_output=True,
                        timeout=10,
                    )
                    if converted.returncode != 0:
                        raise RuntimeError("ffmpeg could not decode cover")
                else:
                    shutil.copy2(temporary_path, cached_path)

                render_src_jpg = cached_path

            enforce_cache_limits(cache_dir)
            process = subprocess.run(
                chafa_cover_command(render_src_jpg),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if process.returncode == 0 and process.stdout.strip():
                raw = process.stdout.rstrip("\n")
                with self.poster_lock:
                    self._raw_cache[url_hash] = raw
                    show["_poster_raw"] = raw
                    show["_poster_status"] = "ready"
                    show["_poster_status_time"] = time.time()
            else:
                with self.poster_lock:
                    show["_poster_failed"] = True
                self.set_status(show, "failed")
            self.request_redraw()
        except Exception:
            with self.poster_lock:
                show["_poster_failed"] = True
            self.set_status(show, "failed")
            self.request_redraw()
        finally:
            if temporary_path:
                try:
                    os.remove(temporary_path)
                except Exception:
                    pass
            self._unmark_download(url_hash)
