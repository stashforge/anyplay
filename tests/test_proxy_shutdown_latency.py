"""Idle proxy shutdown should not wait for the default half-second poll."""

import http.client
import time

import pytest

from media_cli.media import local_proxy


@pytest.mark.parametrize('kind', ['video', 'split_audio', 'content'])
def test_idle_shared_proxy_stops_promptly_and_closes_socket(monkeypatch, kind, record_property):
    monkeypatch.setattr(local_proxy.urllib.request, 'urlopen',
                        lambda *a, **kw: pytest.fail('Shutdown check must not contact upstream'))
    if kind == 'content':
        _, server = local_proxy.start_local_content_server(b'payload', 'subtitle.vtt', 'text/vtt')
    else:
        _, server = local_proxy.start_local_proxy('https://example.test/video.m3u8', '',
            audio_url='https://example.test/audio.m3u8' if kind == 'split_audio' else None)
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=2)
    try:
        # A rejected local request proves the serve loop has started, without fetching media.
        connection.request('GET', '/not-an-authorized-proxy-path')
        response = connection.getresponse()
        assert response.status in (403, 404)
        response.read()
        started = time.monotonic()
        local_proxy.stop_local_proxy(server)
        elapsed = time.monotonic() - started
        record_property('shutdown_ms', round(elapsed * 1000, 2))
        assert server.socket.fileno() == -1
        assert elapsed < 0.3, f'Idle proxy shutdown took {elapsed:.3f}s'
    finally:
        connection.close()
        local_proxy.stop_local_proxy(server)
