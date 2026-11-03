"""Known HLS variants outrank an equivalent master without losing Auto choice."""

import pytest

from media_cli.media import sources
from media_cli.media.ytdlp import streams_from_ytdlp_data


def sample_data():
    def fmt(label, width, height, master):
        return {'format_id': label, 'url': f'https://cdn.example.com/{label}.m3u8',
                'manifest_url': f'https://cdn.example.com/{master}.m3u8',
                'width': width, 'height': height, 'protocol': 'm3u8_native',
                'vcodec': 'h264', 'acodec': 'aac'}
    return {'formats': [fmt('360', 640, 268, 'first'), fmt('720', 1280, 536, 'first'),
                        fmt('1080', 1920, 804, 'second')]}


def extracted():
    return streams_from_ytdlp_data(sample_data(), url='https://rumble.com/embed/example', name='Rumble', priority=1)


def test_master_ceiling_is_derived_from_its_own_variants():
    masters = {s['link']: s for s in extracted() if s['resolution'] == 'Adaptive'}
    assert len(masters) == 2
    first = masters['https://cdn.example.com/first.m3u8']
    second = masters['https://cdn.example.com/second.m3u8']
    assert first['_max_quality_rank'] == 720
    assert first['max_resolution'] == '720p (1280×536)'
    assert second['_max_quality_rank'] == 1080
    assert second['max_resolution'] == '1080p (1920×804)'


@pytest.mark.parametrize('target', ['best', '1080p', '720p'])
def test_known_master_does_not_beat_equivalent_concrete_quality(target):
    rows = [{'resolution': 'Adaptive', '_max_quality_rank': 720}, {'resolution': '720p'}]
    ordered = sorted(rows, key=lambda row: sources.stream_quality_preference_key(row, target))
    assert ordered[0]['resolution'] == '720p'
    assert len(ordered) == 2


def test_master_quality_ceiling_matters_and_exact_quality_precedes_higher_one():
    rows = extracted()
    ordered = sorted(rows, key=lambda row: sources.stream_quality_preference_key(row, 'best'))
    assert ordered[0]['resolution'].startswith('1080p')
    assert ordered[1]['resolution'] == 'Adaptive'
    assert ordered[1]['_max_quality_rank'] == 1080
    requested = sorted(rows, key=lambda row: sources.stream_quality_preference_key(row, '720p'))
    assert requested[0]['resolution'].startswith('720p')


@pytest.mark.parametrize('target', ['auto', 'adaptive'])
def test_explicit_adaptive_choice_still_wins(target):
    rows = [{'resolution': '720p'}, {'resolution': 'Adaptive', '_max_quality_rank': 720}]
    assert min(rows, key=lambda row: sources.stream_quality_preference_key(row, target))['resolution'] == 'Adaptive'


def test_unknown_adaptive_is_available_but_has_no_invented_quality():
    rows = [{'resolution': 'Adaptive'}, {'resolution': '480p'}]
    assert min(rows, key=lambda row: sources.stream_quality_preference_key(row, 'best'))['resolution'] == '480p'
    assert sources.stream_quality_preference_key(rows[0], 'best')


@pytest.mark.parametrize('quality,expected,saved_preference', [('best', '720p', False), ('1080p', '720p', False), ('auto', 'Adaptive', False), ('best', 'Adaptive', True)])
def test_actual_foreground_selection_uses_same_stream_quality_rule(monkeypatch, quality, expected, saved_preference):
    from media_cli.core import streams

    master = {'source_name': 'Rumble [HLS] Adaptive', 'resolution': 'Adaptive', '_max_quality_rank': 720,
              'link': 'https://cdn.example.com/master.m3u8'}
    concrete = {'source_name': 'Rumble 720p', 'resolution': '720p', 'link': 'https://cdn.example.com/720.m3u8'}
    monkeypatch.setattr(streams, 'expand_direct_sources', lambda rows: rows)
    monkeypatch.setattr(streams, 'get_preferred_mirror', lambda *args: {'source_name': master['source_name'], 'resolution': 'Adaptive'} if saved_preference else {})
    monkeypatch.setattr(streams, 'resolve_source', lambda *args, **kwargs: [master, concrete])
    result = streams.fetch_episode_stream('show', '1', quality=quality,
        ep_data={'episode': {'sourceUrls': [{'sourceName': 'Rumble', 'sourceUrl': 'https://rumble.com/embed/example'}]}})
    assert result[0]['resolution'] == expected
