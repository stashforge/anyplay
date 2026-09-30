"""Kwik native stream extractor."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .base import CURL_CFFI_AVAILABLE, BaseExtractor, cffi_requests
from .unpack import JsUnpacker


class KwikExtractor(BaseExtractor):
    """Extractor for Kwik (used by AnimePahe)."""

    name = "Kwik"
    domains = [
        "kwik.cx",
        "kwik.si",
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
        **kwargs,
    ) -> list[dict]:
        try:
            from ..providers.animepahe import _detect_firefox_user_agent, _find_browser_cookies
            default_ua = _detect_firefox_user_agent()
            browser_cookies = _find_browser_cookies()
        except Exception:
            default_ua = "Mozilla/5.0 (X11; Linux x86_64; rv:155.0) Gecko/20100101 Firefox/155.0"
            browser_cookies = {}

        req_headers = {
            "Referer": referer or "https://animepahe.pw/",
            "User-Agent": default_ua,
        }
        if headers:
            req_headers.update(headers)

        html = self.fetch_page(
            url,
            headers=req_headers,
            referer=req_headers.get("Referer"),
            cookies=browser_cookies,
        )
        if not html:
            return []

        # Unpack script
        soup = BeautifulSoup(html, "html.parser")
        unpacked_code = ""
        for script_tag in soup.find_all("script"):
            data = script_tag.string or script_tag.text or ""
            if "eval(function(p,a,c" in data or "eval(function(p, a, c" in data:
                unpacked_code = JsUnpacker.unpack_and_combine(data)
                break

        if not unpacked_code:
            unpacked_code = html

        # Normalize escaped quotes from packer output
        clean_code = unpacked_code.replace(r"\'", "'").replace(r'\"', '"')

        # Check for direct m3u8 or mp4 source/file
        m = re.search(r'(?:source|file)\s*[:=]\s*[\'"](https?://[^\'"\s;]+)', clean_code)
        if not m:
            m = re.search(r'source\s*=\s*["\']([^"\']+\.(?:m3u8|mp4)[^"\']*)["\']', clean_code)
        if not m:
            m = re.search(r'file\s*:\s*["\']([^"\']+\.(?:m3u8|mp4)[^"\']*)["\']', clean_code)

        stream_name = name or self.name
        resolution = "1080p" if "1080" in stream_name else ("360p" if "360" in stream_name else "720p")
        stream_headers = {
            "Referer": "https://kwik.cx/",
            "User-Agent": req_headers.get("User-Agent", default_ua),
        }

        if m:
            stream_url = m.group(1).rstrip("\\'\"")
            is_hls = ".m3u8" in stream_url
            return [{
                "source_name": stream_name,
                "link": stream_url,
                "type": "hls" if is_hls else "mp4",
                "resolution": resolution,
                "referer": "https://kwik.cx/",
                "headers": stream_headers,
                "source_priority": priority,
                "android_safe": True,
                "requires_proxy": True,
                "subtitles": subtitles or [],
                "_quality_rank": 1080 if resolution == "1080p" else (720 if resolution == "720p" else 360),
                "_bitrate": 0,
            }]

        # Check for form POST (action and _token)
        action_match = re.search(r'action=["\']([^"\']+)["\']', clean_code)
        token_match = re.search(r'value=["\']([^"\']+)["\']', clean_code)

        if action_match and token_match and CURL_CFFI_AVAILABLE and cffi_requests is not None:
            action_url = action_match.group(1)
            token = token_match.group(1)
            try:
                imp = "firefox147" if "Firefox" in req_headers.get("User-Agent", "") else "chrome"
                post_resp = cffi_requests.post(
                    action_url,
                    data={"_token": token},
                    headers={"Referer": url, "User-Agent": req_headers.get("User-Agent", default_ua)},
                    cookies=browser_cookies,
                    impersonate=imp,
                    allow_redirects=False,
                    timeout=10,
                )
                final_location = post_resp.headers.get("Location") or post_resp.headers.get("location")
                if final_location:
                    return [{
                        "source_name": stream_name,
                        "link": final_location,
                        "type": "mp4",
                        "resolution": resolution,
                        "referer": "https://kwik.cx/",
                        "headers": stream_headers,
                        "source_priority": priority,
                        "android_safe": True,
                        "requires_proxy": True,
                        "subtitles": subtitles or [],
                        "subtitle_url": next((s["url"] for s in subtitles if s.get("default")), subtitles[0]["url"]) if subtitles else "",
                        "_quality_rank": 1080 if resolution == "1080p" else (720 if resolution == "720p" else 360),
                        "_bitrate": 0,
                    }]
            except Exception:
                pass

        return []
