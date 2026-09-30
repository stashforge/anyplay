"""Bounded API decoding, cache scoping, and search error messages."""

import hashlib
import json
import socket
import urllib.error

from .terminal import sanitize_terminal_text

MAX_API_JSON_BYTES = 8 * 1024 * 1024


class SearchFailure(RuntimeError):
    pass


class ProviderDependencyError(RuntimeError):
    pass


def read_limited_response(response, max_bytes=MAX_API_JSON_BYTES):
    try:
        max_bytes = max(1, int(max_bytes))
    except (TypeError, ValueError):
        raise ValueError("Invalid response size limit")
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise ValueError("API response is too large")
        except ValueError as exc:
            if str(exc) == "API response is too large":
                raise
    data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("API response is too large")
    return data


def read_json_response(response, max_bytes=MAX_API_JSON_BYTES):
    return json.loads(read_limited_response(response, max_bytes))


def search_failure_message(source, exc):
    source = sanitize_terminal_text(source or "Search")
    if type(exc).__name__ == "TMDBError":
        return str(exc)
    if isinstance(exc, urllib.error.HTTPError):
        import http.client
        reason = getattr(exc, "reason", None) or http.client.responses.get(exc.code, "")
        reason_suffix = f": {reason}" if reason else ""
        if exc.code in (401, 403):
            return f"{source} authentication or access was rejected (HTTP {exc.code}{reason_suffix})."
        if exc.code == 429:
            return f"{source} rate limit reached (HTTP 429{reason_suffix})."
        if 500 <= exc.code <= 599:
            return f"{source} service is temporarily unavailable (HTTP {exc.code}{reason_suffix})."
        return f"{source} request failed (HTTP {exc.code}{reason_suffix})."
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return f"{source} request timed out."
    if isinstance(exc, urllib.error.URLError):
        reason_suffix = f": {reason}" if reason else ""
        return f"Could not connect to {source}{reason_suffix}."
    if isinstance(exc, (json.JSONDecodeError, KeyError, TypeError)):
        return f"{source} returned an invalid response."
    return f"{source} search failed."


def anilist_account_cache_key(token):
    token = str(token or "").strip()
    if not token:
        return "anonymous"
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"token:{digest}"
