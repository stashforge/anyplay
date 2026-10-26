"""Closed browser command vocabulary and exact provider-origin boundaries.

The extension must repeat these checks before executing a command. This policy
is not a sandbox for provider pages and does not restrict their own subresources.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urlsplit


class BrowserPolicyError(ValueError):
    """An operation, URL or response violates the provider boundary."""


def _origin(url: str) -> str:
    if not isinstance(url, str) or len(url) > 8192 or not url:
        raise BrowserPolicyError("Invalid browser URL")
    decoded = unquote(url)
    if (any(ord(char) <= 32 for char in url)
            or any(ord(char) < 32 or ord(char) == 127 or char == "\\" for char in decoded)):
        raise BrowserPolicyError("Ambiguous browser URL")
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if (parts.scheme != "https" or parts.username is not None or parts.password is not None
                or parts.port not in (None, 443) or "#" in url):
            raise ValueError
        # ASCII DNS names only: do not disagree with Chromium over IDNA spelling.
        labels = host.split(".")
        if (len(host) > 253 or len(labels) < 2
                or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                       for label in labels)
                or labels[-1] in {"localhost", "local", "internal", "lan", "home"}
                or not re.fullmatch(r"[a-z]{2,63}", labels[-1])):
            raise ValueError
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError
    except ValueError:
        raise BrowserPolicyError("Browser URL must use an approved HTTPS origin") from None
    return "https://" + host


@dataclass(frozen=True, init=False)
class BrowserPolicy:
    """Immutable capabilities for one provider; no wildcard origin inheritance."""

    provider_id: str
    origins: frozenset[str]
    max_body_bytes: int

    def __init__(self, provider_id: str, origins: Iterable[str], *, max_body_bytes: int = 1048576):
        if (not isinstance(provider_id, str)
                or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", provider_id)):
            raise BrowserPolicyError("Invalid browser provider ID")
        if (isinstance(max_body_bytes, bool) or not isinstance(max_body_bytes, int)
                or not 64 <= max_body_bytes <= 1048576):
            raise BrowserPolicyError("Invalid browser response limit")
        approved = set()
        if isinstance(origins, (str, bytes)):
            raise BrowserPolicyError("Browser origins must be a collection")
        for url in origins:
            origin = _origin(url)
            parts = urlsplit(url)
            if parts.path not in ("", "/") or "?" in url:
                raise BrowserPolicyError("Allowlist entries must be origins, not URLs")
            approved.add(origin)
        if not approved:
            raise BrowserPolicyError("Browser policy requires an origin")
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "origins", frozenset(approved))
        object.__setattr__(self, "max_body_bytes", max_body_bytes)

    def validate_url(self, url: str) -> str:
        if _origin(url) not in self.origins:
            raise BrowserPolicyError("Browser URL is outside the provider allowlist")
        return url

    def command(self, operation: str, url: str) -> dict[str, Any]:
        if operation not in ("fetch", "document"):
            raise BrowserPolicyError("Unsupported browser operation")
        self.validate_url(url)
        command = {"operation": operation, "url": url, "max_body_bytes": self.max_body_bytes}
        if operation == "fetch":
            command.update(method="GET", redirect="error")
        return command

    def response(self, reply: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(reply, dict) or set(reply) != {"url", "status", "body"}:
            raise BrowserPolicyError("Invalid browser response fields")
        self.validate_url(reply["url"])
        status, body = reply["status"], reply["body"]
        if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
            raise BrowserPolicyError("Invalid browser response status")
        try:
            if not isinstance(body, str) or len(body.encode("utf-8")) > self.max_body_bytes:
                raise ValueError
        except ValueError:
            raise BrowserPolicyError("Invalid browser response body") from None
        return dict(reply)
