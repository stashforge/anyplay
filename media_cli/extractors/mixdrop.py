"""MixDrop native stream extractor."""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from .base import BaseExtractor
from .unpack import JsUnpacker


class MixDropExtractor(BaseExtractor):
    """Extractor for MixDrop video hosts."""

    name = "MixDrop"
    domains = [
        "mixdrop.co",
        "mixdrop.to",
        "mixdrop.sx",
        "mixdrop.bz",
        "mixdrop.ch",
        "mixdrop.ag",
        "mixdrop.gl",
        "mixdrop.club",
    ]
    patterns = [
        re.compile(r"mixdrop\.[a-z]+/(?:[ef])/([a-zA-Z0-9]+)", re.IGNORECASE),
    ]

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
        # Ensure embed URL (/e/ instead of /f/)
        embed_url = re.sub(r"/f/([a-zA-Z0-9]+)", r"/e/\1", url)
        parsed = urllib.parse.urlparse(embed_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        ref = referer or f"{origin}/"

        req_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": ref,
        }
        if headers:
            req_headers.update(headers)

        html = self.fetch_page(embed_url, headers=req_headers, referer=ref)
        if not html:
            return []

        unpacked = JsUnpacker.unpack_and_combine(html)
        if not unpacked:
            unpacked = html

        wurl_match = re.search(r'(?:MDCore\.)?wurl\s*=\s*["\']([^"\']+)["\']', unpacked)
        if not wurl_match:
            return []

        wurl = wurl_match.group(1)
        if wurl.startswith("//"):
            wurl = f"https:{wurl}"

        extracted_subs = list(subtitles or [])
        sub_match = re.search(r'(?:MDCore\.)?remotesub\s*=\s*["\']([^"\']+)["\']', unpacked)
        if sub_match:
            sub_url = sub_match.group(1)
            if sub_url.startswith("//"):
                sub_url = f"https:{sub_url}"
            extracted_subs.append({
                "label": "English",
                "url": sub_url,
                "default": True,
            })

        prefix = name or self.name
        stream_type = "hls" if ".m3u8" in wurl else "mp4"

        stream_dict = {
            "source_name": f"{prefix} (Adaptive)",
            "link": wurl,
            "type": stream_type,
            "resolution": "Adaptive",
            "referer": ref,
            "headers": req_headers,
            "source_priority": priority,
            "android_safe": stream_type == "mp4",
            "_quality_rank": 800,
        }
        if extracted_subs:
            stream_dict["subtitles"] = extracted_subs
            def_sub = next((s["url"] for s in extracted_subs if s.get("default")), extracted_subs[0]["url"])
            stream_dict["subtitle_url"] = def_sub

        return [stream_dict]
