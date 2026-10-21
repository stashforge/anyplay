"""AniPM's short-lived gateway/resolver handshake, without session caching."""

from __future__ import annotations

import json
import math
import urllib.request
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit

from ...media.languages import normalize_lang_code
from ...media.provider_skips import normalize_provider_skips
from ...services.http import SSL_CTX_SECURE, UA

BASE_URL = "https://ani.pm"
RESOLVER_URL = "https://embed.settlar.io"


class _StaleSession(Exception):
    """A fresh embed nonce is required; carries no credential-bearing detail."""


def headers(origin: str, accept: str = "application/json") -> dict[str, str]:
    return {"User-Agent": UA, "Origin": origin, "Referer": f"{origin}/", "Accept": accept}


def request_html(url: str, *, extra_hdrs: dict) -> str:
    request = urllib.request.Request(url, headers=extra_hdrs)
    with urllib.request.urlopen(request, context=SSL_CTX_SECURE, timeout=8) as response:
        return response.read().decode("utf-8")


class _ConfigParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.active = dict(attrs).get("id") == "anipm-embed-config"

    def handle_endtag(self, tag):
        if tag == "script":
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


def _fresh(payload: dict, now: float) -> bool:
    expiry = payload.get("expiresAt")
    if not isinstance(expiry, (int, float)) or isinstance(expiry, bool) or not math.isfinite(expiry):
        return False
    if expiry <= now + 5:
        raise _StaleSession
    return True


def _session_json(fetch_json, url: str, origin: str):
    try:
        payload = fetch_json(url, extra_hdrs=headers(origin))
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise _StaleSession from None
        raise
    error = payload.get("error") if isinstance(payload, dict) else None
    code = error.get("code") if isinstance(error, dict) else error
    if isinstance(code, str) and code in {
        "invalid-nonce", "expired-nonce", "invalid-token", "expired-token", "session-expired",
    }:
        raise _StaleSession
    return payload


def _https_url(value) -> bool:
    if not isinstance(value, str) or any(ord(char) <= 32 for char in value):
        return False
    parsed = urlsplit(value)
    return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password


def resolve(identity: str, episode: str, language: str, *, fetch_html, fetch_json, clock, hardsub=False):
    for _attempt in range(2):
        try:
            return _resolve_once(identity, episode, language, fetch_html=fetch_html,
                                 fetch_json=fetch_json, clock=clock, hardsub=hardsub)
        except _StaleSession:
            continue
    return None


def _resolve_once(identity: str, episode: str, language: str, *, fetch_html, fetch_json, clock, hardsub=False):
    path = f"{quote(identity, safe='')}/{quote(episode, safe='')}/{language}"
    suffix = "?hardsub=1" if hardsub else ""
    html = fetch_html(f"{BASE_URL}/embed/ani/{path}{suffix}", extra_hdrs=headers(BASE_URL, "text/html"))
    parser = _ConfigParser()
    parser.feed(html)
    config = json.loads("".join(parser.parts))
    nonce = config.get("nonce") if isinstance(config, dict) else None
    if not isinstance(nonce, str) or not nonce.strip():
        return None
    query = urlencode({"n": nonce, "id": identity, "ep": episode, "lang": language, "hardsub": int(hardsub)})
    gateway = _session_json(fetch_json, f"{BASE_URL}/api/partner/v1/session?{query}", BASE_URL)
    if not isinstance(gateway, dict) or not _fresh(gateway, clock()):
        return None
    token = gateway.get("token")
    if not isinstance(token, str) or not token.strip():
        return None
    query = urlencode({"t": token})
    resolved = _session_json(fetch_json, f"{RESOLVER_URL}/api/embed/session?{query}", RESOLVER_URL)
    if not isinstance(resolved, dict) or not _fresh(resolved, clock()) or not _fresh(gateway, clock()):
        return None
    if not _https_url(resolved.get("source")) or resolved.get("kind") != "hls":
        return None
    subtitles = []
    rows = resolved.get("subtitles")
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and _https_url(row.get("url")):
            subtitles.append({"url": row["url"], "label": row.get("label") or "",
                              "lang": normalize_lang_code(row.get("srclang") or row.get("label")),
                              "default": row.get("default") is True})
    selected = next((row for row in subtitles if row["default"]), None)
    if selected is None:
        selected = next((row for row in subtitles if row["lang"] == "en"), None)
    if selected is None and subtitles:
        selected = subtitles[0]
    for row in subtitles:
        row["default"] = row is selected
    source = {
        "sourceName": "AniPM (Auto)", "streamUrl": resolved["source"],
        "type": "hls", "resolution": "auto",
        "requires_proxy": True, "allow_ytdlp_expansion": False,
        "referer": f"{RESOLVER_URL}/", "headers": headers(RESOLVER_URL, "*/*"),
        "subtitles": subtitles,
        "subtitle_url": selected["url"] if selected else "",
        "expiresAt": min(gateway["expiresAt"], resolved["expiresAt"]),
        "skip_intervals": normalize_provider_skips(gateway.get("skip")),
    }
    return {"episode": {"sourceUrls": [source]}}
