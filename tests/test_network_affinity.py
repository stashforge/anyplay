import http.client
import socket
from unittest.mock import Mock, patch

from curl_cffi import CurlOpt

from media_cli.services import network_affinity as network


def test_ipv4_connection_filters_dns_without_mutating_global_resolver():
    resolver = socket.getaddrinfo
    sock = Mock()
    with patch.object(network.socket, 'getaddrinfo', return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.1', 443)),
    ]) as lookup, patch.object(network.socket, 'socket', return_value=sock):
        assert network._create_ipv4_connection(('cdn.test', 443), 3, ('0.0.0.0', 0)) is sock
    lookup.assert_called_once_with('cdn.test', 443, socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout.assert_called_once_with(3)
    sock.bind.assert_called_once_with(('0.0.0.0', 0))
    sock.connect.assert_called_once_with(('192.0.2.1', 443))
    assert socket.getaddrinfo is resolver


def test_ipv4_connection_closes_failed_socket_and_never_falls_back_to_ipv6():
    sock = Mock()
    sock.connect.side_effect = OSError('unavailable')
    with patch.object(network.socket, 'getaddrinfo', return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.1', 443)),
    ]), patch.object(network.socket, 'socket', return_value=sock):
        import pytest
        with pytest.raises(OSError):
            network._create_ipv4_connection(('cdn.test', 443), 3)
    sock.close.assert_called_once()


def test_ipv4_https_keeps_tls_hostname_and_only_overrides_own_connection():
    original = http.client.HTTPSConnection('other.test')
    connection = network._IPv4HTTPSConnection('cdn.test')
    assert connection.host == 'cdn.test'
    assert connection._create_connection is network._create_ipv4_connection
    assert original._create_connection is socket.create_connection


def test_curl_and_proxy_options_are_explicit_and_default_streams_unchanged():
    assert network.ipv4_curl_options() == {CurlOpt.IPRESOLVE: 1, CurlOpt.PROXY: ''}
    assert network.bound_proxy_options({}) == {}
    assert network.bound_proxy_options({'ip_version': 6}) == {}
    stream = {'ip_version': 4, 'audio_url': 'https://cdn.test/audio',
              'audio_tracks': [{'url': 'https://cdn.test/audio'}]}
    assert network.bound_proxy_options(stream) == stream


def test_shared_source_resolution_preserves_network_policy():
    from media_cli.media.resolver import resolve_source
    source = {'link': 'https://cdn.test/video.m3u8', 'type': 'hls', 'ip_version': 4}
    assert resolve_source(source)[0]['ip_version'] == 4


def test_queued_download_binds_both_inputs_and_removes_remote_audio(monkeypatch):
    from media_cli.media import download_worker, local_proxy
    item = download_worker.DownloadItem('id', 'Example', '1', '/tmp/test.mkv',
        url='https://cdn.test/video.m3u8', audio_url='https://cdn.test/audio.m3u8')
    item.ip_version = 4
    server = object()
    def start(url, referer, headers, **kwargs):
        assert kwargs['ip_version'] == 4
        assert kwargs['audio_url'] == item.audio_url
        return 'http://127.0.0.1/master.m3u8', server
    monkeypatch.setattr(local_proxy, 'start_local_proxy', start)
    download_worker._prepare_bound_network(item)
    assert item.url == 'http://127.0.0.1/master.m3u8'
    assert item.proxy_server is server
    assert item.audio_url == '' and item.audio_tracks == []


def test_queued_download_default_networking_is_unchanged(monkeypatch):
    from media_cli.media import download_worker, local_proxy
    item = download_worker.DownloadItem('id', 'Example', '1', '/tmp/test.mkv', url='https://cdn.test/video')
    monkeypatch.setattr(local_proxy, 'start_local_proxy', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('unexpected proxy')))
    download_worker._prepare_bound_network(item)
    assert item.url == 'https://cdn.test/video'


def test_queued_download_retry_rebuilds_from_original_remote_inputs(monkeypatch):
    from media_cli.media import download_worker, local_proxy
    item = download_worker.DownloadItem('id', 'Example', '1', '/tmp/test.mkv',
        url='https://cdn.test/video.m3u8', audio_url='https://cdn.test/audio.m3u8', ip_version=4)
    calls = []
    def start(url, referer, headers, **kwargs):
        calls.append((url, kwargs['audio_url']))
        return 'http://127.0.0.1/master.m3u8', object()
    monkeypatch.setattr(local_proxy, 'start_local_proxy', start)
    download_worker._prepare_bound_network(item)
    item.proxy_server = None
    download_worker._prepare_bound_network(item)
    assert calls == [('https://cdn.test/video.m3u8', 'https://cdn.test/audio.m3u8')] * 2


def test_bound_split_download_cannot_use_playlist_only_engine(monkeypatch, tmp_path):
    from media_cli.media import download_worker, local_proxy
    item = download_worker.DownloadItem('id', 'Example', '1', str(tmp_path / 'test.mkv'),
        url='https://cdn.test/video.m3u8', audio_url='https://cdn.test/audio.m3u8',
        ip_version=4, engine='aria2c', download_sub_langs='none',
        status=download_worker.DownloadStatus.DOWNLOADING)
    monkeypatch.setattr(local_proxy, 'start_local_proxy', lambda *a, **kw: ('http://127.0.0.1/master.m3u8', object()))
    worker = download_worker.DownloadWorker(download_dir=str(tmp_path))
    called = []
    monkeypatch.setattr(worker, '_download_aria2c', lambda item: called.append(True))
    monkeypatch.setattr(download_worker, '_stop_proxy_server', lambda *a, **kw: None)
    try:
        worker._process_item(item)
        assert not called
        assert 'ffmpeg is required' in item.error
    finally:
        worker.stop()
