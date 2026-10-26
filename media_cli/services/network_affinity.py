"""Per-stream address-family policy, without global socket or DNS changes."""

import http.client
import socket
import urllib.request


def ipv4_curl_options():
    from curl_cffi import CurlOpt
    return {CurlOpt.IPRESOLVE: 1, CurlOpt.PROXY: ""}


def bound_proxy_options(stream):
    if stream.get("ip_version") != 4:
        return {}
    return {"ip_version": 4, "audio_url": stream.get("audio_url"),
            "audio_tracks": stream.get("audio_tracks")}


def _create_ipv4_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
    host, port = address
    for family, kind, protocol, _, target in socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM):
        connection = socket.socket(family, kind, protocol)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                connection.settimeout(timeout)
            if source_address:
                connection.bind(source_address)
            connection.connect(target)
            return connection
        except OSError:
            connection.close()
    raise OSError("IPv4 connection unavailable")


class _IPv4HTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _create_ipv4_connection


class _IPv4HTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _create_ipv4_connection


class _IPv4HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, request):
        return self.do_open(_IPv4HTTPConnection, request)


class _IPv4HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(_IPv4HTTPSConnection, request, context=self._context)


def urlopen_ipv4(request, *, context, timeout):
    # Keep redirects on the same handlers, TLS hostname and certificate checks.
    # Ambient proxies could change the token's public IP, so are not inherited.
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _IPv4HTTPHandler(), _IPv4HTTPSHandler(context=context),
    )
    return opener.open(request, timeout=timeout)
