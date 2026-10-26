"""Validated, private cf_clearance reuse for one provider and exact origin."""

from __future__ import annotations

import ctypes
import ctypes.util
import hashlib
import json
import math
import os
import re
import secrets
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from http.cookiejar import Cookie, CookieJar
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from .diagnostics import record
from .policy import BrowserPolicy, _origin

if TYPE_CHECKING:
    from curl_cffi.requests import Session

    from .access import AccessResponse

_MAX_CACHE = 65536
_MAX_BODY = 1048576


class ClearanceRequestError(OSError):
    """Replay failed without exposing credentials or transport error details."""


@lru_cache(maxsize=256)
def _site(host: str) -> str | None:
    # libpsl includes ICANN and private suffixes. Without it, accept only exact
    # host scope rather than guessing registrable domains from DNS label counts.
    try:
        library = ctypes.util.find_library("psl")
        if not library:
            return None
        psl = ctypes.CDLL(library)
        psl.psl_builtin.restype = ctypes.c_void_p
        psl.psl_registrable_domain.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        psl.psl_registrable_domain.restype = ctypes.c_char_p
        result: bytes | None = psl.psl_registrable_domain(psl.psl_builtin(), host.encode("ascii"))
        return result.decode("ascii") if result else None
    except (OSError, AttributeError, UnicodeError):
        return None


def _partition(value: object, host: str) -> tuple[str, bool | None] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not {"topLevelSite"} <= value.keys() <= {
            "topLevelSite", "hasCrossSiteAncestor"}:
        raise ValueError
    site = value["topLevelSite"]
    if _origin(site) != site:
        raise ValueError
    site_host = urlsplit(site).hostname
    if site_host != host and (not _site(host) or _site(site_host) != _site(host)):
        raise ValueError
    ancestor = value.get("hasCrossSiteAncestor")
    if "hasCrossSiteAncestor" in value and ancestor is not False:
        raise ValueError
    return site, ancestor


def _partition_dict(value: tuple[str, bool | None] | None) -> dict | None:
    if value is None:
        return None
    result: dict[str, str | bool] = {"topLevelSite": value[0]}
    if value[1] is not None:
        result["hasCrossSiteAncestor"] = value[1]
    return result


def _path_matches(path: str, url: str) -> bool:
    requested = urlsplit(url).path or "/"
    return requested == path or (requested.startswith(path) and (
        path.endswith("/") or requested[len(path):].startswith("/")))


@dataclass(frozen=True, repr=False)
class _ClearanceCookie:
    value: str
    domain: str
    path: str
    expiration: float
    host_only: bool
    http_only: bool
    same_site: str
    partition: tuple[str, bool | None] | None

    def export(self) -> dict:
        result = {"name": "cf_clearance", "value": self.value, "domain": self.domain,
                  "path": self.path, "expirationDate": self.expiration, "secure": True,
                  "hostOnly": self.host_only, "httpOnly": self.http_only,
                  "sameSite": self.same_site, "session": False}
        if self.partition is not None:
            result["partitionKey"] = _partition_dict(self.partition)
        return result


@dataclass(frozen=True, repr=False)
class Clearance:
    """Immutable snapshot. Cookie access returns copies; repr never contains secrets."""

    provider_id: str
    origin: str
    user_agent: str = field(repr=False)
    _cookies: tuple[_ClearanceCookie, ...] = field(repr=False)
    _partition_key: tuple[str, bool | None] | None = field(repr=False)
    _max_body_bytes: int = field(default=_MAX_BODY, repr=False)

    def __repr__(self) -> str:
        return "Clearance(<redacted>)"

    @property
    def cookies(self) -> tuple[dict, ...]:
        return tuple(cookie.export() for cookie in self._cookies)

    @property
    def partition_key(self) -> dict | None:
        return _partition_dict(self._partition_key)

    @property
    def expires(self) -> float:
        return min(cookie.expiration for cookie in self._cookies)

    def valid_for(self, policy: BrowserPolicy, url: str) -> bool:
        try:
            policy.validate_url(url)
            return (self.provider_id == policy.provider_id and _origin(url) == self.origin
                    and self.expires > time.time()
                    and all(_path_matches(cookie.path, url) for cookie in self._cookies))
        except (ValueError, TypeError):
            return False

    @classmethod
    def from_export(cls, policy: BrowserPolicy, url: str, reply: dict) -> Clearance | None:
        try:
            policy.validate_url(url)
            origin = _origin(url)
            host = urlsplit(origin).hostname
            if not isinstance(reply, dict) or set(reply) != {"cookies", "user_agent", "partition_key"}:
                return None
            ua, cookies = reply["user_agent"], reply["cookies"]
            if (not isinstance(ua, str) or not 1 <= len(ua) <= 2048
                    or any(not 32 <= ord(char) <= 126 for char in ua) or not ua.strip()
                    or not isinstance(cookies, list) or not 1 <= len(cookies) <= 8):
                return None
            partition = _partition(reply["partition_key"], host)
            validated = []
            identities = set()
            now = time.time()
            for cookie in cookies:
                if not isinstance(cookie, dict) or not set(cookie) <= {
                    "name", "value", "domain", "path", "secure", "httpOnly", "session",
                    "hostOnly", "expirationDate", "sameSite", "storeId", "partitionKey",
                }:
                    return None
                if cookie.get("name") != "cf_clearance" or cookie.get("secure") is not True:
                    return None
                value, domain, path = cookie.get("value"), cookie.get("domain"), cookie.get("path")
                if (not isinstance(value, str) or not 1 <= len(value) <= 4096
                        or not re.fullmatch(r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]+", value)
                        or not isinstance(domain, str) or len(domain) > 254 or domain != domain.lower()
                        or not isinstance(path, str) or not 1 <= len(path) <= 2048
                        or not path.startswith("/") or path.startswith("//")
                        or any(ord(c) < 33 or ord(c) > 126 or c in ";\\?#" for c in path)
                        or not _path_matches(path, url)):
                    return None
                plain_domain = domain[1:] if domain.startswith(".") else domain
                _origin("https://" + plain_domain)
                host_only = cookie.get("hostOnly", not domain.startswith("."))
                if not isinstance(host_only, bool) or (host_only and domain.startswith(".")):
                    return None
                if plain_domain != host:
                    if (host_only or not host.endswith("." + plain_domain)
                            or not _site(host) or _site(plain_domain) != _site(host)):
                        return None
                expiration = cookie.get("expirationDate")
                if (isinstance(expiration, bool) or not isinstance(expiration, (int, float))
                        or not math.isfinite(expiration) or not now < expiration <= now + 400 * 86400
                        or cookie.get("session", False) is not False
                        or not isinstance(cookie.get("httpOnly", False), bool)
                        or cookie.get("sameSite", "unspecified") not in {
                            "unspecified", "no_restriction", "lax", "strict"}
                        or _partition(cookie.get("partitionKey"), host) != partition):
                    return None
                if "storeId" in cookie and (not isinstance(cookie["storeId"], str)
                                             or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", cookie["storeId"])):
                    return None
                identity = domain, path
                if identity in identities:
                    return None
                identities.add(identity)
                validated.append(_ClearanceCookie(value, domain, path, float(expiration), host_only,
                                                  cookie.get("httpOnly", False),
                                                  cookie.get("sameSite", "unspecified"), partition))
            return cls(policy.provider_id, origin, ua, tuple(validated), partition, policy.max_body_bytes)
        except (ValueError, TypeError, OverflowError, UnicodeError):
            return None


def _safe_file(info: os.stat_result) -> bool:
    return (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == 0o600)


class ClearanceStore:
    """Best-effort cache. All path traversal and publication use pinned directory FDs."""

    def __init__(self, root: Path):
        self.root = Path(os.path.abspath(root))

    @contextmanager
    def _directory(self, provider: str, *, create: bool, cancel: Event | None = None):
        if cancel is not None and cancel.is_set():
            raise OSError("Clearance save cancelled")
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            parts = (*self.root.parts[1:], provider)
            for index, part in enumerate(parts):
                if create:
                    if cancel is not None and cancel.is_set():
                        raise OSError("Clearance save cancelled")
                    try:
                        os.mkdir(part, mode=0o700, dir_fd=fd)
                    except FileExistsError:
                        pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd)
                os.close(fd)
                fd = child
                if index >= len(parts) - 2:
                    info = os.fstat(fd)
                    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                        raise OSError("Unsafe clearance directory")
            yield fd
        finally:
            os.close(fd)

    @staticmethod
    def _name(policy: BrowserPolicy, url: str) -> str:
        policy.validate_url(url)
        return hashlib.sha256(_origin(url).encode("ascii")).hexdigest() + ".json"

    @staticmethod
    def _remove(fd: int, name: str) -> None:
        if _safe_file(os.stat(name, dir_fd=fd, follow_symlinks=False)):
            os.unlink(name, dir_fd=fd)

    def load(self, policy: BrowserPolicy, url: str) -> Clearance | None:
        try:
            name = self._name(policy, url)
            with self._directory(policy.provider_id, create=False) as directory:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
                with os.fdopen(fd, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not _safe_file(info):
                        record(policy, url, "cache_miss", reason="unsafe")
                        return None
                    raw = stream.read(_MAX_CACHE + 1)
                item = None
                try:
                    saved_record = json.loads(raw) if len(raw) <= _MAX_CACHE else None
                    if (isinstance(saved_record, dict) and set(saved_record) == {"version", "provider_id", "origin", "payload"}
                            and type(saved_record["version"]) is int and saved_record["version"] == 1
                            and saved_record["provider_id"] == policy.provider_id and saved_record["origin"] == _origin(url)):
                        item = Clearance.from_export(policy, url, saved_record["payload"])
                except (ValueError, UnicodeError, RecursionError):
                    pass
                if item is None:
                    record(policy, url, "cache_miss", reason="invalid_or_expired")
                    self._remove(directory, name)
                else:
                    record(policy, url, "cache_hit", cookies=len(item.cookies),
                           partitioned=item.partition_key is not None)
                return item
        except FileNotFoundError:
            record(policy, url, "cache_miss", reason="missing")
            return None
        except (OSError, ValueError, TypeError):
            record(policy, url, "cache_miss", reason="storage_unavailable")
            return None

    def save(self, policy: BrowserPolicy, url: str, clearance: Clearance, *, cancel: Event | None = None) -> None:
        record(policy, url, "cache_save_attempt")
        try:
            if cancel is not None and cancel.is_set():
                record(policy, url, "cache_save_skipped", reason="cancelled")
                return
            if not isinstance(clearance, Clearance) or not clearance.valid_for(policy, url):
                record(policy, url, "cache_save_skipped", reason="invalid_snapshot")
                return
            name = self._name(policy, url)
            payload = {"cookies": list(clearance.cookies), "user_agent": clearance.user_agent,
                       "partition_key": clearance.partition_key}
            raw = json.dumps({"version": 1, "provider_id": policy.provider_id,
                              "origin": clearance.origin, "payload": payload}, allow_nan=False).encode("utf-8")
            if len(raw) > _MAX_CACHE:
                record(policy, url, "cache_save_skipped", reason="too_large")
                return
            with self._directory(policy.provider_id, create=True, cancel=cancel) as directory:
                try:
                    info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    if not _safe_file(info):
                        record(policy, url, "cache_save_skipped", reason="unsafe")
                        return
                temporary = "." + secrets.token_hex(16) + ".tmp"
                written = None
                published = False
                try:
                    if cancel is not None and cancel.is_set():
                        record(policy, url, "cache_save_skipped", reason="cancelled")
                        return
                    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                 0o600, dir_fd=directory)
                    with os.fdopen(fd, "wb") as stream:
                        os.fchmod(stream.fileno(), 0o600)
                        if cancel is not None and cancel.is_set():
                            record(policy, url, "cache_save_skipped", reason="cancelled")
                            return
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                        written = os.fstat(stream.fileno())
                    if clearance.valid_for(policy, url):
                        if cancel is not None and cancel.is_set():
                            record(policy, url, "cache_save_skipped", reason="cancelled")
                            return
                        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                        published = True
                        if cancel is None or not cancel.is_set():
                            os.fsync(directory)
                finally:
                    try:
                        # Roll back only this write, never a later replacement.
                        if written is not None and cancel is not None and cancel.is_set():
                            try:
                                current = os.stat(name, dir_fd=directory, follow_symlinks=False)
                            except FileNotFoundError:
                                pass
                            else:
                                if (current.st_dev, current.st_ino) == (written.st_dev, written.st_ino):
                                    self._remove(directory, name)
                                    os.fsync(directory)
                    finally:
                        try:
                            os.unlink(temporary, dir_fd=directory)
                        except FileNotFoundError:
                            pass
            cancelled = cancel is not None and cancel.is_set()
            record(policy, url, "cache_saved" if published and not cancelled else "cache_save_skipped",
                   reason="cancelled" if cancelled else "" if published else "invalid_or_expired")
        except (OSError, ValueError, TypeError):
            record(policy, url, "cache_save_skipped",
                   reason="cancelled" if cancel is not None and cancel.is_set() else "storage_unavailable")
            return

    def invalidate(self, policy: BrowserPolicy, url: str) -> None:
        try:
            name = self._name(policy, url)
            with self._directory(policy.provider_id, create=False) as directory:
                self._remove(directory, name)
                record(policy, url, "cache_invalidated")
        except (OSError, ValueError, TypeError):
            return


def _updated_clearance(session: Session, policy: BrowserPolicy, url: str, original: Clearance) -> Clearance | None:
    jar = getattr(getattr(session, "cookies", None), "jar", None)
    if jar is None:
        return original
    cookies: list[dict[str, object]] = []
    for cookie in jar:
        if cookie.name != "cf_clearance":
            continue
        if len(cookies) >= 8:
            return None
        snapshot = {"name": "cf_clearance", "value": cookie.value, "domain": cookie.domain,
                    "path": cookie.path, "secure": cookie.secure, "expirationDate": cookie.expires,
                    "hostOnly": not cookie.domain_specified, "httpOnly": cookie.has_nonstandard_attr("HttpOnly"),
                    "session": cookie.discard}
        # HTTP cookie jars do not model CHIPS. Replay remains bound to the
        # exported origin and carries that same validated partition forward.
        if original.partition_key is not None:
            snapshot["partitionKey"] = original.partition_key
        cookies.append(snapshot)
    return Clearance.from_export(policy, url, {"cookies": cookies, "user_agent": original.user_agent,
                                              "partition_key": original.partition_key})


def request_with_clearance(url: str, clearance: Clearance, *, headers: dict[str, str] | None = None,
                           timeout: float = 15, session: Session | None = None,
                           cancel: Event | None = None, binary: bool = False,
                           max_bytes: int | None = None) -> AccessResponse:
    """Replay only to the bound origin, using Chrome TLS and the exported page UA."""
    from .access import AccessResponse
    from .profiles import BrowserAccessCancelled

    owned = session is None
    response = None
    try:
        policy = BrowserPolicy(clearance.provider_id, [clearance.origin], max_body_bytes=clearance._max_body_bytes)
        if (not clearance.valid_for(policy, url) or isinstance(timeout, bool)
                or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError
        if (type(binary) is not bool or (max_bytes is not None and
                (type(max_bytes) is not int or not 1 <= max_bytes <= 8 * _MAX_BODY))):
            raise ValueError
        outgoing = {}
        for key, value in (headers or {}).items():
            if (not isinstance(key, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key)
                    or not isinstance(value, str) or len(value) > 8192
                    or any(ord(c) < 32 or ord(c) > 126 for c in value)):
                raise ValueError
            if key.lower() in {"host", "authorization", "proxy-authorization"}:
                raise ValueError
            if key.lower() not in {"cookie", "user-agent"}:
                outgoing[key] = value
        outgoing["User-Agent"] = clearance.user_agent
        jar = CookieJar()
        for cookie in clearance._cookies:
            jar.set_cookie(Cookie(0, "cf_clearance", cookie.value, None, False,
                                  cookie.domain, not cookie.host_only, cookie.domain.startswith("."),
                                  cookie.path, True, True, int(cookie.expiration), False,
                                  None, None, {"HttpOnly": None} if cookie.http_only else {}, False))
        from curl_cffi.requests import Session

        if cancel is not None and cancel.is_set():
            raise BrowserAccessCancelled("Browser access cancelled")
        body = bytearray()
        limit = (max_bytes or _MAX_BODY) if binary else min(_MAX_BODY, clearance._max_body_bytes)

        if session is None:
            session = Session(impersonate="chrome", cookies=jar, trust_env=False)
            response = session.get(url, headers=outgoing, timeout=timeout, allow_redirects=False, stream=True)
            for chunk in response.iter_content():
                if len(body) + len(chunk) > limit:
                    raise ValueError
                body.extend(chunk)
        else:
            from curl_cffi.curl import CURL_WRITEFUNC_ERROR
            from curl_cffi.requests.cookies import Cookies

            session.cookies = Cookies(jar)
            aborted = False

            def receive(chunk: bytes) -> int:
                nonlocal aborted
                if (len(body) + len(chunk) > limit
                        or (cancel is not None and cancel.is_set())):
                    aborted = True
                    return CURL_WRITEFUNC_ERROR
                body.extend(chunk)
                return len(chunk)

            # stream=True duplicates the curl handle, losing accepted connections.
            response = session.get(url, headers=outgoing, timeout=timeout, allow_redirects=False,
                                   content_callback=receive)
            if aborted:
                raise ValueError
        if cancel is not None and cancel.is_set():
            raise BrowserAccessCancelled("Browser access cancelled")
        if _origin(str(response.url)) != clearance.origin:
            raise ValueError
        # Binary responses retain exact bytes; only a small sample is needed for
        # challenge detection, including sites that return challenge HTML as 200.
        text = (body[:65536] if binary else body).decode(response.encoding or "utf-8", errors="replace")
        if not binary and len(text.encode("utf-8")) > limit:
            raise ValueError
        updated = _updated_clearance(session, policy, url, clearance)
        return AccessResponse(str(response.url), response.status_code, text, dict(response.headers),
                              clearance=updated, data=bytes(body) if binary else None)
    except BrowserAccessCancelled:
        raise
    except Exception:
        if cancel is not None and cancel.is_set():
            raise BrowserAccessCancelled("Browser access cancelled") from None
        raise ClearanceRequestError("Clearance HTTP request failed") from None
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        if owned and session is not None:
            try:
                session.close()
            except Exception:
                pass
