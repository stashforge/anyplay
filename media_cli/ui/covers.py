"""Cover download validation and terminal rendering helpers."""

import os
import re
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


def chafa_cover_command(path):
    return [
        "chafa",
        "--probe=off",
        "--relative=on",
        "--animate=off",
        f"--size={POSTER_WIDTH}x{POSTER_HEIGHT}",
        "--stretch",
        "--colors=full",
        path,
    ]


def poster_uses_native_protocol(raw):
    raw = str(raw or "")
    return any(
        marker in raw
        for marker in ("\033_G", "\033Pq", "\033]1337;File=")
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
    except Exception:
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
