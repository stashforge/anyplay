"""Offline batch schema checks: strict metadata, legacy reads, immutable files."""

from copy import deepcopy
import json
import stat

import pytest

from media_cli.domain.health_report import prepare_health_evidence
from media_cli.state.health_reports import list_saved_reports, read_saved_report, save_health_report


def report():
    return {
        'started_at': '2026-10-30T10:00:00Z',
        'status': 'complete', 'verification': 'http',
        'providers': [{
            'id': 'example', 'name': 'Example', 'status': 'partly_working',
            'stages': [{'name': 'search', 'status': 'passed', 'reason': 'Recorded result'}],
        }],
    }


def test_mixed_batch_roundtrip_preserves_metadata_and_recorded_verdicts(tmp_path):
    data = report()
    data.update(verification='mixed', translation='both', status='cancelled')
    first = data['providers'][0]
    first.update(verification='http', scope_note='HTTP samples only',
                 run_status='complete', translation='both')
    other = deepcopy(first)
    other.update(id='other', name='Other', status='not_checked', stages=[],
                 verification='catalogue', scope_note='Not reached before cancellation',
                 run_status='not_started', translation='sub')
    data['providers'].append(other)
    before = deepcopy(data)
    clean = prepare_health_evidence(data)
    assert clean == data
    run_id = save_health_report(tmp_path, data)
    folder = tmp_path / run_id
    files = {path: path.read_bytes() for path in folder.iterdir()}
    saved = json.loads(files[folder / 'report.json'])
    assert saved['verification'] == 'mixed'
    assert saved['translation'] == 'both'
    assert saved['status'] == 'cancelled'
    assert saved['providers'] == before['providers']
    rows, skipped = list_saved_reports(tmp_path)
    assert skipped == 0 and len(rows) == 1
    assert rows[0].run_id == run_id and rows[0].verification == 'mixed'
    assert rows[0].status == 'cancelled'
    assert 'Example' in read_saved_report(tmp_path, 'latest')
    assert all(path.read_bytes() == content for path, content in files.items())
    assert stat.S_IMODE(folder.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in files)
    assert data == before


@pytest.mark.parametrize(('key', 'value'), [
    *[('verification', level) for level in ('catalogue', 'discovery', 'extraction', 'http', 'mpv', 'visible')],
    *[('run_status', status) for status in ('complete', 'incomplete', 'cancelled', 'not_started')],
    *[('translation', translation) for translation in ('sub', 'dub', 'both')],
    ('scope_note', ''),
])
def test_optional_provider_metadata_survives_without_recomputing_verdict(key, value):
    data = report()
    data['providers'][0][key] = value
    clean = prepare_health_evidence(data)
    assert clean['providers'][0][key] == value
    assert clean['providers'][0]['status'] == 'partly_working'
    assert clean['providers'][0]['stages'] == data['providers'][0]['stages']


@pytest.mark.parametrize('translation', ['sub', 'dub', 'both'])
def test_requested_root_translation_survives_save(tmp_path, translation):
    data = report()
    data['translation'] = translation
    run_id = save_health_report(tmp_path, data)
    saved = json.loads((tmp_path / run_id / 'report.json').read_text())
    assert saved['translation'] == translation
    assert 'translation' not in saved['providers'][0]


@pytest.mark.parametrize('level', ['catalogue', 'discovery', 'extraction', 'http', 'mpv', 'visible', 'mixed'])
def test_valid_root_verification_lists_and_reads_without_rewriting(tmp_path, level):
    data = report()
    data.update(verification=level, schema_version=1, run_id='existing')
    folder = tmp_path / 'existing'
    folder.mkdir()
    (folder / 'report.json').write_text(json.dumps(data))
    (folder / 'report.txt').write_text('Legacy text\n')
    files = {path: path.read_bytes() for path in folder.iterdir()}
    rows, skipped = list_saved_reports(tmp_path)
    assert skipped == 0 and len(rows) == 1
    assert rows[0].verification == level
    assert 'Recorded result' in read_saved_report(tmp_path, 'existing')
    assert all(path.read_bytes() == content for path, content in files.items())


@pytest.mark.parametrize(('where', 'key', 'value'), [
    *[('root', 'verification', value) for value in ('invalid', 'Mixed', '', None, True, 1, [], {})],
    *[('root', 'translation', value) for value in ('invalid', 'Sub', '', None, True, 1, [], {})],
    *[('provider', 'verification', value) for value in ('mixed', 'invalid', 'HTTP', '', None, True, 1, [], {})],
    *[('provider', 'run_status', value) for value in ('working', 'not_checked', 'Complete', '', None, True, 1, [], {})],
    *[('provider', 'translation', value) for value in ('invalid', 'Sub', '', None, True, 1, [], {})],
    *[('provider', 'scope_note', value) for value in (None, True, 1, [], {})],
])
def test_invalid_batch_metadata_is_rejected_before_writing(tmp_path, where, key, value):
    data = report()
    target = data if where == 'root' else data['providers'][0]
    target[key] = value
    before = deepcopy(data)
    with pytest.raises(ValueError):
        prepare_health_evidence(data)
    root = tmp_path / 'absent'
    with pytest.raises(ValueError):
        save_health_report(root, data)
    assert not root.exists()
    assert data == before


def test_scope_note_uses_label_safety_and_does_not_expand_schema(tmp_path):
    data = report()
    data['providers'][0].update(
        scope_note='Sampled https://cdn.example/private-path?token=SECRET-URL to\x1b[31mken=SECRET-TOKEN',
        headers={'Cookie': 'SECRET-COOKIE'},
    )
    data['translation'] = 'both'
    data['unrecognized_batch_metadata'] = 'SECRET-UNKNOWN'
    clean = prepare_health_evidence(data)
    assert clean['providers'][0]['scope_note'] == 'Sampled cdn.example [redacted]'
    assert 'headers' not in clean['providers'][0]
    assert 'unrecognized_batch_metadata' not in clean
    run_id = save_health_report(tmp_path, data)
    content = (tmp_path / run_id / 'report.json').read_text()
    assert 'scope_note' in content and 'cdn.example' in content
    assert 'SECRET' not in content and 'private-path' not in content
    assert '\\u001b' not in content


def test_legacy_provider_fields_stay_absent_and_legacy_text_stays_immutable(tmp_path):
    data = report()
    clean = prepare_health_evidence(data)
    assert clean == data
    assert set(clean['providers'][0]) == {'id', 'name', 'status', 'stages'}
    data.update(schema_version=1, run_id='legacy')
    del data['providers']
    folder = tmp_path / 'legacy'
    folder.mkdir()
    (folder / 'report.json').write_text(json.dumps(data))
    (folder / 'report.txt').write_text('Legacy evidence\n')
    files = {path: path.read_bytes() for path in folder.iterdir()}
    assert read_saved_report(tmp_path, 'latest') == 'Legacy evidence\n'
    assert all(path.read_bytes() == content for path, content in files.items())


@pytest.mark.parametrize(('key', 'value'), [
    ('verification', 'mixed'), ('run_status', 'working'),
    ('translation', 'Sub'), ('scope_note', 5),
])
def test_invalid_optional_provider_metadata_keeps_legacy_fallback(tmp_path, key, value):
    data = report()
    data['providers'][0][key] = value
    data.update(schema_version=1, run_id='legacy')
    folder = tmp_path / 'legacy'
    folder.mkdir()
    (folder / 'report.json').write_text(json.dumps(data))
    (folder / 'report.txt').write_text('Legacy evidence\n')
    files = {path: path.read_bytes() for path in folder.iterdir()}
    assert read_saved_report(tmp_path, 'legacy') == 'Legacy evidence\n'
    assert all(path.read_bytes() == content for path, content in files.items())
