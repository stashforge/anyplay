"""Header, method, path, and range rules for local media proxies."""

import re
import urllib.parse
import uuid

PROXY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def proxy_filtered_headers(headers):
    filtered = {}
    for key, value in (headers or {}).items():
        name = str(key or "").strip()
        text = str(value or "")
        if (
            not name
            or name.casefold() in PROXY_HOP_HEADERS
            or name.casefold() in ("host", "content-length")
            or any(ord(char) < 32 for char in name + text)
        ):
            continue
        filtered[name] = text
    return filtered


def proxy_path_authorized(request_path, secret_path):
    return urllib.parse.urlsplit(str(request_path or "")).path == secret_path


def proxy_method_allowed(method):
    return str(method or "").upper() in ("GET", "HEAD")


def proxy_range_header(value):
    value = str(value or "").strip()
    return (
        value
        if value and re.fullmatch(r"bytes=\d*-\d*(?:,\d*-\d*)*", value)
        else ""
    )


def proxy_response_headers(headers):
    connection_tokens = set()
    connection = headers.get("Connection", "") if headers else ""
    for token in str(connection).split(","):
        token = token.strip().casefold()
        if token:
            connection_tokens.add(token)
    blocked = PROXY_HOP_HEADERS | connection_tokens
    return [
        (key, value)
        for key, value in (headers.items() if headers else [])
        if str(key).casefold() not in blocked
    ]


def new_proxy_secret_path(extension="mp4", title="stream"):
    extension = str(extension or "mp4").strip().lower().lstrip(".")
    if not re.fullmatch(r"[a-z0-9]{1,8}", extension):
        extension = "mp4"

    # Keep it filename-safe but preserve spaces for players to display nicely
    safe_title = re.sub(r"[^\w\-. ]+", "", str(title or "stream")).strip()
    safe_title = re.sub(r" {2,}", " ", safe_title)
    safe_title = safe_title[:100] or "stream"
    safe_title = urllib.parse.quote(safe_title)

    return f"/{uuid.uuid4().hex}/{safe_title}.{extension}"
