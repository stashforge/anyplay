"""Vidnest native stream extractor."""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

from .base import BaseExtractor


class VidnestExtractor(BaseExtractor):
    """Extractor for Vidnest and related hosts."""

    name = "Vidnest"
    domains = [
        "vidnest.io",
        "vidnest.net",
        "vidnest.fun",
        "vidnest.com",
    ]
    patterns = [
        re.compile(r"vidnest\.", re.IGNORECASE),
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
        parsed = urllib.parse.urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        file_code = (
            parsed.path.rstrip("/").split("/")[-1].split("-")[-1].replace(".html", "")
        )
        if not file_code:
            return []

        post_data = urllib.parse.urlencode({
            "op": "embed",
            "file_code": file_code,
            "auto": "1",
            "referer": referer or "https://mkissa.to/",
        }).encode("utf-8")

        post_headers = {
            "Referer": url,
            "Origin": origin,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        if headers:
            post_headers.update(headers)

        import ssl
        import urllib.request

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(f"{origin}/dl", data=post_data, headers=post_headers)
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=8) as r:
                html = r.read().decode("utf-8", errors="ignore")
        except Exception:
            return []

        sources_match = re.search(r"sources\s*:\s*\[(.*?)\]", html, re.DOTALL)
        if not sources_match:
            return []

        prefix = name or self.name
        video_headers = {
            "Referer": f"{origin}/",
            "User-Agent": post_headers.get("User-Agent", "Mozilla/5.0"),
        }
        streams = []

        for m in re.finditer(r'file\s*:\s*"([^"]+)"(?:\s*,\s*label\s*:\s*"([^"]*)")?', sources_match.group(1)):
            file_url = m.group(1)
            raw_label = m.group(2) or ""
            res_match = re.search(r"\d+x(\d+)", raw_label)
            res = f"{res_match.group(1)}p" if res_match else (raw_label if raw_label else "1080p")
            stream_url = file_url if file_url.startswith("http") else f"{origin}{file_url}"

            streams.append({
                "source_name": f"{prefix} ({res})",
                "source_parent_name": prefix,
                "link": stream_url,
                "type": "mp4" if ".mp4" in stream_url else "hls",
                "resolution": res,
                "referer": f"{origin}/",
                "headers": video_headers,
                "source_priority": priority,
                "android_safe": True,
            })

        return streams
