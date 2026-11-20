"""Cover download validation and terminal rendering helpers."""

import os
import re
import subprocess
import shutil
import tempfile
import threading
from collections import OrderedDict
import urllib.error
import urllib.request

from ..core.terminal import fit_terminal_line
from ..media.urls import is_supported_image, validate_http_url
from ..services.http import SSL_CTX_SECURE

MAX_COVER_BYTES = 8 * 1024 * 1024
POSTER_WIDTH = 12
POSTER_HEIGHT = 8


def enforce_cache_limits(
        cache_dir,
        *,
        max_bytes=100 * 1024 * 1024,
        target_bytes=80 * 1024 * 1024):
    try:
        files = []
        total_size = 0
        for name in os.listdir(cache_dir):
            path = os.path.join(cache_dir, name)
            if os.path.isfile(path):
                stat = os.stat(path)
                files.append((stat.st_atime, path, stat.st_size))
                total_size += stat.st_size
        if total_size <= max_bytes:
            return
        files.sort(key=lambda item: item[0])
        for _, path, size in files:
            try:
                os.remove(path)
                total_size -= size
                if total_size <= target_bytes:
                    break
            except Exception:
                pass
    except Exception:
        pass


def chafa_cover_command(path, *, width=POSTER_WIDTH, height=POSTER_HEIGHT):
    return [
        "chafa",
        "--probe=off",
        "--relative=on",
        "--animate=off",
        f"--size={width}x{height}",
        "--stretch",
        "--colors=full",
        path,
    ]


_compact_covers = {}
_info_cover_shape = "portrait"
INFO_COVER_SIZES = {"portrait": (8, 5), "square": (10, 5)}


def configure_info_cover(shape):
    global _info_cover_shape
    shape = str(shape).strip().lower()
    _info_cover_shape = shape if shape in INFO_COVER_SIZES else "portrait"


def info_cover_size():
    return INFO_COVER_SIZES[_info_cover_shape]


def _render_compact(path, *, timeout, shape=None):
    shape = shape or _info_cover_shape
    width, height = INFO_COVER_SIZES[shape]
    command = chafa_cover_command(path, width=width, height=height)
    if shape == "portrait":
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    # Crop cached image bytes in the worker; no extra cover download is needed.
    with tempfile.TemporaryDirectory(prefix="anyplay-cover-") as directory:
        cropped = os.path.join(directory, "crop.png")
        pixels = width * 50
        pixel_height = height * 100
        if shutil.which("magick"):
            crop_command = ["magick", path, "-resize", f"{pixels}x{pixel_height}^", "-gravity", "center",
                            "-extent", f"{pixels}x{pixel_height}", cropped]
        elif shutil.which("ffmpeg"):
            crop_command = ["ffmpeg", "-v", "error", "-y", "-i", path, "-vf",
                            f"scale={pixels}:{pixel_height}:force_original_aspect_ratio=increase,crop={pixels}:{pixel_height}",
                            "-frames:v", "1", cropped]
        else:
            crop_command = None
        if crop_command:
            crop = subprocess.run(crop_command, capture_output=True, timeout=timeout)
            if crop.returncode == 0:
                command[-1] = cropped
            else:
                command.remove("--stretch")
        else:
            command.remove("--stretch")
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


_rendered_covers = OrderedDict()
_rendered_cover_lock = threading.Lock()


def render_cover_variants(path, *, timeout=5):
    """Render both layouts in the existing cover worker, never on UI navigation."""
    key = None
    try:
        stat = os.stat(path)
        key = (os.path.abspath(path), stat.st_mtime_ns, stat.st_size, _info_cover_shape,
               os.environ.get('TERM'), os.environ.get('TERM_PROGRAM'))
        with _rendered_cover_lock:
            if key in _rendered_covers:
                result, compact = _rendered_covers[key]
                _rendered_covers.move_to_end(key)
                _compact_covers[result.stdout.rstrip('\n')] = compact
                return result
    except OSError:
        pass
    result = subprocess.run(chafa_cover_command(path), capture_output=True, text=True, timeout=timeout)
    if result.returncode == 0 and result.stdout.strip():
        raw = result.stdout.rstrip("\n")
        try:
            compact = _render_compact(path, timeout=timeout)
            if compact.returncode == 0 and compact.stdout.strip():
                _compact_covers[raw] = compact.stdout.rstrip("\n")
                if key is not None:
                    with _rendered_cover_lock:
                        _rendered_covers[key] = (result, compact.stdout.rstrip('\n'))
                        while len(_rendered_covers) > 32:
                            _rendered_covers.popitem(last=False)
                while len(_compact_covers) > 128:
                    _compact_covers.pop(next(iter(_compact_covers)))
        except (OSError, subprocess.SubprocessError):
            pass
    return result


def compact_cover(raw):
    return _compact_covers.get(raw, "")


_placeholder_renders = {}
_placeholder_pending = set()
_placeholder_lock = threading.Lock()


def placeholder_cover():
    """Render the bundled asset once per shape/session, off the UI thread."""
    shape = _info_cover_shape
    with _placeholder_lock:
        if shape in _placeholder_renders:
            return _placeholder_renders[shape]
        if shape in _placeholder_pending:
            return ""
        _placeholder_pending.add(shape)
    def render():
        raw = ""
        try:
            result = _render_compact(os.path.join(os.path.dirname(__file__), 'assets', 'no-cover.png'),
                                     timeout=5, shape=shape)
            if result.returncode == 0:
                raw = result.stdout.rstrip('\n')
        except (OSError, subprocess.SubprocessError):
            pass
        finally:
            with _placeholder_lock:
                _placeholder_renders[shape] = raw
                _placeholder_pending.discard(shape)
    threading.Thread(target=render, daemon=True).start()
    return ""


def poster_uses_native_protocol(raw):
    raw = str(raw or "")
    # SIXEL's DCS introducer may include parameters (e.g. Chafa's 0;1;0).
    return bool(re.search(r"\x1bP[0-?]*q", raw)) or any(
        marker in raw
        for marker in ("\033_G", "\033]1337;File=")
    )


def poster_symbol_lines(raw, height, columns):
    if not raw or poster_uses_native_protocol(raw):
        return []
    text = str(raw).replace("\x1bD", "\n")
    text = re.sub(r"\033\[\d+D", "", text)
    return [
        fit_terminal_line(line, columns) + "\033[0m"
        for line in text.splitlines()[:height]
    ]


def fetch_cover_bytes(url, max_bytes=MAX_COVER_BYTES):
    validate_http_url(url)
    parsed = urllib.parse.urlparse(url)
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    if parsed.scheme and parsed.netloc:
        headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
    request = urllib.request.Request(
        url,
        headers=headers,
    )
    try:
        resp_ctx = urllib.request.urlopen(
            request,
            context=SSL_CTX_SECURE,
            timeout=10,
        )
    except Exception as error:
        if isinstance(error, urllib.error.HTTPError) and error.code in {403, 429, 503}:
            from ..services.browser_access.integration import fetch_cached_image
            image = fetch_cached_image(url, max_bytes=max_bytes)
            if image is not None:
                return image
        # If a Jetpack i*.wp.com CDN URL fails or times out, try the direct host URL as fallback
        if parsed.netloc.endswith(".wp.com") and "/" in parsed.path.lstrip("/"):
            direct_url = f"https://{parsed.path.lstrip('/')}"
            validate_http_url(direct_url)
            direct_parsed = urllib.parse.urlparse(direct_url)
            headers["Referer"] = f"{direct_parsed.scheme}://{direct_parsed.netloc}/"
            req_direct = urllib.request.Request(direct_url, headers=headers)
            resp_ctx = urllib.request.urlopen(req_direct, context=SSL_CTX_SECURE, timeout=10)
        else:
            raise

    with resp_ctx as response:
        final_url = response.geturl() if hasattr(response, "geturl") else url
        validate_http_url(final_url)
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    raise ValueError("Cover image is too large")
            except ValueError as exc:
                if str(exc) == "Cover image is too large":
                    raise

        chunks = []
        total = 0
        while True:
            chunk = response.read(min(65536, max_bytes - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("Cover image is too large")
            chunks.append(chunk)

    data = b"".join(chunks)
    if not is_supported_image(data):
        raise ValueError("Unsupported cover image format")
    return data
