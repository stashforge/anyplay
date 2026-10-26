import http.client
import io
import urllib.error
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from curl_cffi import CurlOpt

from media_cli.media import local_proxy


def read(url):
    parts = urlsplit(url)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=3)
    try:
        connection.request('GET', parts.path)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def upstream(body, url):
    response = io.BytesIO(body)
    response.headers = {'Content-Type': 'application/vnd.apple.mpegurl' if body.startswith(b'#EXTM3U') else 'application/octet-stream'}
    response.status = 200
    response.geturl = lambda: url
    return response


@pytest.mark.parametrize('extension', ['m3u8', 'jpg'])
def test_ipv4_combined_proxy_keeps_video_audio_keys_and_segments_on_same_policy(monkeypatch, extension):
    fetched = []
    def open(request, **kwargs):
        url = request.full_url
        fetched.append(url)
        if url.endswith('video.' + extension) or url.endswith('audio.' + extension):
            response = upstream(b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"\n#EXTINF:1,\nsegment\n#EXT-X-ENDLIST\n', url)
            if extension == 'jpg':
                response.headers['Content-Type'] = 'image/jpeg'
            return response
        return upstream(b'payload', url)
    monkeypatch.setattr(local_proxy, 'urlopen_ipv4', open, raising=False)
    monkeypatch.setattr(local_proxy.urllib.request, 'urlopen', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('unbound request')))
    url, server = local_proxy.start_local_proxy(
        'https://cdn.test/video.' + extension, '', stream_type='hls', ip_version=4,
        audio_url='https://cdn.test/audio.' + extension)
    try:
        master = read(url)[1].decode()
        import m3u8
        manifest = m3u8.loads(master, uri=url)
        children = [manifest.playlists[0].absolute_uri, manifest.media[0].absolute_uri]
        for child in children:
            playlist = m3u8.loads(read(child)[1].decode(), uri=child)
            assert read(playlist.keys[0].absolute_uri)[1] == b'payload'
            assert read(playlist.segments[0].absolute_uri)[1] == b'payload'
        assert set(fetched) == {'https://cdn.test/video.' + extension, 'https://cdn.test/audio.' + extension,
                                'https://cdn.test/key', 'https://cdn.test/segment'}
    finally:
        local_proxy.stop_local_proxy(server)


def test_ipv4_curl_fallback_retains_family_and_tls_verification(monkeypatch):
    def fail(request, **kwargs):
        raise urllib.error.HTTPError(request.full_url, 403, 'Forbidden', {}, None)
    calls = []
    def request(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(headers={'Content-Type': 'video/mp2t'}, status_code=200,
                               iter_content=lambda *a: iter([b'payload']), close=lambda: None)
    monkeypatch.setattr(local_proxy, 'urlopen_ipv4', fail, raising=False)
    monkeypatch.setattr(local_proxy, 'optional_curl_requests', lambda: SimpleNamespace(request=request))
    url, server = local_proxy.start_local_proxy('https://cdn.test/segment.ts', '', ip_version=4)
    try:
        assert read(url) == (200, b'payload')
        assert calls[0]['curl_options'] == {CurlOpt.IPRESOLVE: 1, CurlOpt.PROXY: ''}
        assert calls[0]['verify'] is True
        assert 'trust_env' not in calls[0]  # Not a request-level curl_cffi option.
    finally:
        local_proxy.stop_local_proxy(server)
