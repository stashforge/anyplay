"""Ok.ru (Odnoklassniki) native stream extractor."""

from __future__ import annotations

import html
import json
import re
from typing import Any

from .base import BaseExtractor

QUALITY_MAP = {
    "ultra": ("2160p", 2160),
    "quad": ("1440p", 1440),
    "full": ("1080p", 1080),
    "hd": ("720p", 720),
    "sd": ("480p", 480),
    "low": ("360p", 360),
    "lowest": ("240p", 240),
    "mobile": ("144p", 144),
}


class OkruExtractor(BaseExtractor):
    """Extractor for Ok.ru (Odnoklassniki) video embeds and links."""

    name = "Ok.ru"
    domains = [
        "ok.ru",
        "odnoklassniki.ru",
    ]
    patterns = [
        re.compile(r"(?:ok\.ru|odnoklassniki\.ru)/(?:video|videoembed)/(\d+)", re.IGNORECASE),
    ]

    def _extract_id(self, url: str) -> str | None:
        for pattern in self.patterns:
            m = pattern.search(url)
            if m:
                return m.group(1)
        return None

    def extract(
        self,
        url: str,
        *,
        name: str = "",
        priority: int = 2,
        subtitles: list[dict] | None = None,
        headers: dict[str, str] | None = None,
        referer: str | None = None,
        **kwargs: Any,
    ) -> list[dict]:
        video_id = self._extract_id(url)
        target_url = f"https://ok.ru/videoembed/{video_id}" if video_id else url

        req_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://ok.ru/",
        }
        if headers:
            req_headers.update(headers)

        html_text = self.fetch_page(target_url, headers=req_headers, referer="https://ok.ru/")
        if not html_text:
            return []

        m = re.search(r'data-options=(["\'])(.*?)\1', html_text)
        if not m:
            return []

        try:
            raw = html.unescape(m.group(2))
            data = json.loads(raw)
            flashvars = data.get("flashvars", {})
            metadata_raw = flashvars.get("metadata")
            if isinstance(metadata_raw, str):
                metadata = json.loads(metadata_raw)
            elif isinstance(metadata_raw, dict):
                metadata = metadata_raw
            else:
                return []
        except Exception:
            return []

        videos = metadata.get("videos", [])
        hls_url = metadata.get("hlsManifestUrl")
        prefix = name or self.name
        streams: list[dict] = []

        if hls_url:
            hls_streams = self.extract_m3u8(
                hls_url,
                referer="https://ok.ru/",
                origin="https://ok.ru",
                name=prefix,
                priority=priority,
                subtitles=subtitles,
                headers=req_headers,
            )
            if hls_streams:
                streams.extend(hls_streams)

        for v in videos:
            v_name = (v.get("name") or "").lower()
            v_url = v.get("url")
            if not v_url:
                continue

            quality_str, rank = QUALITY_MAP.get(v_name, (f"{v_name}p" if v_name.isdigit() else "Adaptive", 500))
            stream_dict = {
                "source_name": f"{prefix} ({quality_str})",
                "source_parent_name": prefix,
                "link": v_url,
                "type": "mp4",
                "resolution": quality_str,
                "referer": "https://ok.ru/",
                "headers": req_headers,
                "source_priority": priority,
                "android_safe": True,
                "_quality_rank": rank,
            }
            if subtitles:
                stream_dict["subtitles"] = subtitles
                def_sub = next((s["url"] for s in subtitles if s.get("default")), subtitles[0]["url"])
                stream_dict["subtitle_url"] = def_sub
            streams.append(stream_dict)

        streams.sort(key=lambda s: s.get("_quality_rank", 0), reverse=True)
        return streams
