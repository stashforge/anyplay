"""AllAnime / Mkissa payload and clock URL decoding."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import re
import time
import urllib.parse
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

_logger = logging.getLogger(__name__)

# Legacy fallback key
_ALLANIME_KEY_HEX = "64642f3d5c51401e26207d3649a6753fb4264069563b547270cb98cb051bd04a"

# Constants for modern Mkissa client crypto
KEY_SIZE = 32
SEED_COUNT = 4
SEED_SIZE = KEY_SIZE // SEED_COUNT
SALT_MUL = 20
SALT_ADD = 73
FRAG_MUL = 10
FRAG_ADD = 195
BOOT_PREFIX = "I5AgJjIcVH:"
ANIME_LANE = "k7"
KEY_GROUP = "mkissa"
SITE_HOST = "mkissa.to"
SITE_URL = "https://mkissa.to"
API_BASE = "https://api.mkissa.net"
BOOTSTRAP_PATH = "/client-crypto/v1/bootstrap"
WINDOW_MS = 5 * 60 * 1000
EPOCH_WINDOW_MS = 7 * 24 * 60 * 60 * 1000
MATERIAL_TTL_SEC = 6 * 3600

# Verified pre-baked constants for Build 177
DEFAULT_BUILD_ID = "177"
DEFAULT_SEEDS = [
    "yWOoNgubFtM=",
    "KSFXIy3z700=",
    "wnO0Sm9WX3A=",
    "aFA1PCbg7Dg=",
]
DEFAULT_MASK = bytes.fromhex(
    "b1ca68cba9ce7464fd20e16233502556680eaee5a169cf3b6ebbf70f42b7ccc9"
)


def _allanime_key() -> bytes:
    return binascii.unhexlify(_ALLANIME_KEY_HEX)


def derive_mask(build_id: str, seeds: list[str]) -> bytes | None:
    """Derive 32-byte mask from buildId and 4 base64 seed strings."""
    if not build_id or len(seeds) != SEED_COUNT:
        return None

    stream = bytearray(KEY_SIZE)
    for i in range(KEY_SIZE):
        stream[i] = ord(build_id[i % len(build_id)]) ^ ((i * SALT_MUL + SALT_ADD) & 0xFF)

    mask = bytearray(KEY_SIZE)
    for index in range(len(seeds)):
        try:
            raw = base64.b64decode(seeds[index])
        except Exception:
            return None
        if len(raw) < SEED_SIZE:
            return None
        base = index * SEED_SIZE
        for offset in range(SEED_SIZE):
            mask[base + offset] = (
                raw[offset] ^ stream[base + offset] ^ ((index * FRAG_MUL + offset * FRAG_ADD) & 0xFF)
            )

    if all(b == 0 for b in mask):
        return None
    return bytes(mask)


def derive_key(mask: bytes, part_b: bytes) -> bytes:
    """Derive 32-byte AES key by XORing partB with mask."""
    return bytes(part_b[i] ^ mask[i % len(mask)] for i in range(KEY_SIZE))


def boot_token(
    mask: bytes,
    build_id: str,
    epoch: int,
    key_group: str = KEY_GROUP,
    referer_host: str = SITE_HOST,
    lane: str = ANIME_LANE,
) -> str:
    """Compute HMAC-SHA256 handshake token for /client-crypto/v1/bootstrap."""
    inner = hmac.new(mask, f"{BOOT_PREFIX}{build_id}".encode("utf-8"), hashlib.sha256).digest()
    message = f"{key_group}/{lane}/{referer_host}/{build_id}/{epoch}"
    return hmac.new(inner, message.encode("utf-8"), hashlib.sha256).hexdigest()


def build_aa_req(
    key: bytes,
    epoch: int,
    build_id: str,
    query_hash: str,
    lane: str = ANIME_LANE,
) -> str:
    """Build short-lived encrypted aaReq envelope for stream GraphQL queries."""
    now_ms = int(time.time() * 1000)
    ts = now_ms // WINDOW_MS * WINDOW_MS
    iv_seed = f"{epoch}:{build_id}:{query_hash}:{ts}:{lane}".encode("utf-8")
    iv = hashlib.sha256(iv_seed).digest()[:12]
    payload = json.dumps(
        {
            "v": 1,
            "ts": ts,
            "epoch": epoch,
            "buildId": build_id,
            "qh": query_hash,
            "k": lane,
        },
        separators=(",", ":"),
    ).encode("utf-8")

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        ciphertext = AESGCM(key).encrypt(iv, payload, None)
        return base64.b64encode(b"\x01" + iv + ciphertext).decode("ascii")
    except Exception:
        pass

    for lib in ("Cryptodome", "Crypto"):
        try:
            AES = __import__(f"{lib}.Cipher", fromlist=["AES"]).AES
            cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
            ciphertext, tag = cipher.encrypt_and_digest(payload)
            return base64.b64encode(b"\x01" + iv + ciphertext + tag).decode("ascii")
        except ImportError:
            continue

    return ""


def encrypt_aa_req(payload: str, iv_seed: str, key: bytes | None = None) -> str:
    """Legacy compatibility helper."""
    if key is None:
        material = get_mkissa_material()
        key = material.key if material else _allanime_key()
    iv = hashlib.sha256(iv_seed.encode("utf-8")).digest()[:12]
    payload_bytes = payload.encode("utf-8")

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        encrypted = AESGCM(key).encrypt(iv, payload_bytes, None)
        return base64.b64encode(b"\x01" + iv + encrypted).decode("ascii")
    except Exception:
        pass

    for lib in ("Cryptodome", "Crypto"):
        try:
            AES = __import__(f"{lib}.Cipher", fromlist=["AES"]).AES
            cipher = AES.new(key, AES.MODE_GCM, nonce=iv)
            ciphertext, tag = cipher.encrypt_and_digest(payload_bytes)
            return base64.b64encode(b"\x01" + iv + ciphertext + tag).decode("ascii")
        except ImportError:
            continue

    return ""


def decrypt_tobeparsed(encoded: str, key: bytes | None = None) -> str | None:
    """Decrypt AES-256-GCM encrypted tobeparsed payload."""
    if not encoded:
        return None
    if key is None:
        material = get_mkissa_material()
        key = material.key if material else _allanime_key()

    try:
        encrypted = base64.b64decode(encoded)
    except Exception:
        return None
    if len(encrypted) < 29:
        return None

    nonce = encrypted[1:13] if encrypted[0] == 1 else encrypted[:12]
    ciphertext_and_tag = encrypted[13:] if encrypted[0] == 1 else encrypted[12:]

    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        decrypted = AESGCM(key).decrypt(nonce, ciphertext_and_tag, None)
        return decrypted.decode("utf-8", errors="ignore")
    except Exception:
        pass

    for lib in ("Cryptodome", "Crypto"):
        try:
            AES = __import__(f"{lib}.Cipher", fromlist=["AES"]).AES
            ciphertext = ciphertext_and_tag[:-16]
            tag = ciphertext_and_tag[-16:]
            cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
            decrypted = cipher.decrypt_and_verify(ciphertext, tag)
            return decrypted.decode("utf-8", errors="ignore")
        except ImportError:
            continue
        except Exception:
            continue

    return None


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


@dataclass
class MkissaMaterial:
    key: bytes
    epoch: int
    build_id: str
    expires_at: float
    switch_at: int
    mask: bytes


_cached_material: MkissaMaterial | None = None


def get_mkissa_material(force_refresh: bool = False) -> MkissaMaterial:
    """Obtain or refresh active MKissa AES key and handshake material."""
    global _cached_material
    now = time.time()
    if not force_refresh and _cached_material is not None:
        if now < _cached_material.expires_at:
            return _cached_material

    now_ms = int(now * 1000)
    current_epoch = now_ms // EPOCH_WINDOW_MS

    build_id = DEFAULT_BUILD_ID
    seeds = DEFAULT_SEEDS
    mask = DEFAULT_MASK

    material = _bootstrap_handshake(build_id, seeds, mask, current_epoch)
    if material is not None:
        _cached_material = material
        return material

    # Try scraping dynamic bundle if build 177 is rotated by site
    scraped = _scrape_site_build()
    if scraped:
        sc_build_id, sc_seeds = scraped
        sc_mask = derive_mask(sc_build_id, sc_seeds)
        if sc_mask:
            material = _bootstrap_handshake(sc_build_id, sc_seeds, sc_mask, current_epoch)
            if material is not None:
                _cached_material = material
                return material

    # Offline / pre-baked fallback
    fallback_key = bytes.fromhex(_ALLANIME_KEY_HEX)
    _cached_material = MkissaMaterial(
        key=fallback_key,
        epoch=current_epoch,
        build_id=DEFAULT_BUILD_ID,
        expires_at=now + 300,
        switch_at=0,
        mask=DEFAULT_MASK,
    )
    return _cached_material


def _bootstrap_handshake(
    build_id: str,
    seeds: list[str],
    mask: bytes,
    epoch: int,
) -> MkissaMaterial | None:
    import urllib.request
    epochs_to_try = [epoch, epoch - 1, epoch + 1]
    url = f"{API_BASE}{BOOTSTRAP_PATH}?buildId={build_id}&k={ANIME_LANE}"

    for ep in epochs_to_try:
        try:
            token = boot_token(mask, build_id, ep, KEY_GROUP, SITE_HOST, ANIME_LANE)
            req = urllib.request.Request(
                url,
                headers={
                    "x-build-id": build_id,
                    "x-aa-boot": token,
                    "Origin": SITE_URL,
                    "Referer": f"{SITE_URL}/",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"
                    ),
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    payload = json.loads(resp.read().decode("utf-8"))
                    part_b_b64 = payload.get("partB")
                    if not part_b_b64:
                        continue
                    part_b = base64.b64decode(part_b_b64)
                    key = derive_key(mask, part_b)
                    switch_at = int(payload.get("switchAt") or 0)
                    now = time.time()
                    expires_at = (switch_at / 1000.0) if switch_at > 0 else (now + MATERIAL_TTL_SEC)
                    expires_at = min(expires_at, now + MATERIAL_TTL_SEC)
                    return MkissaMaterial(
                        key=key,
                        epoch=int(payload.get("epoch") or ep),
                        build_id=build_id,
                        expires_at=expires_at,
                        switch_at=switch_at,
                        mask=mask,
                    )
        except Exception as exc:
            _logger.debug("Bootstrap attempt failed for epoch %d: %s", ep, exc)
            continue
    return None


def _scrape_site_build() -> tuple[str, list[str]] | None:
    """Scrape and parse the current MKissa SvelteKit bundle for buildId and seeds."""
    import urllib.request
    try:
        req = urllib.request.Request(
            f"{SITE_URL}/",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": f"{SITE_URL}/",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        app_match = re.search(r'import\("([^"]*/entry/app\.[^"]*\.js)"\)', html)
        if not app_match:
            return None
        app_url = urllib.parse.urljoin(SITE_URL, app_match.group(1))

        req = urllib.request.Request(
            app_url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Referer": f"{SITE_URL}/",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            app_js = resp.read().decode("utf-8", errors="ignore")

        chunk_refs = re.findall(r'["\'](\.\.?/[\w./-]+\.js)["\']', app_js)
        for ref in chunk_refs:
            chunk_url = urllib.parse.urljoin(app_url, ref)
            req = urllib.request.Request(
                chunk_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": f"{SITE_URL}/",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                chunk_body = resp.read().decode("utf-8", errors="ignore")
            if "aaReq" not in chunk_body:
                continue

            parsed = _parse_bundle(chunk_body)
            if parsed:
                return parsed
    except Exception as exc:
        _logger.debug("Failed to scrape site build: %s", exc)
    return None


def _parse_bundle(js: str) -> tuple[str, list[str]] | None:
    """Extract (build_id, seeds) from MKissa SvelteKit bundle chunk."""
    IDENT = r"[\$A-Za-z0-9_]+"
    TABLE_HEAD_REGEX = re.compile(rf"function\s+({IDENT})\(\)\s*\{{\s*(?:const|let|var)\s+{IDENT}\s*=\s*\[")
    BASE_DECODER_REGEX = re.compile(rf"function\s+({IDENT})\(({IDENT})(?:,{IDENT})*\)\{{return \2=\2-\(?([-\d+*\s]+?)\)?,({IDENT})\(\)\[\2\]\}}")
    ALIAS_DECODER_REGEX = re.compile(rf"function\s+({IDENT})\(({IDENT}),({IDENT})\)\{{return ({IDENT})\(({IDENT})((?:[-+](?:[\d+*\s-]+|\{{[^{{}}]*\}}[._][A-Za-z0-9_\$]+))?)\)\}}")
    CALL_PATTERN = rf"({IDENT})\(\s*(-?\d+)\s*(?:,\s*(-?\d+)\s*)?\)"
    CALL_REGEX = re.compile(rf"^{CALL_PATTERN}$")
    SEED_ARRAY_REGEX = re.compile(rf"=\[((?:{CALL_PATTERN}(?:\+{CALL_PATTERN})*,\s*){{3}}{CALL_PATTERN}(?:\+{CALL_PATTERN})*)\]")
    SEED_REGEX = re.compile(r"^[A-Za-z0-9+/]{11}=$")
    TERM_REGEX = re.compile(r"[-+]*\d+(?:\*[-+]*\d+)*")
    MEMBER_ACCESS_REGEX = re.compile(rf"\{{[^{{}}]*:(-?\d+)\}}[._][A-Za-z0-9_\$]+")
    BUILD_ID_DIGITS_REGEX = re.compile(r"^\d{2,10}$")

    def read_string_array(s: str, open_idx: int) -> list[str] | None:
        items = []
        i = open_idx + 1
        n = len(s)
        while i < n:
            c = s[i]
            if c == "]":
                return items
            elif c in (",", " "):
                i += 1
            elif c in ('"', "'"):
                quote = c
                sb = []
                i += 1
                while i < n and s[i] != quote:
                    if s[i] == "\\":
                        sb.append(s[i + 1])
                        i += 2
                    else:
                        sb.append(s[i])
                        i += 1
                if i >= n:
                    return None
                i += 1
                items.append("".join(sb))
            else:
                return None
        return None

    def read_tables(s: str) -> dict[str, list[str]]:
        t = {}
        for m in TABLE_HEAD_REGEX.finditer(s):
            arr = read_string_array(s, m.end() - 1)
            if arr is not None:
                t[m.group(1)] = arr
        return t

    def fold(expr: str) -> int:
        total = 0
        cleaned = expr.replace(" ", "")
        for term in TERM_REGEX.findall(cleaned):
            sign = 1
            body = term
            while body.startswith(("+", "-")):
                if body.startswith("-"):
                    sign = -sign
                body = body[1:]
            parts = body.split("*")
            try:
                val = sign * int(parts[0])
                for f in parts[1:]:
                    val *= int(f)
                total += val
            except ValueError:
                continue
        return total

    tables = read_tables(js)
    bases = {}
    for m in BASE_DECODER_REGEX.finditer(js):
        bases[m.group(1)] = (m.group(4), fold(m.group(3)))

    aliases = {name: (name, 0, 0) for name in bases}
    for m in ALIAS_DECODER_REGEX.finditer(js):
        name, p1, p2, callee, arg, delta = m.groups()
        if callee not in bases:
            continue
        arg_idx = 0 if arg == p1 else 1
        delta_val = fold(MEMBER_ACCESS_REGEX.sub(r"\1", delta)) if delta else 0
        aliases[name] = (callee, arg_idx, delta_val)

    def resolve(call_str: str, rot: int) -> str | None:
        cm = CALL_REGEX.match(call_str.strip())
        if not cm:
            return None
        alias = aliases.get(cm.group(1))
        if not alias:
            return None
        callee, arg_idx, delta = alias
        base_info = bases.get(callee)
        if not base_info:
            return None
        table_name, offset = base_info
        tbl = tables.get(table_name)
        if not tbl:
            return None
        args = [int(x) for x in [cm.group(2), cm.group(3)] if x is not None]
        if arg_idx >= len(args):
            return None
        idx = args[arg_idx] + delta - offset + rot
        return tbl[((idx % len(tbl)) + len(tbl)) % len(tbl)]

    def split_top(s: str) -> list[str]:
        p, depth, curr = [], 0, []
        for ch in s:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                p.append("".join(curr))
                curr = []
            else:
                curr.append(ch)
        p.append("".join(curr))
        return p

    seeds_found: list[str] | None = None
    rotation_found: int | None = None

    for m in SEED_ARRAY_REGEX.finditer(js):
        groups = [[c.strip() for c in elem.split("+")] for elem in split_top(m.group(1))]
        if len(groups) != 4 or any(not calls or any(not CALL_REGEX.match(c) for c in calls) for calls in groups):
            continue
        first_alias = aliases.get(CALL_REGEX.match(groups[0][0]).group(1))
        if not first_alias or first_alias[0] not in bases:
            continue
        tbl = tables.get(bases[first_alias[0]][0])
        if not tbl:
            continue

        for rot in range(len(tbl)):
            current_seeds = []
            for g in groups:
                parts = [resolve(call, rot) for call in g]
                if any(p is None for p in parts):
                    break
                s_val = "".join(parts)
                if not SEED_REGEX.match(s_val):
                    break
                current_seeds.append(s_val)
            if len(current_seeds) == 4:
                seeds_found = current_seeds
                rotation_found = rot
                break
        if seeds_found:
            break

    if not seeds_found or rotation_found is None:
        return None

    # Search for buildId call
    mask_var_match = re.search(rf"function\s+({IDENT})\s*\(\s*\w+\s*=\s*(\w+)\s*[,)]", js)
    candidates = []
    if mask_var_match:
        vname = mask_var_match.group(2)
        for am in re.finditer(rf"\b{re.escape(vname)}\s*=\s*({CALL_PATTERN})\s*(?:,|;|\n)", js):
            candidates.append(am.group(1))

    for c in candidates:
        decoded = resolve(c, rotation_found)
        if decoded and BUILD_ID_DIGITS_REGEX.match(decoded):
            return decoded, seeds_found

    return None


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
