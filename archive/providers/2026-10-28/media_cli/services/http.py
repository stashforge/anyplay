"""Shared HTTP transport, TLS contexts, and media probes."""

import http.client
import io
import re
import ssl
import time
import urllib.parse
import urllib.request
from queue import Empty, Queue

from ..core.api import read_json_response
from ..media.urls import validate_optional_referer, validate_stream_url

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE
SSL_CTX_SECURE = ssl.create_default_context()

ANILIST_TIMEOUT = 8
API_BASE = "https://api.mkissa.net/api"
CLOCK_BASE = "allanime.day"
REFERER = "https://mkissa.to/"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
BASE_HDRS = {
    "User-Agent": UA,
    "Origin": "https://mkissa.to",
    "Referer": REFERER,
    "sec-ch-ua-platform": '"Windows"',
}


class _PooledHTTPResponse:
    """urllib-compatible response wrapper around connection pool payload."""

    def __init__(self, status: int, headers: dict[str, str], body_bytes: bytes):
        self.status = status
        self.headers = headers
        self._body = body_bytes
        self._io = io.BytesIO(body_bytes)

    def read(self, *args):
        return self._io.read(*args)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class HTTPSConnectionPool:
    """Thread-safe persistent HTTPS connection pool using only standard library."""

    def __init__(self, max_per_host: int = 8, timeout: int = 8):
        self.max_per_host = max_per_host
        self.timeout = timeout
        self._pools: dict[tuple[str, int], Queue] = {}

    def _get_conn(self, host: str, port: int = 443, timeout: int | None = None) -> http.client.HTTPSConnection:
        key = (host, port)
        if key not in self._pools:
            self._pools[key] = Queue(maxsize=self.max_per_host)

        pool = self._pools[key]
        tout = timeout or self.timeout
        while not pool.empty():
            try:
                conn, last_used = pool.get_nowait()
                if time.time() - last_used < 45 and getattr(conn, "sock", None) is not None:
                    return conn
                conn.close()
            except Empty:
                break

        return http.client.HTTPSConnection(host, port, timeout=tout, context=SSL_CTX_SECURE)

    def _release_conn(self, host: str, port: int, conn: http.client.HTTPSConnection) -> None:
        key = (host, port)
        pool = self._pools.get(key)
        if pool and not pool.full():
            try:
                pool.put_nowait((conn, time.time()))
                return
            except Exception:
                pass
        conn.close()

    def request(
        self,
        method: str,
        url: str,
        data: bytes | str | None = None,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"

        req_headers = {
            "Host": host,
            "Connection": "keep-alive",
            "User-Agent": UA,
        }
        if headers:
            for k, v in headers.items():
                req_headers[k] = v

        body = data if isinstance(data, (bytes, type(None))) else str(data).encode("utf-8")

        conn = self._get_conn(host, port, timeout=timeout)
        try:
            conn.request(method, path, body=body, headers=req_headers)
            resp = conn.getresponse()
            body_bytes = resp.read()
            resp_headers = dict(resp.getheaders())
            self._release_conn(host, port, conn)
            return resp.status, resp_headers, body_bytes
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            # Reconnect once on broken/stale keep-alive socket
            conn = http.client.HTTPSConnection(host, port, timeout=timeout or self.timeout, context=SSL_CTX_SECURE)
            conn.request(method, path, body=body, headers=req_headers)
            resp = conn.getresponse()
            body_bytes = resp.read()
            resp_headers = dict(resp.getheaders())
            self._release_conn(host, port, conn)
            return resp.status, resp_headers, body_bytes


_GLOBAL_POOL = HTTPSConnectionPool()


def anilist_urlopen(request, data=None):
    url = request.full_url
    method = request.get_method()
    headers = dict(request.headers)
    body = data if data is not None else request.data
    status, resp_hdrs, body_bytes = _GLOBAL_POOL.request(method, url, data=body, headers=headers, timeout=ANILIST_TIMEOUT)
    return _PooledHTTPResponse(status, resp_hdrs, body_bytes)


def request_json(url, data=None, extra_hdrs=None, timeout=8):
    headers = {**BASE_HDRS, **(extra_hdrs or {})}
    if data:
        headers["Content-Type"] = "application/json"
    method = "POST" if data else "GET"
    status, resp_hdrs, body_bytes = _GLOBAL_POOL.request(method, url, data=data, headers=headers, timeout=timeout)
    resp = _PooledHTTPResponse(status, resp_hdrs, body_bytes)
    return read_json_response(resp)


def is_alive(url, referer="", timeout=6):
    try:
        url = validate_stream_url(url)
        referer = validate_optional_referer(referer)
    except ValueError:
        return False
    headers = {"User-Agent": UA, "Range": "bytes=0-0"}
    if referer:
        headers["Referer"] = referer
    for method in ("GET", "HEAD"):
        try:
            request = urllib.request.Request(
                url, headers=headers, method=method
            )
            with urllib.request.urlopen(
                request,
                context=SSL_CTX,
                timeout=timeout,
            ) as response:
                return 200 <= response.status < 400
        except Exception:
            continue
    return False


def get_size(url, referer="", timeout=6):
    try:
        url = validate_stream_url(url)
        referer = validate_optional_referer(referer)
    except ValueError:
        return None
    headers = {"User-Agent": UA, "Range": "bytes=0-0"}
    if referer:
        headers["Referer"] = referer
    try:
        request = urllib.request.Request(
            url, headers=headers, method="GET"
        )
        with urllib.request.urlopen(
            request,
            context=SSL_CTX,
            timeout=timeout,
        ) as response:
            if 200 <= response.status < 400:
                match = re.search(
                    r"/(\d+)",
                    response.headers.get("Content-Range", ""),
                )
                return int(match.group(1)) if match else None
    except Exception:
        pass
    return None
