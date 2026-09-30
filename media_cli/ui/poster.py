"""Asynchronous cover cache and poster rendering coordination."""

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time

from ..core.terminal import display_width, fit_terminal_line, truncate_display
from .covers import (
    chafa_cover_command,
    enforce_cache_limits,
    fetch_cover_bytes,
)


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

    def footer_line(self, show, default_text, width):
        reset = "\033[0m"
        hint = "\033[38;5;244m"
        max_width = max(1, int(width) - 1)

        def trim(value):
            return truncate_display(value, max_width)

        def split_replaced_footer(value):
            if "\u2502" in default_text:
                _, right = default_text.split("\u2502", 1)
                return value, f"  \u2502{right}"
            if default_text:
                return value, f"  \u2502  {default_text}"
            return value, ""

        def status_line(value, color, loading=False):
            left, right = split_replaced_footer(value)
            if loading:
                left = f"{self.loading_frame()} {left}"
            right_width = max(0, max_width - display_width(left))
            right = truncate_display(right, right_width)
            return fit_terminal_line(f"{color}{left}{reset}{hint}{right}{reset}", width)

        default_line = fit_terminal_line(f"{hint}{trim(default_text)}{reset}", width)
        if not self.enabled() or not show:
            return default_line
        clean_default = re.sub(r'\033\[[0-9;]*m', '', str(default_text or "")).strip()
        if clean_default.startswith('*') or clean_default.startswith('✓') or clean_default.startswith('✔'):
            return default_line
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
            return default_line
        if status == "loading":
            if status_time and time.time() - status_time > 4:
                return default_line
            return status_line("Loading cover", "\033[36m", loading=True)
        if status_time and time.time() - status_time > 3:
            return default_line
        if status == "failed":
            return status_line("Cover failed", "\033[38;5;203m")
        if status == "missing":
            return status_line("Cover unavailable", "\033[38;5;214m")
        if status == "no_chafa":
            return status_line("Cover needs chafa", "\033[38;5;214m")
        return default_line

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
        time.sleep(0.15)
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
