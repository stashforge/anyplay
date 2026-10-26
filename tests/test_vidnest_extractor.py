import io
import urllib.parse
import urllib.request

import pytest

from media_cli.extractors.vidnest import VidnestExtractor


def test_vidnest_posts_embed_form_and_extracts_real_qualities(monkeypatch):
    requests = []

    def open_mock(request, **kwargs):
        requests.append(request)
        assert kwargs['timeout'] == 8
        return io.BytesIO(b'sources: [{file: "https://cdn.example/video.mp4", label: "1920x1080"},'
                          b'{file: "/video.m3u8", label: "720p"}]')

    monkeypatch.setattr(urllib.request, 'urlopen', open_mock)
    streams = VidnestExtractor().extract('https://vidnest.io/embed-abc.html',
                                        referer='https://provider.example/show', priority=4)
    assert requests[0].full_url == 'https://vidnest.io/dl'
    assert urllib.parse.parse_qs(requests[0].data.decode()) == {
        'op':['embed'], 'file_code':['abc'], 'auto':['1'],
        'referer':['https://provider.example/show'],
    }
    assert [(stream['resolution'], stream['type']) for stream in streams] == [('1080p','mp4'),('720p','hls')]
    assert streams[1]['link'] == 'https://vidnest.io/video.m3u8'
    assert all(stream['source_priority'] == 4 for stream in streams)


@pytest.mark.parametrize('failure', [False, True])
def test_vidnest_handles_empty_pages_and_request_failure(monkeypatch, failure):
    def open_mock(*args, **kwargs):
        if failure:
            raise OSError('Connection failed')
        return io.BytesIO(b'No sources')

    monkeypatch.setattr(urllib.request, 'urlopen', open_mock)
    assert VidnestExtractor().extract('https://vidnest.io/e/abc') == []
