"""Extraction headers are part of the playable stream, not disposable metadata."""

import pytest

from media_cli.media.ytdlp import streams_from_ytdlp_data


@pytest.mark.parametrize('format_headers', [None, {'referer': 'https://odysee.com/', 'User-Agent': 'format-agent'}])
def test_variant_and_adaptive_keep_extractor_headers(format_headers):
    item = {
        'url': 'https://secure.odycdn.com/video/1080.m3u8',
        'manifest_url': 'https://secure.odycdn.com/video/master.m3u8',
        'width': 1920, 'height': 804, 'vcodec': 'h264', 'acodec': 'aac',
        'protocol': 'm3u8_native',
    }
    if format_headers is not None:
        item['http_headers'] = format_headers
    data = {'http_headers': {'Referer': 'https://parent.example/', 'User-Agent': 'parent-agent', 'Accept': '*/*'},
            'formats': [item]}
    streams = streams_from_ytdlp_data(data, url='https://odysee.com/@channel/video', name='Odysee', priority=1)
    assert len(streams) == 2
    for stream in streams:
        headers = {key.lower(): value for key, value in stream['headers'].items()}
        assert headers['user-agent'] == ('format-agent' if format_headers else 'parent-agent')
        assert headers['accept'] == '*/*'
        assert stream['referer'] == ('https://odysee.com/' if format_headers else 'https://parent.example/')
    assert data['http_headers']['Referer'] == 'https://parent.example/'
    if format_headers:
        assert item['http_headers'] == format_headers


def test_invalid_or_transport_owned_headers_do_not_reach_playback():
    data = {'http_headers': {'User-Agent': 'safe-agent', 'Host': 'wrong.example', 'Connection': 'keep-alive',
                             'X-Bad': 'value\r\nInjected: yes'},
            'formats': [{'url': 'https://cdn.example.com/video.mp4', 'width': 1920, 'height': 1080,
                         'vcodec': 'h264', 'acodec': 'aac', 'protocol': 'https'}]}
    streams = streams_from_ytdlp_data(data, url='https://example.com/embed', name='Example', priority=1)
    assert streams[0]['headers'] == {'User-Agent': 'safe-agent'}
    assert streams[0]['referer'] == ''


def test_mpv_header_handoff_preserves_commas_inside_values(monkeypatch):
    from types import SimpleNamespace

    from media_cli.playback.mpv import MpvIpc

    player = MpvIpc()
    commands = []
    monkeypatch.setattr(player, 'start', lambda: None)
    monkeypatch.setattr(player, 'send_cmd', lambda *args: commands.append(args))

    def recv(size):
        raise BlockingIOError

    player.client = SimpleNamespace(recv=recv)
    player.load('https://cdn.example.com/master.m3u8', 'Test',
                {'User-Agent': 'extractor-agent', 'Accept': 'text/html,application/xhtml+xml', 'Sec-Fetch-Mode': 'navigate'},
                'https://odysee.com/')
    assert ('set_property', 'http-header-fields',
            ['Accept: text/html,application/xhtml+xml', 'Sec-Fetch-Mode: navigate']) in commands
    assert ('set_property', 'referrer', 'https://odysee.com/') in commands
    assert ('set_property', 'user-agent', 'extractor-agent') in commands
