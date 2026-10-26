"""HLS identity comes from the manifest structure, not opaque CDN extensions."""

import http.client
import io
from urllib.parse import urlsplit

import pytest

from media_cli.media import local_proxy


def read(url):
    parts = urlsplit(url)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=3)
    try:
        connection.request("GET", parts.path)
        response = connection.getresponse()
        return response.status, response.getheader("Content-Type"), response.read()
    finally:
        connection.close()


def upstream(body, final_url, content_type="image/jpeg"):
    response = io.BytesIO(body)
    response.headers = {"Content-Type": content_type, "Content-Length": str(len(body))}
    response.status = 200
    response.geturl = lambda: final_url
    return response


@pytest.mark.parametrize("filename", ["video.jpg", "video.m3u8"])
def test_declared_hls_rewrites_disguised_media_against_final_url(monkeypatch, filename):
    original = "https://source.test/original/" + filename
    final = "https://cdn.test/redirected/video.jpg"
    requests = []
    def open(request, **kwargs):
        requests.append(request.full_url)
        if request.full_url == original:
            return upstream(b"#EXTM3U\n#EXTINF:10,\nsegment.jpg\n#EXT-X-ENDLIST\n", final)
        assert request.full_url == "https://cdn.test/redirected/segment.jpg"
        return upstream(b"media-payload", request.full_url)
    monkeypatch.setattr(local_proxy.urllib.request, "urlopen", open)
    url, server = local_proxy.start_local_proxy(original, "https://senshi.to/", stream_type="hls")
    try:
        status, content_type, body = read(url)
        assert status == 200 and "mpegurl" in content_type.lower()
        segment = next(line for line in body.decode().splitlines() if not line.startswith("#"))
        assert read("http://127.0.0.1:" + str(server.server_port) + segment)[2] == b"media-payload"
        assert requests == [original, "https://cdn.test/redirected/segment.jpg"]
    finally:
        local_proxy.stop_local_proxy(server)


def test_master_tags_preserve_child_playlist_type_with_jpg_urls(monkeypatch):
    root = "https://cdn.test/master.m3u8"
    def open(request, **kwargs):
        url = request.full_url
        if url == root:
            return upstream(b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nchild.jpg\n', root,
                            "application/vnd.apple.mpegurl")
        assert url == "https://cdn.test/child.jpg"
        return upstream(b"#EXTM3U\n#EXTINF:10,\nsegment.jpg\n#EXT-X-ENDLIST\n", url)
    monkeypatch.setattr(local_proxy.urllib.request, "urlopen", open)
    url, server = local_proxy.start_local_proxy(root, "https://senshi.to/", stream_type="hls")
    try:
        body = read(url)[2].decode()
        child = next(line for line in body.splitlines() if not line.startswith("#"))
        _, content_type, content = read("http://127.0.0.1:" + str(server.server_port) + child)
        assert "mpegurl" in content_type.lower()
        assert content.startswith(b"#EXTM3U") and b"\nsegment.jpg" not in content
    finally:
        local_proxy.stop_local_proxy(server)
