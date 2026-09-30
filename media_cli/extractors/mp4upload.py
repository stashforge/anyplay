"""Mp4Upload native stream extractor."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .base import BaseExtractor
from .unpack import JsUnpacker


class Mp4UploadExtractor(BaseExtractor):
    """Extractor for Mp4Upload."""

    name = "Mp4Upload"
    domains = [
        "mp4upload.com",
    ]

    REFERER = "https://mp4upload.com/"

    def extract(
        self,
        url: str,
        *,
        name: str = "",
        priority: int = 2,
        subtitles: list[dict] | None = None,
    ) -> list[dict]:
        # Embed URL format is typically https://www.mp4upload.com/embed-xxx.html
        embed_url = url
        if "/embed-" not in url and ".html" not in url:
            file_id = url.split("/")[-1]
            if file_id:
                embed_url = f"https://www.mp4upload.com/embed-{file_id}.html"

        html = self.fetch_page(embed_url, referer=self.REFERER)
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        video_url = ""
        resolution = "Adaptive"

        for script_tag in soup.find_all("script"):
            data = script_tag.string or script_tag.text or ""
            if not data:
                continue

            if "eval(function(p,a,c" in data or "eval(function(p, a, c" in data:
                unpacked = JsUnpacker.unpack_and_combine(data)
            else:
                unpacked = data

            if "player.src" in unpacked or "src:" in unpacked or ".mp4" in unpacked:
                # 1. player.src({src: "...", ...}) or .src("...")
                m = re.search(r'src\s*:\s*["\']([^"\']+\.mp4[^"\']*)["\']', unpacked)
                if not m:
                    m = re.search(r'\.src\(\s*["\']([^"\']+\.mp4[^"\']*)["\']', unpacked)
                if not m:
                    m = re.search(r'src\s*:\s*["\']([^"\']+)["\']', unpacked)

                if m:
                    video_url = m.group(1).replace(r"\/", "/")

                # Look for HEIGHT=(\d+)
                res_match = re.search(r"\WHEIGHT=(\d+)", unpacked)
                if res_match:
                    resolution = f"{res_match.group(1)}p"

            if video_url:
                break

        if not video_url:
            return []

        rank = int(resolution.replace("p", "")) if resolution.replace("p", "").isdigit() else 720
        stream_name = f"{name or self.name} ({resolution})"

        return [{
            "source_name": stream_name,
            "link": video_url,
            "type": "mp4",
            "resolution": resolution,
            "referer": self.REFERER,
            "headers": {
                "User-Agent": "Mozilla/5.0",
                "Referer": self.REFERER,
            },
            "source_priority": priority,
            "android_safe": True,
            "subtitles": subtitles or [],
            "_quality_rank": rank,
            "_bitrate": 0,
        }]
