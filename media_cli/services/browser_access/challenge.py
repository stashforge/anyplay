"""Conservative verification detection shared by all adopting transports."""

from __future__ import annotations

import re
from collections.abc import Mapping


def is_verification_challenge(status: int, headers: Mapping[str, str], body: str) -> bool:
    if any(key.casefold() == "cf-mitigated" and value.strip().casefold() == "challenge"
           for key, value in headers.items()):
        return True
    if status not in (200, 403, 429, 503):
        return False
    sample = body[:65536].casefold()
    title = re.search(r"<title\b[^>]*>\s*([^<]+)</title\s*>", sample)
    if title is None or not any(label in title[1] for label in (
            "just a moment", "attention required", "checking your browser")):
        return False
    return "/cdn-cgi/challenge-platform/" in sample or "cf-chl-" in sample
