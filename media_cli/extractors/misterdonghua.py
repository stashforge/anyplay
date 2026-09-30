"""MisterDonghua native stream extractor."""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
from typing import Any

from ..services.http import UA
from .base import BaseExtractor

_logger = logging.getLogger(__name__)

AES_KEY = b"kiemtienmua911ca"
AES_IV = b"1234567890oiuytr"


def _decrypt_aes_cbc(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    """Decrypt AES-128-CBC ciphertext using cryptography or pycryptodome."""
    # 1. Try cryptography
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        if padded:
            pad_len = padded[-1]
            if 1 <= pad_len <= 16 and padded.endswith(bytes([pad_len]) * pad_len):
                return padded[:-pad_len]
        return padded
    except ImportError:
        pass
    except Exception as e:
        _logger.debug("cryptography AES-CBC decryption failed: %s", e)

    # 2. Try pycryptodome / pycryptodomex
    for lib in ("Cryptodome", "Crypto"):
        try:
            AES = __import__(f"{lib}.Cipher", fromlist=["AES"]).AES
            cipher = AES.new(key, AES.MODE_CBC, iv)
            padded = cipher.decrypt(ciphertext)
            if padded:
                pad_len = padded[-1]
                if 1 <= pad_len <= 16 and padded.endswith(bytes([pad_len]) * pad_len):
                    return padded[:-pad_len]
            return padded
        except ImportError:
            continue
        except Exception as e:
            _logger.debug("%s AES-CBC decryption failed: %s", lib, e)
            continue

    raise RuntimeError(
        "Neither 'cryptography' nor 'pycryptodome' is installed. "
        "Install with: pip install pycryptodome (or pip install cryptography)"
    )


class MisterDonghuaExtractor(BaseExtractor):
    """Native extractor for misterdonghua.in video player embeds."""

    name = "MisterDonghua"
    domains = [
        "misterdonghua.in",
    ]
    patterns = [
        re.compile(r"misterdonghua\.in/(?:embed/|v/|#)?([a-zA-Z0-9]+)", re.IGNORECASE),
    ]

    def _extract_id(self, url: str) -> str | None:
        if not url:
            return None
        parsed = urllib.parse.urlparse(url)
        if parsed.fragment:
            clean_frag = parsed.fragment.strip("/")
            if clean_frag:
                return clean_frag
        qs = urllib.parse.parse_qs(parsed.query)
        if "id" in qs and qs["id"]:
            return qs["id"][0]
        match = re.search(r"misterdonghua\.in/(?:embed/|v/)?([a-zA-Z0-9]+)", url)
        if match:
            return match.group(1)
        return None

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
        video_id = self._extract_id(url)
        if not video_id:
            return []

        api_url = f"https://misterdonghua.in/api/v1/video?id={video_id}"
        req_headers = {
            "User-Agent": UA,
            "Referer": "https://misterdonghua.in/",
            "Origin": "https://misterdonghua.in",
        }
        if headers:
            req_headers.update(headers)

        hex_data = self.fetch_page(api_url, headers=req_headers, referer="https://misterdonghua.in/", timeout=12)
        if not hex_data or not hex_data.strip():
            return []

        try:
            raw_bytes = bytes.fromhex(hex_data.strip())
            decrypted = _decrypt_aes_cbc(AES_KEY, AES_IV, raw_bytes)
            data = json.loads(decrypted.decode("utf-8", errors="ignore"))
        except Exception as exc:
            _logger.warning("Failed to decrypt misterdonghua metadata: %s", exc)
            return []

        if not isinstance(data, dict):
            return []

        master_url = data.get("cfNative") or data.get("source")
        if not master_url:
            return []

        stream_name = name or self.name
        stream_headers = {
            "User-Agent": UA,
            "Referer": "https://misterdonghua.in/",
            "Origin": "https://misterdonghua.in",
        }
        if headers:
            stream_headers.update(headers)

        return self.extract_m3u8(
            master_url,
            referer="https://misterdonghua.in/",
            origin="https://misterdonghua.in",
            name=stream_name,
            priority=priority,
            subtitles=subtitles,
            headers=stream_headers,
        )
