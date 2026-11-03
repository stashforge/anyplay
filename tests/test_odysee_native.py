"""Odysee's API/playlist is the primary extractor, with signed URLs intact."""

from urllib.parse import parse_qs, urlsplit
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from media_cli.extractors import find_extractor
from media_cli.extractors.odysee import OdyseeExtractor
from media_cli.media import resolver


MASTER = '''#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1567500,RESOLUTION=1920x804
1080.m3u8?token=video
#EXT-X-STREAM-INF:BANDWIDTH=2890800,RESOLUTION=1720x720
720.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=655600,RESOLUTION=860x360
360.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=215600,RESOLUTION=344x144
144.m3u8
'''


@pytest.mark.parametrize('path,uri', [
    ('/@channel:1/video:4', 'lbry://@channel#1/video#4'),
    ('/%24/embed/%40channel%3A1/video%3A4', 'lbry://@channel#1/video#4'),
    ('/$/download/video/abcd', 'lbry://video#abcd'),
])
def test_native_api_preserves_signature_and_all_advertised_qualities(monkeypatch, path, uri):
    extractor = OdyseeExtractor()
    calls = []
    stream_url = 'https://player.odycdn.com/api/v4/streams/free/video/claim?signature=a%2Bb'

    def rpc(method, params):
        calls.append((method, params))
        if method == 'resolve':
            return {uri: {'claim_id': 'claim', 'value': {'stream_type': 'video',
                    'video': {'width': 1920, 'height': 804, 'duration': 1371}}}}
        return {'streaming_url': stream_url}

    def read(url, *, method='GET', headers):
        assert headers['Referer'] == 'https://odysee.com/'
        if method == 'HEAD':
            return 'https://cdn.example.test/video/master.m3u8?token=master', 'application/x-mpegurl', ''
        if 'master.m3u8' in url:
            return url, 'application/x-mpegurl', MASTER
        assert '/api/v3/' in url
        return url, 'video/mp4', ''

    monkeypatch.setattr(extractor, '_rpc', rpc)
    monkeypatch.setattr(extractor, '_read_stream', read)
    streams = extractor.extract('https://odysee.com'+path+'?signature=a%2Bb&signature_ts=123',
                                name='Odysee', priority=1, subtitles=[{'url': 'sub.vtt'}])
    assert calls == [('resolve', {'urls': [uri]}),
                     ('get', {'uri': uri, 'signature': 'a+b', 'signature_ts': '123'})]
    assert [s['_quality_rank'] for s in streams if s['type'] == 'hls' and s['resolution'] != 'Adaptive'] == [1080, 720, 360, 144]
    concrete = next(s for s in streams if s['resolution'].startswith('360p'))
    assert concrete['link'] == 'https://cdn.example.test/video/360.m3u8'
    original = next(s for s in streams if s['type'] == 'mp4')
    assert original['_is_original'] is True
    assert parse_qs(urlsplit(original['link']).query)['signature'] == ['a+b']
    assert 'Original' in original['resolution']
    assert all(s.get('subtitles') == [{'url': 'sub.vtt'}] for s in streams)
    adaptive = next(s for s in streams if s['resolution'] == 'Adaptive')
    assert adaptive['_max_quality_rank'] == 1080


def test_exact_host_routing_and_lazy_native_selection():
    assert isinstance(find_extractor('https://odysee.com/@channel:1/video:4'), OdyseeExtractor)
    assert find_extractor('https://odysee.com.evil.example/video') is None


@pytest.mark.parametrize('native_fails', [False, True])
def test_source_resolver_prefers_native_and_retains_ytdlp_fallback(monkeypatch, native_fails):
    called = []

    def extract(self, *args, **kwargs):
        if native_fails:
            raise RuntimeError('API unavailable')
        return [{'link': 'https://cdn.example.test/1080.m3u8', 'resolution': '1080p'}]

    def fallback(*args, **kwargs):
        called.append(True)
        return [{'link': 'fallback'}]

    monkeypatch.setattr(OdyseeExtractor, 'extract', extract)
    monkeypatch.setattr(resolver, 'resolve_ytdlp_embed', fallback)
    result = resolver.resolve_source({'sourceName': 'Odysee', 'sourceUrl': 'https://odysee.com/@c:1/v:2'}, silent=True)
    assert bool(called) is native_fails
    assert result[0]['link'] == ('fallback' if native_fails else 'https://cdn.example.test/1080.m3u8')


def test_original_media_read_is_bounded_and_head_does_not_read_body(monkeypatch):
    from media_cli.extractors import odysee
    reads = []

    def open_request(request, **kwargs):
        return nullcontext(SimpleNamespace(
            url='https://cdn.example.test/original.mp4?signature=kept',
            headers={'Content-Type': 'video/mp4'},
            read=lambda size: reads.append(size) or b'\x00\x00\x00\x18ftypisom',
        ))

    monkeypatch.setattr(odysee.urllib.request, 'urlopen', open_request)
    extractor = OdyseeExtractor()
    extractor._read_stream('https://cdn.example.test/media', headers={}, method='HEAD')
    assert reads == []
    extractor._read_stream('https://cdn.example.test/media', headers={})
    assert reads == [1024]


def test_api_errors_do_not_expose_signed_request_details(monkeypatch):
    from media_cli.extractors import odysee

    def unavailable(*args, **kwargs):
        raise RuntimeError('https://example.test/?signature=private')

    monkeypatch.setattr(odysee.urllib.request, 'urlopen', unavailable)
    with pytest.raises(RuntimeError, match='Odysee get request unavailable') as caught:
        OdyseeExtractor()._rpc('get', {'uri': 'lbry://video#1', 'signature': 'private'})
    assert 'private' not in str(caught.value)


@pytest.mark.parametrize('target', ['1080p', 'best'])
def test_original_outranks_equal_resolution_transcode(target):
    from media_cli.media.sources import stream_quality_preference_key
    original = {'resolution': '1080p (1920×804) Original', '_is_original': True}
    transcode = {'resolution': '1080p (1920×804)'}
    assert stream_quality_preference_key(original, target) < stream_quality_preference_key(transcode, target)
    assert stream_quality_preference_key({'resolution': '720p'}, '720p') < stream_quality_preference_key(original, '720p')
