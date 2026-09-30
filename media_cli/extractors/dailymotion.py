"""Dailymotion native stream extractor."""

from __future__ import annotations

import json
import re
from typing import Any

from .base import BaseExtractor


class DailymotionExtractor(BaseExtractor):
    """Extractor for Dailymotion video embeds and links."""

    name = "Dailymotion"
    domains = [
        "dailymotion.com",
        "dai.ly",
    ]
    patterns = [
        re.compile(r"dailymotion\.com/(?:embed/)?video/([a-zA-Z0-9]+)", re.IGNORECASE),
        re.compile(r"dai\.ly/([a-zA-Z0-9]+)", re.IGNORECASE),
        re.compile(r"geo\.dailymotion\.com/player\.html\?video=([a-zA-Z0-9]+)", re.IGNORECASE),
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
        if not video_id:
            return []

        embed_url = f"https://geo.dailymotion.com/player.html?video={video_id}"
        req_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": "https://www.dailymotion.com/",
        }
        if headers:
            req_headers.update(headers)

        html = self.fetch_page(embed_url, headers=req_headers, referer="https://www.dailymotion.com/")
        if not html:
            html = self.fetch_page(f"https://www.dailymotion.com/embed/video/{video_id}", headers=req_headers, referer="https://www.dailymotion.com/")

        ts = ""
        v1st = ""
        if html:
            ts_m = re.search(r'"ts"\s*:\s*(\d+)', html)
            v1st_m = re.search(r'"v1st"\s*:\s*"([^"]+)"', html)
            if ts_m:
                ts = ts_m.group(1)
            if v1st_m:
                v1st = v1st_m.group(1)

        meta_url = f"https://www.dailymotion.com/player/metadata/video/{video_id}?locale=en-US&dmV1st={v1st}&dmTs={ts}&is_native_app=0"
        meta_json_str = self.fetch_page(meta_url, headers=req_headers, referer="https://www.dailymotion.com/")
        if not meta_json_str:
            return []

        try:
            meta = json.loads(meta_json_str)
        except Exception:
            return []

        if meta.get("error"):
            return []

        qualities = meta.get("qualities", {})
        auto_streams = qualities.get("auto", [])
        if not auto_streams:
            return []

        master_url = auto_streams[0].get("url")
        if not master_url:
            return []

        extracted_subs = list(subtitles or [])
        sub_data = meta.get("subtitles", {}).get("data", {})
        if isinstance(sub_data, dict):
            for code, sub_info in sub_data.items():
                if isinstance(sub_info, dict) and sub_info.get("urls"):
                    urls = sub_info.get("urls")
                    if urls:
                        extracted_subs.append({
                            "label": sub_info.get("label", code),
                            "url": urls[0],
                            "default": "en" in code.lower(),
                        })

        stream_name = name or self.name
        return self.extract_m3u8(
            master_url,
            referer="https://www.dailymotion.com/",
            origin="https://www.dailymotion.com",
            name=stream_name,
            priority=priority,
            subtitles=extracted_subs,
            headers={"User-Agent": req_headers["User-Agent"]},
        )
