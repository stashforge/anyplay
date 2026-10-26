"""Helpers for providers that expand HLS masters before resolver time."""

from __future__ import annotations

import re
from urllib.parse import urljoin
from typing import Any


_PASSTHROUGH_KEYS = (
    "ip_version",
    "audio_url",
    "audio_tracks",
    "split_audio_url",
    "split_video_url",
    "split_width",
    "split_height",
    "split_bandwidth",
    "subtitles",
    "subtitle_url",
    "dailymotion_audio",
    "dailymotion_video",
    "dailymotion_width",
    "dailymotion_height",
    "dailymotion_bandwidth",
)


def resolved_hls_streams_to_sources(streams: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for stream in streams or []:
        link = stream.get("link") or stream.get("streamUrl")
        if not link:
            continue
        source = {
            "sourceName": stream.get("source_name") or stream.get("sourceName") or "HLS",
            "link": link,
            "type": stream.get("type") or "hls",
            "resolution": stream.get("resolution") or "Adaptive",
            "headers": dict(stream.get("headers") or {}),
            "referer": stream.get("referer") or "",
            "priority": stream.get("source_priority", stream.get("priority", 1)),
            "_source_kind": "direct",
        }
        if "android_safe" in stream:
            source["android_safe"] = bool(stream.get("android_safe"))
        for key in _PASSTHROUGH_KEYS:
            if key in stream:
                source[key] = stream[key]
        sources.append(source)
    return sources


def parse_master_playlist_variants(master_url: str, body: str) -> list[dict[str, Any]]:
    """Return HLS variants from a master playlist, resolving relative URLs."""
    variants: list[dict[str, Any]] = []
    if not body or "#EXT-X-STREAM-INF" not in body:
        return variants

    lines = [line.strip() for line in body.splitlines() if line.strip()]
    pending_info = ""
    for line in lines:
        if line.startswith("#EXT-X-STREAM-INF:"):
            pending_info = line
            continue
        if not pending_info or line.startswith("#"):
            continue

        width = height = None
        res_match = re.search(r"RESOLUTION=(\d+)x(\d+)", pending_info, re.IGNORECASE)
        if res_match:
            width = int(res_match.group(1))
            height = int(res_match.group(2))
        bandwidth = None
        bw_match = re.search(r"(?:AVERAGE-)?BANDWIDTH=(\d+)", pending_info, re.IGNORECASE)
        if bw_match:
            bandwidth = int(bw_match.group(1))

        resolution = f"{height}p" if height else "auto"
        variants.append(
            {
                "url": urljoin(master_url, line),
                "resolution": resolution,
                "width": width,
                "height": height,
                "bandwidth": bandwidth,
            }
        )
        pending_info = ""

    return variants
