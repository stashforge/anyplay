"""DoodStream native stream extractor."""

from __future__ import annotations

import random
import string
import time
import urllib.parse

from .base import BaseExtractor


class DoodExtractor(BaseExtractor):
    """Extractor for DoodStream and clones."""

    name = "DoodStream"
    domains = [
        "dood.to",
        "dood.watch",
        "doodstream.com",
        "ds2play.com",
        "d0o0d.com",
        "do0od.com",
        "d000d.com",
        "dood.so",
        "dood.la",
        "dood.ws",
        "dood.sh",
        "dood.cx",
        "dood.pm",
        "dood.re",
        "playmogo.com",
        "dooodster.com",
        "doodstream.co",
        "ds2video.com",
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
        dood_host = parsed.netloc

        # Convert /d/<id> to /e/<id> if necessary
        embed_url = url.replace("/d/", "/e/")
        html = self.fetch_page(embed_url, referer=f"https://{dood_host}/")
        if not html or "'/pass_md5/" not in html:
            return []

        try:
            md5_path = html.split("'/pass_md5/")[1].split("',")[0].split("'")[0]
            token = md5_path.split("/")[-1]

            pass_url = f"https://{dood_host}/pass_md5/{md5_path}"
            video_url_prefix = self.fetch_page(pass_url, referer=embed_url).strip()
            if not video_url_prefix or not video_url_prefix.startswith("http"):
                return []

            random_str = "".join(random.choices(string.ascii_letters + string.digits, k=10))
            expiry = int(time.time() * 1000)
            final_url = f"{video_url_prefix}{random_str}?token={token}&expiry={expiry}"

            stream_name = f"{name or self.name} (720p)"
            return [{
                "source_name": stream_name,
                "link": final_url,
                "type": "mp4",
                "resolution": "720p",
                "referer": f"https://{dood_host}/",
                "headers": {
                    "User-Agent": "Mozilla/5.0",
                    "Referer": f"https://{dood_host}/",
                },
                "source_priority": priority,
                "android_safe": True,
                "subtitles": subtitles or [],
                "_quality_rank": 720,
                "_bitrate": 0,
            }]
        except Exception:
            return []
