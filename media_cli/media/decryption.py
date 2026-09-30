"""AllAnime payload and clock URL decoding."""

import base64
import binascii
import hashlib

_ALLANIME_KEY_HEX = "29f65d91ec588d32262f1905ae4a7d1cfe3f5ab61e77604ca24912c1772ce2e6"


def _allanime_key():
    return binascii.unhexlify(_ALLANIME_KEY_HEX)


def decrypt_tobeparsed(encoded):
    key = _allanime_key()
    try:
        encrypted = base64.b64decode(encoded)
    except Exception:
        return None
    if len(encrypted) < 30:
        return None

    nonce = encrypted[1:13]
    ciphertext = encrypted[13:-16]
    tag = encrypted[-16:]

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
        except ImportError:
            continue
        except Exception as exc:
            import sys
            sys.stderr.write(f"\\n[DEBUG] {lib} decryption failed: {exc}\\n")
            sys.stderr.flush()
            continue

    return None


def encrypt_aa_req(payload, iv_seed):
    key = _allanime_key()
    nonce = hashlib.sha256(iv_seed.encode("utf-8")).digest()[:12]
    payload_bytes = payload.encode("utf-8")

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        encrypted = AESGCM(key).encrypt(nonce, payload_bytes, None)
        return base64.b64encode(b"\x01" + nonce + encrypted).decode("ascii")
    except Exception:
        pass

    for lib in ("Cryptodome", "Crypto"):
        try:
            AES = __import__(f"{lib}.Cipher", fromlist=["AES"]).AES
            cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
            ciphertext, tag = cipher.encrypt_and_digest(payload_bytes)
            return base64.b64encode(b"\x01" + nonce + ciphertext + tag).decode("ascii")
        except ImportError:
            continue

    return ""


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

