"""VOE native stream extractor."""

from __future__ import annotations

import base64
import json
import re
import urllib.parse

from bs4 import BeautifulSoup

from .base import BaseExtractor


class VoeExtractor(BaseExtractor):
    """Extractor for VOE video host."""

    name = "VOE"
    domains = [
        "voe.sx",
        "voe-network.net",
        "audaciousdefaulthouse.com",
        "metaphoricallyclattering.com",
        "tastelessparliament.com",
        "fraudulentmoulding.com",
    ]

    def extract(
        self,
        url: str,
        *,
        name: str = "",
        priority: int = 2,
        subtitles: list[dict] | None = None,
    ) -> list[dict]:
        html = self.fetch_page(url, referer=url)
        if not html:
            return []

        # Check for redirect page (VOE sometimes renders window.location.href = '...')
        redirect_match = re.search(r"window\.location\.href\s*=\s*['\"]([^'\"]+)['\"]", html)
        if redirect_match and redirect_match.group(1).startswith("http"):
            redirect_url = redirect_match.group(1)
            html = self.fetch_page(redirect_url, referer=url)

        soup = BeautifulSoup(html, "html.parser")
        stream_url = ""

        for script_tag in soup.find_all("script"):
            data = script_tag.string or script_tag.text or ""
            if not data:
                continue

            # Layout 1: sources = { 'hls': '...' }
            if "sources" in data and ("hls" in data or "mp4" in data):
                m = re.search(r"['\"](?:hls|mp4)['\"]\s*:\s*['\"]([^'\"]+)['\"]", data)
                if m:
                    candidate = m.group(1)
                    if candidate.startswith("http"):
                        stream_url = candidate
                    else:
                        try:
                            stream_url = base64.b64decode(candidate).decode("utf-8")
                        except Exception:
                            pass

            # Layout 2: wc0 or base64 JSON payload
            if not stream_url and "wc0" in data:
                m = re.search(r"['\"]([A-Za-z0-9+/=]{30,})['\"]", data)
                if m:
                    try:
                        decoded = base64.b64decode(m.group(1)).decode("utf-8")
                        if "file" in decoded:
                            data_json = json.loads(decoded)
                            stream_url = data_json.get("file", "")
                    except Exception:
                        pass

            if stream_url:
                break

        if not stream_url:
            return []

        parsed = urllib.parse.urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        stream_name = name or self.name

        if ".m3u8" in stream_url:
            return self.extract_m3u8(
                stream_url,
                referer=f"{origin}/",
                origin=origin,
                name=stream_name,
                priority=priority,
                subtitles=subtitles,
            )

        return [{
            "source_name": f"{stream_name} (Adaptive)",
            "link": stream_url,
            "type": "mp4",
            "resolution": "Adaptive",
            "referer": f"{origin}/",
            "headers": {"Referer": f"{origin}/"},
            "source_priority": priority,
            "android_safe": True,
            "subtitles": subtitles or [],
            "_quality_rank": 800,
            "_bitrate": 0,
        }]
