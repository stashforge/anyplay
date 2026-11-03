"""Offline regressions for safe, attributable saved health evidence."""

from copy import deepcopy
import json
import stat

import pytest

from media_cli.domain.health_report import prepare_health_evidence
from media_cli.state.health_reports import read_saved_report, save_health_report


def report(stages):
    return {
        'started_at': '2026-10-30T10:00:00Z',
        'status': 'complete', 'verification': 'http',
        'providers': [{'id': 'example', 'name': 'Example',
                       'status': 'inconclusive', 'stages': stages}],
    }


@pytest.mark.parametrize('reason', [
    'token=\x1b[31mSECRET-VALUE\x1b[0m',
    'to\x1b[31mken\x1b[0m=SECRET-VALUE',
    'to\x08ken=SECRET-VALUE',
    'to\rken=SECRET-VALUE',
    'to\tken=SECRET-VALUE',
    'to\x9b31mken\x9b0m=SECRET-VALUE',
    'api\x1b]0;hidden\x07_key=SECRET-VALUE',
    'Bea\x1b[0mrer SECRET-VALUE',
    'Coo\x1b[0mkie: SECRET-VALUE',
    'api\x1bPignored\x1b\\_key=SECRET-VALUE',
    'https\x1b[0m://cdn.example/private-path?credential=SECRET-VALUE',
])
def test_terminal_sequences_are_removed_before_redacting_saved_pair(tmp_path, reason):
    data = report([{'name': 'media_access', 'status': 'inconclusive', 'reason': reason}])
    before = deepcopy(data)
    run_id = save_health_report(tmp_path, data)
    for name in ('report.json', 'report.txt'):
        content = (tmp_path / run_id / name).read_text()
        assert 'SECRET-VALUE' not in content
        assert 'private-path' not in content
        assert '\x1b' not in content and '\\u001b' not in content
    assert 'SECRET-VALUE' not in read_saved_report(tmp_path, run_id)
    assert data == before


def url_report():
    first = 'https://user:SECRET-PASSWORD@cdn.example/private-one?token=SECRET-ONE#fragment'
    second = 'https://cdn.example/private-two?signature=SECRET-TWO'
    stages = [
        {'name': 'title_details', 'status': 'passed', 'title': 'Public title',
         'provider_title_id': first, 'episode': first, 'mal_id': 123,
         'anilist_id': 456, 'release_year': 2024},
        {'name': 'source_resolution', 'status': 'passed', 'title': 'Public title',
         'provider_title_id': second, 'episode': second},
        {'name': 'media_access', 'status': 'inconclusive', 'title': 'Public title',
         'provider_title_id': first, 'episode': first, 'reason': f'Failed {first}'},
        {'name': 'title_details', 'status': 'passed', 'title': 'Public title',
         'provider_title_id': 'native-public-slug', 'episode': '12.5'},
    ]
    data = report(stages)
    other = deepcopy(data['providers'][0])
    other.update(id='other', name='Other')
    data['providers'].append(other)
    return data


def test_url_identities_correlate_without_merging_provider_or_identifier_kinds(tmp_path):
    data = url_report()
    before = deepcopy(data)
    run_id = save_health_report(tmp_path, data)
    folder = tmp_path / run_id
    contents = {path: path.read_bytes() for path in folder.iterdir()}
    saved = json.loads(contents[folder / 'report.json'])
    stages = saved['providers'][0]['stages']
    for kind in ('provider_title_id', 'episode'):
        first, second, repeated = [stage[kind] for stage in stages[:3]]
        assert first != second
        assert first == repeated
        assert first != saved['providers'][1]['stages'][0][kind]
        assert first != 'cdn.example'
        assert first in contents[folder / 'report.txt'].decode()
    assert stages[0]['provider_title_id'] != stages[0]['episode']
    assert stages[2]['reason'] == 'Failed cdn.example'
    assert stages[3]['provider_title_id'] == 'native-public-slug'
    assert stages[3]['episode'] == '12.5'
    assert stages[0]['title'] == 'Public title'
    assert (stages[0]['mal_id'], stages[0]['anilist_id'], stages[0]['release_year']) == (123, 456, 2024)
    assert prepare_health_evidence(saved)['providers'] == saved['providers']
    viewed = read_saved_report(tmp_path, run_id)
    assert stages[0]['provider_title_id'] in viewed
    assert stages[0]['episode'] in viewed
    for content in [*(value.decode() for value in contents.values()), viewed]:
        for sensitive in ('SECRET', 'private-one', 'private-two', 'fragment', 'https://', 'user:'):
            assert sensitive not in content
    assert all(path.read_bytes() == content for path, content in contents.items())
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in contents)
    assert data == before


def test_url_references_are_unlinkable_between_preparations():
    data = url_report()
    first = prepare_health_evidence(data)['providers'][0]['stages'][0]
    second = prepare_health_evidence(data)['providers'][0]['stages'][0]
    for kind in ('provider_title_id', 'episode'):
        assert first[kind] != second[kind]


def test_huge_duration_is_a_validation_error_before_any_save(tmp_path):
    data = report([{'name': 'search', 'status': 'passed', 'elapsed_seconds': 10 ** 1000}])
    with pytest.raises(ValueError, match='duration'):
        prepare_health_evidence(data)
    with pytest.raises(ValueError, match='duration'):
        save_health_report(tmp_path / 'absent', data)
    assert not (tmp_path / 'absent').exists()


def test_huge_saved_duration_falls_back_to_legacy_text_without_rewriting(tmp_path):
    data = report([{'name': 'search', 'status': 'passed', 'elapsed_seconds': 10 ** 1000}])
    data.update(run_id='legacy', schema_version=1)
    folder = tmp_path / 'legacy'
    folder.mkdir()
    (folder / 'report.json').write_text(json.dumps(data))
    (folder / 'report.txt').write_text('\x1b[31mLegacy evidence\x1b[0m\n')
    before = {path: path.read_bytes() for path in folder.iterdir()}
    assert read_saved_report(tmp_path, 'legacy') == 'Legacy evidence\n'
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize('value', ['native//public-slug', 'public-slug', '123', '12.5'])
def test_non_url_identifiers_remain_public_and_unchanged(value):
    data = report([{'name': 'search', 'status': 'passed',
                    'provider_title_id': value, 'episode': value}])
    stage = prepare_health_evidence(data)['providers'][0]['stages'][0]
    assert stage['provider_title_id'] == value
    assert stage['episode'] == value


@pytest.mark.parametrize('value', [
    '//cdn.example/private-path?credential=SECRET',
    'ftp://user:SECRET@cdn.example/private-path',
    'https\x1b[0m://cdn.example/private-path?credential=SECRET',
])
def test_url_shaped_native_identifiers_are_opaque(value):
    data = report([{'name': 'search', 'status': 'passed',
                    'provider_title_id': value, 'episode': value}])
    stage = prepare_health_evidence(data)['providers'][0]['stages'][0]
    for kind in ('provider_title_id', 'episode'):
        assert stage[kind].startswith('opaque-url-ref:')
        assert all(part not in stage[kind] for part in ('cdn.example', 'private-path', 'SECRET'))
