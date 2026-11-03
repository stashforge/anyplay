"""Modern loading-page embeds resolve through their native metadata API."""

import json
from urllib.parse import parse_qs, urlsplit

import pytest

from media_cli.extractors.streamwish import StreamWishExtractor


@pytest.mark.parametrize('delivery', ['Tiktok', 'In-House'])
def test_loading_app_uses_api_and_shared_hls_parser(monkeypatch, delivery):
    extractor = StreamWishExtractor()
    calls = []
    data = {
        'player': {}, 'hlsVideoTiktok': '/hls/video/master.m3u8',
        'source': 'https://source.example.test/v4/signed/master.m3u8',
        'cf': 'https://cf.example.test/protected/master.txt',
        'streamingConfig': json.dumps({'order': ['Cloudflare', delivery], 'adjust': {
            'Tiktok': {'domain': 'video.example.test', 'params': {'v': 'version'}},
        }}),
        'subtitle': [{'file': '/captions/en.vtt', 'label': 'English', 'default': True}],
    }

    def fetch(url, **kwargs):
        calls.append((url, kwargs))
        if '/api/v1/video?' in url:
            query = parse_qs(urlsplit(url).query)
            assert query == {'id': ['video-id'], 'w': ['1920'], 'h': ['1080'], 'r': ['provider.example.test']}
            return 'encrypted'
        if 'master.m3u8' in url:
            assert 'cf.example.test' not in url  # Protected segment-token delivery is not yet supported.
            if delivery == 'Tiktok':
                assert '/hlsmod/video.example.test/' in url
                assert parse_qs(urlsplit(url).query) == {'v': ['version']}
            return '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1920x1080\n1080.m3u8\n'
        return '<html><title>Loading...</title><script type="module" src="/assets/index-abc.js"></script></html>'

    monkeypatch.setattr(extractor, 'fetch_page', fetch)
    monkeypatch.setattr('media_cli.media.player_metadata.decode_player_metadata', lambda body: data)
    rows = extractor.extract('https://seekplayer.vip/#video-id&extra=1',
                             headers={'User-Agent': 'provider-agent'}, referer='https://provider.example.test/episode')
    assert len(rows) == 1
    assert rows[0]['resolution'].startswith('1080p')
    assert len(calls) == 3
    assert rows[0]['subtitles'][0]['url'] == 'https://seekplayer.vip/captions/en.vtt'
    assert rows[0]['headers']['User-Agent'] == 'provider-agent'


def test_existing_packed_inline_parser_does_not_require_metadata_crypto(monkeypatch):
    extractor = StreamWishExtractor()
    monkeypatch.setattr(extractor, 'fetch_page', lambda *a, **kw: '<script>file:"https://cdn.example.test/master.m3u8"</script>')
    monkeypatch.setattr(extractor, 'extract_m3u8', lambda url, **kw: [{'link': url}])
    assert extractor.extract('https://streamwish.to/e/video') == [{'link': 'https://cdn.example.test/master.m3u8'}]


def test_shared_decoder_reads_verified_transport_ciphertext():
    from media_cli.media.player_metadata import decode_player_metadata
    ciphertext = ('bf98bd9b69970f3209cdde1c492dbb3b28727000db02a55c2dce0ade519fc499'
                  '1c6400cbf5e659a2f9e788d615b2c2c16a44dd095cd31b51988cc1cfd6696038')
    assert decode_player_metadata(ciphertext) == {'player': {}, 'source': 'https://cdn.example.test/master.m3u8'}


def test_invalid_encrypted_metadata_has_private_bounded_error():
    from media_cli.media.player_metadata import decode_player_metadata
    with pytest.raises(ValueError, match='Unable to decode player metadata') as caught:
        decode_player_metadata('https://private.example.test/?token=secret')
    assert 'secret' not in str(caught.value)


@pytest.mark.parametrize('external', [False, True])
def test_api_subtitle_map_and_optional_embed_tracks_are_preserved(monkeypatch, external):
    extractor = StreamWishExtractor()
    metadata = {'player': {'allowExternal': external, 'defaultSubtitle': 'en'},
                'subtitle': {'French': '/subs/fr.vtt#fr', 'English': '/subs/en.vtt#en'},
                'source': '/master.m3u8', 'streamingConfig': {'order': ['In-House']}}
    monkeypatch.setattr('media_cli.media.player_metadata.decode_player_metadata', lambda _: metadata)
    monkeypatch.setattr(extractor, 'fetch_page', lambda *a, **kw:
                        '#EXTM3U\n#EXT-X-TARGETDURATION:10\nseg.ts\n' if 'master.m3u8' in a[0] else 'encrypted')
    from urllib.parse import quote
    embedded = quote(json.dumps([{'label': 'Indonesian', 'language': 'id', 'url': '/subs/id.vtt'}]))
    rows = extractor._extract_app('https://seekplayer.vip/#video&subs='+embedded,
                                 'https://seekplayer.vip', 'https://provider.test/', {}, 'Wish', 2, [])
    assert rows
    tracks = rows[0]['subtitles']
    assert [t['language'] for t in tracks] == (['fr', 'en', 'id'] if external else ['fr', 'en'])
    assert next(t for t in tracks if t.get('default'))['language'] == 'en'
    assert tracks[1]['url'] == 'https://seekplayer.vip/subs/en.vtt#en'
    assert metadata['subtitle'] == {'French': '/subs/fr.vtt#fr', 'English': '/subs/en.vtt#en'}
