"""StreamWish and VidHide native stream extractor."""

from __future__ import annotations

import json
import re
import urllib.parse

from bs4 import BeautifulSoup

from .base import BaseExtractor
from .unpack import JsUnpacker


class StreamWishExtractor(BaseExtractor):
    """Extractor for StreamWish, VidHide, Fastream, and affiliated video hosts."""

    name = "StreamWish"
    domains = [
        "streamwish.to",
        "streamwish.com",
        "awish.pro",
        "fastream.to",
        "streamhide.to",
        "seekplayer.com",
        "flaswish.com",
        "wishfast.top",
        "hlswish.com",
        "swhoi.com",
        "swdyu.com",
        "dwish.pro",
        "embedwish.com",
        "mwish.pro",
        "asnwish.com",
        "strwish.com",
        "sfastwish.com",
        "wishonly.com",
        "ajmidm.com",
        "vidhide.com",
        "vidhidepro.com",
        "vidhidepre.com",
        "filelions.to",
        "filelions.site",
        "filelions.online",
        "seekplayer.vip",
        "seekplayer.to",
        "playwish.com",
        "dwish.net",
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

        for script_tag in soup.find_all("script"):
            data = script_tag.string or script_tag.text or ""
            if not data:
                continue

            if "eval(function(p,a,c" in data or "eval(function(p, a, c" in data:
                unpacked = JsUnpacker.unpack_and_combine(data)
            else:
                unpacked = data

            if "m3u8" in unpacked or "source" in unpacked or "file" in unpacked:
                # 1. file: "https://...m3u8"
                m = re.search(r'file\s*:\s*["\']([^"\']+\.m3u8[^"\']*)["\']', unpacked)
                if m:
                    master_url = m.group(1)

                # 2. sources: [{file: "..."}]
                if not master_url:
                    m = re.search(r'source[s]?\s*:\s*\[\s*\{[^}]*?file\s*:\s*["\']([^"\']+)["\']', unpacked)
                    if m:
                        master_url = m.group(1)

                # Extract embedded subtitles if present
                sub_match = re.search(r'tracks\s*:\s*(\[[^\]]+\])', unpacked)
                if sub_match:
                    try:
                        raw_tracks = json.loads(sub_match.group(1))
                        for t in raw_tracks:
                            if isinstance(t, dict) and t.get("kind") in ("captions", "subtitles"):
                                sub_file = t.get("file")
                                if sub_file:
                                    extracted_subs.append({
                                        "label": t.get("label", "Subtitles"),
                                        "url": urllib.parse.urljoin(url, sub_file),
                                        "default": bool(t.get("default")),
                                    })
                    except Exception:
                        pass

            if master_url:
                break

        if not master_url:
            return []

        if master_url.startswith("//"):
            master_url = f"https:{master_url}"

        stream_name = name or self.name
        return self.extract_m3u8(
            master_url,
            referer=referer,
            origin=origin,
            name=stream_name,
            priority=priority,
            subtitles=extracted_subs,
        )
