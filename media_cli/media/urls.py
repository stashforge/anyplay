"""Validation for externally supplied HTTP media URLs."""

import urllib.parse


def validate_http_url(url):
    if not isinstance(url, str) or any(ord(char) < 32 for char in url):
        raise ValueError("Invalid URL")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise ValueError("Only HTTP(S) URLs are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URLs with embedded credentials are not allowed")
    return url


def validate_stream_url(url):
    """Accept only credential-free HTTP(S) media and extractor destinations, or absolute local file paths."""
    import os
    if isinstance(url, str) and os.path.isabs(url):
        return url
    return validate_http_url(url)


def validate_optional_referer(referer):
    if not referer:
        return ""
    return validate_http_url(referer)


def is_supported_image(data):
    if data.startswith(b"\xff\xd8\xff"):
        return True
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return True
    if data.startswith((b"GIF87a", b"GIF89a")):
        return True
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return True
    return (
        len(data) >= 12
        and data[4:8] == b"ftyp"
        and data[8:12] in (b"avif", b"avis")
    )


def provider_frontend_domain(cfg=None):
    from ..providers import ALLANIME
    return ALLANIME.browser_url("", cfg=cfg)


def provider_episode_url(show_id, episode, ttype="sub", cfg=None):
    from ..providers import ALLANIME
    show_id = str(show_id or "").strip()
    episode = str(episode or "").strip()
    if not show_id or not episode:
        return ""
    return ALLANIME.browser_url(show_id, episode, ttype, cfg)


allanime_frontend_domain = provider_frontend_domain
allanime_episode_url = provider_episode_url


def open_external_url(url):
    import os
    import shutil
    import subprocess
    import sys
    try:
        url = validate_http_url(url)
    except ValueError:
        return False
    is_termux = (os.environ.get("PREFIX", "").startswith("/data/data/com.termux")
                 or os.path.exists("/data/data/com.termux"))
    if is_termux:
        opener = shutil.which("termux-open-url") or shutil.which("termux-open")
        command = (
            [opener, url]
            if opener else
            ["am", "start", "-a", "android.intent.action.VIEW", "-d", url]
        )
    elif sys.platform == "darwin":
        command = ["open", url]
    elif os.name == "nt":
        command = ["cmd", "/c", "start", "", url]
    else:
        opener = shutil.which("xdg-open")
        command = [opener, url] if opener else []
    if not command:
        return False
    try:
        subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception:
        return False


def redact_sensitive_text(content):
    import re
    text = str(content)
    text = re.sub(
        r"(?i)(Authorization\s*:\s*Bearer\s+)[^\s'\"]+",
        r"\1<redacted>",
        text,
    )
    text = re.sub(
        r"(https?://[^\s'\"<>?]+)\?[^\s'\"<>]+",
        r"\1?<redacted>",
        text,
    )
    text = re.sub(
        r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b",
        "<redacted-jwt>",
        text,
    )
    return text

