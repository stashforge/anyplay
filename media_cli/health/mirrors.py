"""Summarize extraction output without publishing media URLs or credentials."""

from urllib.parse import urlsplit


def concrete_entries(entries):
    accepted, seen = [], set()
    unresolved = 0
    for entry in entries:
        if not isinstance(entry, dict):
            unresolved += 1
            continue
        kind = str(entry.get("type") or "").lower()
        url = entry.get("streamUrl") or entry.get("sourceUrl") or entry.get("link") or ""
        try:
            parsed = urlsplit(url)
            host = parsed.hostname
        except (ValueError, TypeError):
            host = None
        if kind not in ("hls", "mp4", "dash") or not host or parsed.scheme not in ("http", "https"):
            unresolved += 1
            continue
        identity = (url, entry.get("audio_url"), str(entry.get("resolution") or "auto"))
        if identity in seen:
            continue
        seen.add(identity)
        accepted.append(entry)
    return accepted, unresolved


def summarize_mirrors(entries):
    from ..domain.health_report import _safe_label

    accepted, unresolved = concrete_entries(entries)
    mirrors = []
    for entry in accepted:
        url = entry.get("streamUrl") or entry.get("sourceUrl") or entry.get("link")
        host = urlsplit(url).hostname
        kind = str(entry.get("type") or "").lower()
        subtitles = entry.get("subtitles")
        mirrors.append({"resolution": str(entry.get("resolution") or "auto"), "type": kind,
                        "host": host, "separate_audio": bool(entry.get("audio_url") or any(isinstance(t, dict) and t.get("url") for t in entry.get("audio_tracks") or [])),
                        "subtitle_count": len(subtitles) if isinstance(subtitles, list) else 0,
                        "headers_present": bool(entry.get("headers")),
                        **({"channel": _safe_label(entry['channel'])} if isinstance(entry.get('channel'), str) else {}),
                        **({"source": _safe_label(entry['sourceName'])} if isinstance(entry.get('sourceName'), str) else {})})
    return {"status": "ok", "mirrors": mirrors, "unresolved_count": unresolved}
