"""Native extractors accept the shared resolver's request context."""

import pytest

from media_cli.extractors.doodstream import DoodExtractor
from media_cli.extractors.streamwish import StreamWishExtractor
from media_cli.media import resolver


@pytest.mark.parametrize('kind', ['dood', 'streamwish'])
def test_resolver_uses_native_with_headers_without_ytdlp(monkeypatch, kind):
    calls = []
    original_headers = {'User-Agent': 'provider-agent'}
    if kind == 'dood':
        url = 'https://dood.watch/e/episode'
        extractor_type = DoodExtractor

        def fetch(self, url, **kwargs):
            calls.append((url, kwargs))
            if '/pass_md5/' in url:
                return 'https://cdn.example.test/file/'
            return "$.get('/pass_md5/path/token', function(data){});"
    else:
        url = 'https://streamwish.to/e/episode'
        extractor_type = StreamWishExtractor

        def fetch(self, url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith('.m3u8'):
                return '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=2000000,RESOLUTION=1920x1080\n1080.m3u8\n'
            return '<script>var player = {file: "https://cdn.example.test/master.m3u8"};</script>'

    monkeypatch.setattr(extractor_type, 'fetch_page', fetch)
    monkeypatch.setattr(resolver, 'resolve_ytdlp_embed', lambda *a, **kw: pytest.fail('Native contract failure fell back to yt-dlp'))
    result = resolver.resolve_source({
        'sourceName': 'Native example', 'sourceUrl': url, 'headers': original_headers,
        'referer': 'https://provider.example.test/episode', 'subtitles': [{'url': 'sub.vtt'}],
    }, silent=True)
    assert result
    assert calls[0][1]['referer'] == 'https://provider.example.test/episode'
    assert all(call[1]['headers']['User-Agent'] == 'provider-agent' for call in calls)
    assert all(s['headers']['User-Agent'] == 'provider-agent' for s in result)
    assert all(s['subtitles'] == [{'url': 'sub.vtt'}] for s in result)
    assert original_headers == {'User-Agent': 'provider-agent'}
