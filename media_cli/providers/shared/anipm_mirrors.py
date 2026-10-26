"""Enumerate AniPM channels and parse their real HLS variants locally."""

import logging
from copy import deepcopy
from urllib.parse import urlencode, urljoin

from ...media.sources import quality_from_dimensions
from ...services.requirements import dependency_hint_messages, optional_dependency_hint, optional_python
from .anipm_sources import _https_url

log = logging.getLogger(__name__)
_CHANNELS = {"sub": ("sub", "Sub"), "subhard": ("subHard", "Hard Sub"),
             "dub": ("dub", "Dub"), "dubhard": ("dubHard", "Hard Dub")}


def _variants(source: dict, fetch_playlist, parser) -> list[dict]:
    url = source["streamUrl"]
    text = fetch_playlist(url, extra_hdrs=source.get("headers") or {})
    if not text.lstrip().startswith("#EXTM3U"):
        return []
    master = parser.loads(text, uri=url)
    variants = []
    seen = set()
    for playlist in master.playlists:
        info = playlist.stream_info
        width, height = info.resolution or (0, 0)
        target = urljoin(url, playlist.uri)
        if not height or not _https_url(target):
            continue
        external_audio = any(track.type == "AUDIO" and track.uri and track.group_id == info.audio
                             for track in master.media)
        if external_audio:
            # Existing proxy filtering retains the master audio group rather
            # than handing mpv a silent, video-only variant.
            target = url + ("&" if "?" in url else "?") + urlencode({"hls_res": height})
        if target in seen:
            continue
        seen.add(target)
        label, _ = quality_from_dimensions(width, height)
        variant = deepcopy(source)
        variant.update(streamUrl=target, resolution=label, bandwidth=info.bandwidth or 0,
                       sourceName=f"{source['sourceName']} {label}")
        variants.append((height, info.bandwidth or 0, variant))
    variants.sort(key=lambda row: row[:2], reverse=True)
    return [row[2] for row in variants]


def collect_mirrors(base: dict, language: str, availability: dict, *, resolve_channel, fetch_playlist) -> dict:
    ordered = [language, language + "hard"]
    sources = []
    parser_checked = False
    parser = None
    for priority, channel in enumerate(ordered):
        flag, label = _CHANNELS[channel]
        if channel != language and availability.get(flag) is not True:
            continue
        try:
            payload = base if channel == language else resolve_channel(channel)
            rows = (payload or {}).get("episode", {}).get("sourceUrls", [])
            for original in rows:
                if not parser_checked:
                    parser = optional_python("m3u8")
                    parser_checked = True
                source = deepcopy(original)
                source.update(sourceName=f"AniPM [{label}]", channel=channel, priority=priority,
                              allow_ytdlp_expansion=False)
                try:
                    variants = _variants(source, fetch_playlist, parser) if parser is not None else []
                except Exception as exc:
                    log.warning("AniPM %s playlist expansion failed (%s)", channel, type(exc).__name__)
                    variants = []
                sources.extend(variants or [source])
        except Exception as exc:
            log.warning("AniPM %s channel unavailable (%s)", channel, type(exc).__name__)
    result = deepcopy(base)
    result["episode"]["sourceUrls"] = sources
    parser_hint = optional_dependency_hint("m3u8", fallback="Adaptive playback", feature="individual qualities")
    hints = [hint for hint in dependency_hint_messages(base) if hint != parser_hint]
    if sources and parser_checked and parser is None:
        hints.insert(0, parser_hint)
    if hints:
        result["dependency_hints"] = hints
    else:
        result.pop("dependency_hints", None)
    return result
