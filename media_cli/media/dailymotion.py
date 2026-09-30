"""Dailymotion stream helpers."""

from __future__ import annotations

import re
import urllib.parse
from typing import Any


def is_dailymotion_url(url: str) -> bool:
    host = urllib.parse.urlparse(str(url or "")).netloc.casefold()
    return host == "dai.ly" or host.endswith("dailymotion.com")


def stream_type_from_url(url: str) -> str:
    path = urllib.parse.urlparse(str(url or "")).path.casefold()
    return "hls" if ".m3u8" in path else "mp4"


def dailymotion_video_id(url: str) -> str:
    parsed = urllib.parse.urlparse(str(url or ""))
    if parsed.netloc.casefold() == "geo.dailymotion.com":
        return urllib.parse.parse_qs(parsed.query).get("video", [""])[0]
    match = re.search(r"/(?:embed/video|video)/([^/?#]+)", parsed.path)
    if match:
        return match.group(1)
    if parsed.netloc.casefold() == "dai.ly":
        return parsed.path.strip("/").split("/", 1)[0]
    return ""


def select_dailymotion_av_pair(formats: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    videos = []
    audios = []
    for item in formats or []:
        url = str(item.get("url") or "")
        if not url:
            continue
        acodec = str(item.get("acodec") or "")
        vcodec = str(item.get("vcodec") or "")
        if vcodec != "none" and acodec == "none":
            videos.append(item)
        elif acodec != "none" and vcodec == "none":
            audios.append(item)

    def video_score(item):
        return int(item.get("height") or 0), int(item.get("tbr") or item.get("vbr") or 0)

    def audio_score(item):
        return int(item.get("abr") or item.get("tbr") or 0)

    video = max(videos, key=video_score) if videos else None
    audio = max(audios, key=audio_score) if audios else None
    return video, audio


def build_dailymotion_hls_manifest(video_url: str, audio_url: str, *, width: int = 1280, height: int = 720, bandwidth: int = 2_400_000) -> str:
    return (
        "#EXTM3U\n"
        f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="Audio",DEFAULT=YES,URI="{audio_url}"\n'
        f'#EXT-X-STREAM-INF:BANDWIDTH={int(bandwidth)},RESOLUTION={int(width)}x{int(height)},AUDIO="audio"\n'
        f"{video_url}\n"
    )
