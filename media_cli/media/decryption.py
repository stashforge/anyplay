"""Shared source URL decoding and Senshi encrypted playlist support."""

import base64
import binascii


def decrypt_source_url(url: str) -> str:
    """Decrypt obfuscated source URLs with modern XOR keys or legacy CIPHER."""
    if not isinstance(url, str):
        return ""
    url = url.strip()
    if url.startswith("--"):
        hex_payload, key_idx = url[2:], 3
    elif url.startswith("#-"):
        hex_payload, key_idx = url[2:], 2
    elif url.startswith("##"):
        hex_payload, key_idx = url[2:], 1
    elif url.startswith("-#"):
        hex_payload, key_idx = url[2:], 4
    elif url.startswith("#"):
        hex_payload, key_idx = url[1:], 0
    else:
        hex_payload, key_idx = url, None

    if len(hex_payload) % 2 != 0:
        return url

    try:
        raw_bytes = bytes.fromhex(hex_payload)
    except ValueError:
        return url

    masks = (0, 48, 49, 56, 59)
    if key_idx is not None and key_idx < len(masks):
        mask = masks[key_idx]
        return bytes(b ^ mask for b in raw_bytes).decode("utf-8", errors="ignore")

    for mask in masks:
        try:
            decoded = bytes(b ^ mask for b in raw_bytes).decode("utf-8", errors="ignore")
            if "/clock" in decoded or "http://" in decoded or "https://" in decoded:
                return decoded
        except Exception:
            continue

    return url


_SENSHI_FALLBACK_KEY = "6EE2721327ED469BB6D93AB9B7A838045190B5BA85D9CEA3B1E17805F7B4AEF6"


def decrypt_em3u8(text: str, key_hex: str = _SENSHI_FALLBACK_KEY) -> str | None:
    """Decrypt an AES-256-GCM encrypted playlist with the EM3U8v1: prefix."""
    if not isinstance(text, str) or not text.startswith("EM3U8v1:"):
        return None
    try:
        raw = base64.b64decode(text[len("EM3U8v1:"):].strip())
        if len(raw) < 29:
            return None
        nonce = raw[:12]
        ciphertext = raw[12:-16]
        tag = raw[-16:]
        key = binascii.unhexlify(key_hex)

        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            decrypted = AESGCM(key).decrypt(nonce, ciphertext + tag, None)
            return decrypted.decode("utf-8", errors="ignore")
        except Exception:
            pass

        for lib in ("Cryptodome", "Crypto"):
            try:
                AES = __import__(f"{lib}.Cipher", fromlist=["AES"]).AES
                cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
                decrypted = cipher.decrypt_and_verify(ciphertext, tag)
                return decrypted.decode("utf-8", errors="ignore")
            except Exception:
                continue
    except Exception:
        pass
    return None
