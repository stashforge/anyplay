"""Shared AES metadata decoding for related public video-player APIs."""

import json
import logging

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


def decode_player_metadata(payload):
    from ..services.requirements import require_any_python
    require_any_python(
        ("cryptography.hazmat.primitives.ciphers", "Cryptodome.Cipher.AES", "Crypto.Cipher.AES"),
        "StreamWish player metadata",
    )
    if not isinstance(payload, str) or len(payload) > 2 * 1024 * 1024:
        raise ValueError("Invalid player metadata")
    try:
        data = json.loads(_decrypt_aes_cbc(AES_KEY, AES_IV, bytes.fromhex(payload.strip())))
    except Exception:
        raise ValueError("Unable to decode player metadata") from None
    if not isinstance(data, dict):
        raise ValueError("Invalid player metadata")
    return data
