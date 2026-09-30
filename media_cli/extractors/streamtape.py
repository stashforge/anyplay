"""StreamTape native stream extractor."""

from __future__ import annotations

import re

from .base import BaseExtractor


class StreamTapeExtractor(BaseExtractor):
    """Extractor for StreamTape and mirrors."""

    name = "StreamTape"
    domains = [
        "streamtape.com",
        "streamtape.net",
        "streamtape.xyz",
        "streamtape.to",
        "streamta.pe",
        "strcloud.link",
        "streamadblocker.xyz",
    ]

    def extract(
        self,
        url: str,
        *,
        name: str = "",
        priority: int = 2,
        subtitles: list[dict] | None = None,
    ) -> list[dict]:
        # Convert /v/<id> or /d/<id> to embed format /e/<id>
        embed_url = url
        if "/e/" not in url:
            parts = [p for p in url.split("/") if p]
            if len(parts) >= 4:
                file_id = parts[3] if parts[2] in self.domains else parts[-1]
                embed_url = f"https://streamtape.com/e/{file_id}"

        html = self.fetch_page(embed_url, referer="https://streamtape.com/")
        if not html:
            return []

        # Find target link construction
        target_token = "robotlink" if "robotlink" in html else "idealdirectlink"
        if target_token not in html:
            return []

        # Regex matching: document.getElementById('robotlink').innerHTML = '...' + ('xcd...')
        match = re.search(
            r"document\.getElementById\('" + target_token + r"'\)\.innerHTML\s*=\s*['\"]([^'\"]+)['\"]\s*\+\s*\(['\"]([^'\"]+)['\"]",
            html,
        )

        video_url = ""
        if match:
            part1 = match.group(1)
            part2 = match.group(2)
            # Remove leading substring token if needed (often starts with 'xcd' or similar noise)
            if part2.startswith("xcd"):
                part2 = part2[3:]
            video_url = f"https:{part1}{part2}"
        else:
            # Fallback to substring extraction
            try:
                block = html.split(f"document.getElementById('{target_token}').innerHTML = '")[1]
                part1 = block.split("'")[0]
                part2_block = block.split("+ ('")[1]
                part2 = part2_block.split("')")[0]
                if part2.startswith("xcd"):
                    part2 = part2[3:]
                video_url = f"https:{part1}{part2}"
            except Exception:
                video_url = ""

        if not video_url:
            return []

        stream_name = f"{name or self.name} (HD)"
        return [{
            "source_name": stream_name,
            "link": video_url,
            "type": "mp4",
            "resolution": "720p",
            "referer": embed_url,
            "headers": {
                "User-Agent": "Mozilla/5.0",
                "Referer": embed_url,
            },
            "source_priority": priority,
            "android_safe": True,
            "subtitles": subtitles or [],
            "_quality_rank": 720,
            "_bitrate": 0,
        }]
