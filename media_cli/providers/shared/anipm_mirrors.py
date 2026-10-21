"""Enumerate AniPM channels and parse their real HLS variants locally."""

import logging
from copy import deepcopy
from urllib.parse import urlencode, urljoin

from ...media.sources import quality_from_dimensions
from .anipm_sources import _https_url

log = logging.getLogger(__name__)
_CHANNELS = {"sub": ("sub", "Sub"), "subhard": ("subHard", "Hard Sub"),
             "dub": ("dub", "Dub"), "dubhard": ("dubHard", "Hard Dub")}


def _variants(source: dict, fetch_playlist) -> list[dict]:
    import m3u8

    url = source["streamUrl"]
    text = fetch_playlist(url, extra_hdrs=source.get("headers") or {})
    if not text.lstrip().startswith("#EXTM3U"):
        return []
    master = m3u8.loads(text, uri=url)
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
    for priority, channel in enumerate(ordered):
        flag, label = _CHANNELS[channel]
        if channel != language and availability.get(flag) is not True:
            continue
        try:
            payload = base if channel == language else resolve_channel(channel)
            rows = (payload or {}).get("episode", {}).get("sourceUrls", [])
            for original in rows:
                source = deepcopy(original)
                source.update(sourceName=f"AniPM [{label}]", channel=channel, priority=priority,
                              allow_ytdlp_expansion=False)
                try:
                    variants = _variants(source, fetch_playlist)
                except Exception as exc:
                    log.warning("AniPM %s playlist expansion failed (%s)", channel, type(exc).__name__)
                    variants = []
                sources.extend(variants or [source])
        except Exception as exc:
            log.warning("AniPM %s channel unavailable (%s)", channel, type(exc).__name__)
    result = deepcopy(base)
    result["episode"]["sourceUrls"] = sources
    return result
