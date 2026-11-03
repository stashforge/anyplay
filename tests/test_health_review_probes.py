"""Review regressions use synthetic responses, never provider traffic."""

import pytest

from media_cli.health import catalogue
from media_cli.health.http_media import HTTPResult, probe_entry


def _outcome():
    return {'status': 'ok', 'mirrors': [{'resolution': '1080p', 'type': 'hls',
            'host': 'cdn.example', 'separate_audio': False, 'subtitle_count': 0,
            'headers_present': False}],
            'http_failure': {'status': 'missing_dependency', 'issue_scope': 'local',
                             'requirement_kind': 'python', 'reason': 'HTTP module unavailable'},
            'playback_checks': [{'track': 'video', 'status': 'passed', 'decoded_frames': 12},
                                {'track': 'audio', 'status': 'passed', 'decoded_samples': 500}]}


@pytest.mark.parametrize('provider', ['senshi', 'anipm'])
def test_completed_decoding_survives_http_local_failure(provider):
    common = dict(query=lambda *a: {'status': 'ok', 'results': [
        {'malId': 38000, 'anilistId': 101922, 'provider_id': '101922'}]},
        episode_query=lambda *a: {'status': 'ok', 'ids': ['1']},
        http_checks=True, playback_checks=True)
    if provider == 'senshi':
        common.update(source_query=lambda *a: {'status': 'ok', 'sources': ['Vidcloud']},
                      extraction_query=lambda *a: _outcome())
    else:
        common['resolution_query'] = lambda *a: _outcome()
    report = catalogue.check_catalogue(provider, [{'title': 'Demon Slayer', 'year': 2019,
                                                'mal_id': 38000}], seed=42, **common)
    assert report['status'] == 'incomplete'
    stages = report['providers'][0]['stages']
    assert any(s.get('decoded_frames') == 12 and s['status'] == 'passed' for s in stages)
    assert any(s.get('decoded_samples') == 500 and s['status'] == 'passed' for s in stages)


def test_selected_hls_master_audio_is_checked_not_declared_optional():
    base = 'https://cdn.example/'
    bodies = {'master.m3u8': b'#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="a",NAME="English",DEFAULT=YES,URI="audio.m3u8"\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="other",NAME="Other",URI="other.m3u8"\n#EXT-X-STREAM-INF:BANDWIDTH=1000,AUDIO="a"\nvideo.m3u8\n',
              'video.m3u8': b'#EXTM3U\n#EXTINF:5,\nv.ts\n', 'v.ts': b'video',
              'audio.m3u8': b'gone'}
    seen = []
    def fetch(url, headers, limit, sample=False):
        name = url.removeprefix(base)
        seen.append(name)
        return HTTPResult(url, 404 if name == 'audio.m3u8' else 200, bodies[name])
    checks = probe_entry({'type': 'hls', 'streamUrl': base + 'master.m3u8'}, fetch)
    assert checks[0]['status'] == 'passed'
    audio = next(c for c in checks if c['track'] == 'audio')
    assert audio['required'] is True and audio['status'] == 'failed'
    assert 'audio.m3u8' in seen and 'other.m3u8' not in seen


def test_missing_crypto_is_local_dependency_not_remote_uncertainty(monkeypatch):
    from media_cli.services import requirements
    from media_cli.services.requirements import MissingPythonDependency
    monkeypatch.setattr(requirements, 'optional_python', lambda name: None)
    def fetch(url, headers, limit, sample=False):
        return HTTPResult(url, 200, b'EM3U8v1:synthetic')
    with pytest.raises(MissingPythonDependency):
        probe_entry({'type': 'hls', 'streamUrl': 'https://cdn.example/encrypted'}, fetch)
