"""Pure-Python Dean Edwards p.a.c.k.e.r unpacker."""

from __future__ import annotations

import re

_ALPHABET_62 = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _base_encode(num: int, base: int) -> str:
    if num == 0:
        return _ALPHABET_62[0]
    res = []
    while num > 0:
        res.append(_ALPHABET_62[num % base])
        num //= base
    return "".join(reversed(res))


class JsUnpacker:
    """Unpacks JavaScript obfuscated with Dean Edwards packer."""

    PACKED_REGEX = re.compile(
        r"eval\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,\s*[rd]\s*\)\s*\{.*?\}"
        r"\s*\(\s*(['\"].*?['\"])\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(['\"].*?['\"])\.split\(['\"]\|['\"]\)",
        re.DOTALL,
    )

    @classmethod
    def unpack(cls, script: str) -> str:
        """Unpack a single packed JavaScript snippet."""
        match = cls.PACKED_REGEX.search(script)
        if not match:
            return script

        payload_raw, a_str, c_str, words_raw = match.groups()
        try:
            radix = int(a_str)
            count = int(c_str)
        except (ValueError, TypeError):
            return script

        # Strip enclosing quotes safely
        payload = payload_raw[1:-1] if len(payload_raw) >= 2 and payload_raw[0] in ("'", '"') else payload_raw
        words_str = words_raw[1:-1] if len(words_raw) >= 2 and words_raw[0] in ("'", '"') else words_raw
        words = words_str.split("|")

        lookup: dict[str, str] = {}
        for i in range(count):
            encoded = _base_encode(i, radix)
            lookup[encoded] = words[i] if i < len(words) and words[i] else encoded

        def replace_token(m: re.Match) -> str:
            token = m.group(0)
            return lookup.get(token, token)

        return re.sub(r"\b\w+\b", replace_token, payload)

    @classmethod
    def unpack_and_combine(cls, html: str) -> str:
        """Find and unpack all packed script blocks within HTML/JS text."""
        if "eval(function(p,a,c" not in html and "eval(function(p, a, c" not in html:
            return html

        unpacked_parts = []
        for match in cls.PACKED_REGEX.finditer(html):
            unpacked = cls.unpack(match.group(0))
            if unpacked != match.group(0):
                unpacked_parts.append(unpacked)

        if unpacked_parts:
            return "\n".join(unpacked_parts)
        return html
