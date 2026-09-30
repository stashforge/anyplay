"""Provider source-entry builders.

Providers return source entries, not player-ready streams.  A source entry is
either a direct stream URL that can be passed through by the resolver, or a raw
embed/page URL that must be extracted before playback.
"""

from __future__ import annotations

from typing import Any


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def build_direct_source(
    *,
    name: Any,
    stream_url: Any,
    stream_type: str,
    resolution: Any = "Adaptive",
    referer: Any = "",
    headers: dict | None = None,
    android_safe: bool | None = None,
) -> dict:
    source = {
        "sourceName": _clean_text(name) or "Direct",
        "link": _clean_text(stream_url),
        "type": _clean_text(stream_type) or "mp4",
        "resolution": _clean_text(resolution) or "Adaptive",
        "referer": _clean_text(referer),
        "headers": dict(headers or {}),
        "_source_kind": "direct",
    }
    if android_safe is not None:
        source["android_safe"] = bool(android_safe)
    return source


def build_embed_source(
    *,
    name: Any,
    source_url: Any,
    resolution: Any = "Adaptive",
    referer: Any = "",
    extract_headers: dict | None = None,
    playback_headers: dict | None = None,
) -> dict:
    return {
        "sourceName": _clean_text(name) or "Embed",
        "sourceUrl": _clean_text(source_url),
        "type": "embed",
        "resolution": _clean_text(resolution) or "Adaptive",
        "referer": _clean_text(referer),
        "extractHeaders": dict(extract_headers or {}),
        "headers": dict(playback_headers or {}),
        "_source_kind": "embed",
    }
