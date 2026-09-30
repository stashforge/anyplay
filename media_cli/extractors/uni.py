"""Uni (uns.bio) native stream extractor."""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

from .base import BaseExtractor

KEY = b"kiemtienmua911ca"
IV = b"1234567890oiuytr"


class UniExtractor(BaseExtractor):
    """Extractor for uns.bio (Uni) player used by AllAnime/Mkissa."""

    name = "Uni"
    domains = [
        "uns.bio",
        "watchanime.uns.bio",
    ]
    patterns = [
        re.compile(r"uns\.bio", re.IGNORECASE),
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
        origin = f"{parsed.scheme}://${parsed.netloc}".replace("$", "")
        origin = f"{parsed.scheme}://{parsed.netloc}"
        video_id = (parsed.fragment or "").split("&")[0].strip()
        if not video_id and "/v/" in parsed.path:
            video_id = parsed.path.rstrip("/").split("/")[-1]
        if not video_id:
            return []

        embedder_host = "mkissa.to"
        api_url = f"{origin}/api/v1/video?id={video_id}&w=1920&h=1080&r={embedder_host}"

        req_headers = {
            "Referer": f"{origin}/",
            "Accept": "*/*",
        }
        if headers:
            req_headers.update(headers)

        try:
            hex_payload = self.fetch_page(api_url, headers=req_headers, referer=f"{origin}/", timeout=8)
        except Exception:
            return []

        if not hex_payload:
            return []

        try:
            raw_bytes = bytes.fromhex(hex_payload.strip())
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.backends import default_backend

            cipher = Cipher(algorithms.AES(KEY), modes.CBC(IV), backend=default_backend())
            decryptor = cipher.decryptor()
            decrypted = decryptor.update(raw_bytes) + decryptor.finalize()
            pad_len = decrypted[-1]
            if 1 <= pad_len <= 16:
                plaintext = decrypted[:-pad_len].decode("utf-8")
            else:
                plaintext = decrypted.decode("utf-8", errors="ignore")
            data = json.loads(plaintext)
        except Exception:
            return []

        # Candidate networks in order of preference
        network_candidates = [
            ("Cloudflare", data.get("cfNative") or data.get("cf")),
            ("In-House", data.get("source")),
            ("Google", data.get("hlsVideoGoogle")),
            ("Tiktok", data.get("hlsVideoTiktok")),
        ]

        prefix = name or self.name
        streams = []

        for net_name, path in network_candidates:
            if not path or not isinstance(path, str):
                continue
            if path.startswith("//"):
                playlist_url = f"https:{path}"
            elif path.startswith("http"):
                playlist_url = path
            else:
                playlist_url = f"{origin}/{path.lstrip('/')}"

            player_headers = {
                "Referer": f"{origin}/",
                "Origin": origin,
            }
            try:
                extracted = self.extract_m3u8(
                    playlist_url,
                    referer=f"{origin}/",
                    origin=origin,
                    name=f"{prefix} {net_name}",
                    priority=priority,
                    headers=player_headers,
                    subtitles=subtitles,
                )
                if extracted:
                    streams.extend(extracted)
                    break  # Primary working network is sufficient
            except Exception:
                continue

        return streams
