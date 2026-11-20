"""Isolated cryptographic primitives and credential helpers for Gotaku.

Upstream reference: https://github.com/yuzono/anime-extensions (Apache-2.0)
License: Apache-2.0 (retained per upstream attribution requirements)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import zlib
from typing import Any

# Default bootstrap credentials from Gotaku web bundle
DEFAULT_API_VERSION = 10
DEFAULT_API_SEED = bytes([
    30, 99, 206, 236, 176, 26, 228, 147, 55, 35, 152, 123, 6, 17, 126,
    179, 40, 181, 197, 210, 194, 90, 232, 35, 247, 20, 130, 43, 71, 117, 123, 82,
])
DEFAULT_MANIFEST_SEED = bytes.fromhex(
    "458e5403c40a73332706df406193dd6b040a2ea97a6bb59b52b294a61006e6ef"
)
DEFAULT_HKDF_INFO = "api-seal|v1"

_BUNDLE_CREDENTIAL_REGEX = re.compile(
    r"var\s+ie\s*=\s*\[\s*(\d+)\s*,\s*\[([0-9,\s]+)\]\s*,\s*\[([0-9,\s]+)\]\s*\]\s*,\s*ae\s*=\s*ie\[0\]\s*,\s*oe\s*=\s*[`'\"]([^`'\"]+)[`'\"]"
)


def b64url_encode(data: bytes) -> str:
    """Base64url without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    """Lenient base64url decode accepting unpadded or padded input."""
    cleaned = text.strip().rstrip("=")
    cleaned += "=" * ((4 - len(cleaned) % 4) % 4)
    return base64.urlsafe_b64decode(cleaned)


def _require_crypto():
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        return hashes, Cipher, algorithms, modes, AESGCM, HKDF
    except ImportError as exc:
        raise ImportError(
            "Gotaku requires 'cryptography' for playback extraction and decryption. "
            "Install it via your package manager or pip install cryptography."
        ) from exc


def derive_api_key(seed: bytes, info: str = DEFAULT_HKDF_INFO) -> bytes:
    """Derive 256-bit AES-GCM key for ?k=N API endpoints using HKDF-SHA256."""
    hashes, _, _, _, _, hkdf_cls = _require_crypto()
    hkdf = hkdf_cls(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"\x00" * 32,
        info=info.encode("utf-8"),
    )
    return hkdf.derive(seed)


def decrypt_api_body(
    body: bytes,
    version: int = DEFAULT_API_VERSION,
    seed: bytes = DEFAULT_API_SEED,
    info: str = DEFAULT_HKDF_INFO,
) -> Any:
    """Decrypt binary payload from ?k=N API endpoints.

    Wire format: [1B version][12B IV][Ciphertext + 16B GCM Tag].
    Decompresses zlib deflation and parses JSON.
    """
    if not body or len(body) <= 29:
        return None

    # Wire check: version byte
    version_byte = version & 0xFF
    if body[0] != version_byte:
        # Fallback: check if body is base64-encoded
        try:
            body = b64url_decode(body.decode("utf-8", errors="ignore"))
            if not body or len(body) <= 29 or body[0] != version_byte:
                return None
        except Exception:
            return None

    iv = body[1:13]
    ciphertext_and_tag = body[13:]
    key = derive_api_key(seed, info)

    _, _, _, _, aesgcm_cls, _ = _require_crypto()
    try:
        decrypted = aesgcm_cls(key).decrypt(iv, ciphertext_and_tag, None)
        plaintext = zlib.decompress(decrypted)
        return json.loads(plaintext.decode("utf-8"))
    except Exception:
        return None


def extract_credentials_from_bundle(js_code: str) -> tuple[int, bytes, str] | None:
    """Extract (version, seed, info) statically from main JS bundle without running JS."""
    if not js_code:
        return None
    match = _BUNDLE_CREDENTIAL_REGEX.search(js_code)
    if not match:
        return None
    try:
        ver = int(match.group(1))
        arr1 = [int(x.strip()) for x in match.group(2).split(",")]
        arr2 = [int(x.strip()) for x in match.group(3).split(",")]
        info = match.group(4).strip()
        if len(arr1) != 32 or len(arr2) != 32:
            return None
        seed = bytes(a ^ b for a, b in zip(arr1, arr2))
        return ver, seed, info
    except Exception:
        return None


def build_manifest_handshake_request(
    manifest_base: str,
    stamp: str,
    manifest_seed: bytes = DEFAULT_MANIFEST_SEED,
) -> tuple[str, bytes]:
    """Build URL path for manifest handshake.

    Returns (full_request_url, nonce). The same nonce must be passed to decrypt_manifest_response.
    """
    _, _, _, _, aesgcm_cls, _ = _require_crypto()
    nonce = os.urandom(16)
    payload = json.dumps(
        {"nonce": b64url_encode(nonce), "stamp": stamp},
        separators=(",", ":"),
    ).encode("utf-8")

    iv = os.urandom(12)
    ct = aesgcm_cls(manifest_seed).encrypt(iv, payload, None)
    req_path = b64url_encode(iv + ct)
    manifest_url = f"{manifest_base.rstrip('/')}/{req_path}"
    return manifest_url, nonce


def decrypt_manifest_response(
    response_text: str,
    nonce: bytes,
    manifest_seed: bytes = DEFAULT_MANIFEST_SEED,
) -> dict[str, Any] | None:
    """Decrypt base64url manifest response using the handshake nonce as HKDF salt."""
    hashes, _, _, _, aesgcm_cls, hkdf_cls = _require_crypto()
    try:
        blob = b64url_decode(response_text)
        if len(blob) < 28:
            return None
        resp_iv = blob[:12]
        resp_ct = blob[12:]
        resp_key = hkdf_cls(
            algorithm=hashes.SHA256(),
            length=32,
            salt=nonce,
            info=b"manifest-resp|v1",
        ).derive(manifest_seed)
        decrypted = aesgcm_cls(resp_key).decrypt(resp_iv, resp_ct, None)
        return json.loads(decrypted.decode("utf-8"))
    except Exception:
        return None


def extract_playlist_token(url: str) -> str:
    """Extract playlist token from Gotaku master or variant HLS URLs.

    Handles:
    - Query parameters: ?token=...
    - Master URLs: /route/<uuid>/<token>/master.m3u8 -> parts[2]
    - Variant URLs: /route/<uuid>/<token>/1080/manifest.m3u8 -> parts[2]
    - Alternate stream endpoints: /api/v1/stream/<id>/<token>/master.m3u8
    """
    if not url:
        return ""
    import urllib.parse

    parsed = urllib.parse.urlsplit(url)
    qs = urllib.parse.parse_qs(parsed.query)
    if "token" in qs and qs["token"]:
        return qs["token"][0]

    parts = parsed.path.strip("/").split("/")
    if not parts:
        return ""

    if parts[0] == "route" and len(parts) >= 3:
        return parts[2]

    if parts[0] == "p" and len(parts) >= 4 and parts[1].isdigit():
        return parts[3]

    if "stream" in parts:
        idx = parts.index("stream")
        if idx + 1 < len(parts):
            return parts[idx + 1]

    for part in parts:
        if (
            16 <= len(part) <= 64
            and not part.endswith((".m3u8", ".ts", ".woff", ".mp4", ".m4s"))
            and part not in ("route", "master", "manifest", "stream")
            and not part.isdigit()
            and not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", part, re.I)
        ):
            return part

    return parts[-2] if len(parts) >= 2 else parts[0]


def derive_playlist_key(key_seed: bytes, token: str) -> bytes:
    """Derive 256-bit AES-GCM playlist key from manifest keySeed and URL token."""
    hashes, _, _, _, _, hkdf_cls = _require_crypto()
    return hkdf_cls(
        algorithm=hashes.SHA256(),
        length=32,
        salt=token.encode("ascii", errors="ignore"),
        info=b"pl|v1",
    ).derive(key_seed)


def decrypt_playlist_body(body: bytes, playlist_key: bytes) -> str:
    """Decrypt AES-GCM encrypted HLS playlist.

    If body is already plaintext (#EXTM3U), returns text as-is.
    Fails cleanly (returns empty string) if decryption fails or result is not a valid playlist.
    """
    if not body:
        return ""
    text_peek = body[:32].decode("utf-8", errors="ignore").lstrip("\ufeff").lstrip()
    if text_peek.startswith("#EXTM3U"):
        return body.decode("utf-8", errors="replace")

    if len(body) < 28:
        return ""

    _, _, _, _, aesgcm_cls, _ = _require_crypto()
    try:
        iv = body[:12]
        ct = body[12:]
        decrypted = aesgcm_cls(playlist_key).decrypt(iv, ct, None)
        text = decrypted.decode("utf-8", errors="replace")
        peek = text.lstrip("\ufeff").lstrip()
        if peek.startswith("#EXTM3U") or "#EXT" in peek:
            return text
        return ""
    except Exception:
        return ""


def derive_segment_counter(segment_url: str) -> bytes:
    """Derive 128-bit AES-CTR counter block from the segment filename."""
    url_id = segment_url.split("?")[0].rstrip("/").split("/")[-1].split(".")[0]
    digest = hashlib.sha256(url_id.encode("utf-8")).digest()
    return digest[:8] + (b"\x00" * 8)


def decrypt_segment_prefix(
    chunk: bytes,
    key_seed: bytes,
    counter: bytes,
    offset_in_prefix: int = 0,
    max_decrypt_bytes: int = 8192,
) -> bytes:
    """Decrypt partial AES-CTR obfuscated prefix of an HLS segment.

    Bytes beyond max_decrypt_bytes pass through untouched.
    """
    if not chunk or offset_in_prefix >= max_decrypt_bytes:
        return chunk

    _, cipher_cls, algos, modes_cls, _, _ = _require_crypto()
    to_decrypt = min(len(chunk), max_decrypt_bytes - offset_in_prefix)

    cipher = cipher_cls(algos.AES(key_seed), modes_cls.CTR(counter))
    encryptor = cipher.encryptor()

    # Advance keystream to offset_in_prefix if range starts mid-prefix
    if offset_in_prefix > 0:
        scratch_remaining = offset_in_prefix
        scratch = b"\x00" * min(8192, scratch_remaining)
        while scratch_remaining > 0:
            step = min(scratch_remaining, len(scratch))
            encryptor.update(scratch[:step])
            scratch_remaining -= step

    ks = encryptor.update(b"\x00" * to_decrypt)
    head = bytes(b ^ k for b, k in zip(chunk[:to_decrypt], ks))
    return head + chunk[to_decrypt:]
