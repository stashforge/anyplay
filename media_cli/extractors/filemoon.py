"""Filemoon native stream extractor."""

from __future__ import annotations

import json
import re
import urllib.parse

from bs4 import BeautifulSoup

from .base import BaseExtractor
from .unpack import JsUnpacker


class FilemoonExtractor(BaseExtractor):
    """Extractor for Filemoon and MoonPlayer hosts."""

    name = "Filemoon"
    domains = [
        "filemoon.sx",
        "filemoon.to",
        "filemoon.in",
        "filemoon.top",
        "filemoon.lat",
        "filemoon.art",
        "moonplayer.xyz",
        "mooncloud.top",
    ]

    def extract(
        self,
        url: str,
        *,
        name: str = "",
        priority: int = 2,
        subtitles: list[dict] | None = None,
    ) -> list[dict]:
        parsed = urllib.parse.urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        referer = f"{origin}/"

        html = self.fetch_page(url, referer=url, headers={"Origin": origin})
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        master_url = ""
        extracted_subs = list(subtitles or [])
        unpacked_code = ""

        for script_tag in soup.find_all("script"):
            data = script_tag.string or script_tag.text or ""
            if not data:
                continue

            if "eval" in data and ("m3u8" in data or "file" in data or "sources" in data):
                unpacked = JsUnpacker.unpack_and_combine(data)
                unpacked_code = unpacked

                m = re.search(r'\{file\s*:\s*["\']([^"\']+)["\']\}', unpacked)
                if m:
                    master_url = m.group(1)

                if not master_url:
                    m = re.search(r'file\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']', unpacked)
                    if m:
                        master_url = m.group(1)

            if master_url:
                break

        if not master_url:
            return []

        if master_url.startswith("//"):
            master_url = f"https:{master_url}"

        # Check for subtitles in sub.info query param or fetch(...) in unpacked script
        query_params = urllib.parse.parse_qs(parsed.query)
        sub_url = query_params.get("sub.info", [None])[0]
        if not sub_url and unpacked_code:
            fetch_m = re.search(r"fetch\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", unpacked_code)
            if fetch_m:
                sub_url = fetch_m.group(1)

        if sub_url:
            try:
                sub_data = self.fetch_page(sub_url, referer=url, headers={"Origin": origin}, timeout=6)
                if sub_data:
                    sub_json = json.loads(sub_data)
                    if isinstance(sub_json, list):
                        for s in sub_json:
                            if isinstance(s, dict) and s.get("file"):
                                extracted_subs.append({
                                    "label": s.get("label", "Subtitles"),
                                    "url": s["file"],
                                    "default": "eng" in str(s.get("label", "")).lower(),
                                })
            except Exception:
                pass

        stream_name = name or self.name
        return self.extract_m3u8(
            master_url,
            referer=referer,
            origin=origin,
            name=stream_name,
            priority=priority,
            subtitles=extracted_subs,
        )
