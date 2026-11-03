"""Save one coherent, private report pair without inventing live health results."""

import json
import stat
from copy import deepcopy

import pytest

from media_cli.state import health_reports as reports


def sample(status='complete'):
    return dict(started_at='2026-10-29T10:00:00+00:00', status=status, verification='http', seed=42,
                providers=[dict(id='senshi', name='Senshi', status='partly_working', stages=[
                    dict(name='search', status='passed', title='Example', age_group='recent', elapsed_seconds=1.25),
                    dict(name='extraction', status='failed', source='Example source', reason='HTTP 403', elapsed_seconds=2.5)])])


def save(root, data):
    assert hasattr(reports, 'save_health_report'), 'The report writer is missing'
    return reports.save_health_report(root, data)


@pytest.mark.parametrize('status', ['complete', 'incomplete', 'cancelled'])
def test_saved_pair_works_with_existing_list_view_delete(tmp_path, status):
    data = sample(status)
    before = deepcopy(data)
    run_id = save(tmp_path, data)
    assert run_id != 'latest'
    rows, skipped = reports.list_saved_reports(tmp_path)
    assert skipped == 0 and len(rows) == 1
    assert rows[0].run_id == run_id and rows[0].status == status
    saved = json.loads((tmp_path / run_id / 'report.json').read_text())
    assert saved['run_id'] == run_id and saved['schema_version'] == 1
    assert saved['seed'] == 42
    assert saved['providers'][0]['stages'][0]['elapsed_seconds'] == 1.25
    text = reports.read_saved_report(tmp_path, 'latest')
    for expected in ('Senshi', 'Search', 'Extraction', 'HTTP 403', 'Example', status, '1.25'):
        assert expected in text
    assert data == before
    assert reports.delete_saved_report(tmp_path, run_id) == run_id
    assert reports.list_saved_reports(tmp_path) == ([], 0)


def test_empty_report_does_not_claim_provider_success(tmp_path):
    data = dict(started_at='2026-10-29T10:00:00Z', status='cancelled', verification='http', providers=[])
    run_id = save(tmp_path, data)
    assert 'No providers were checked.' in reports.read_saved_report(tmp_path, run_id)


@pytest.mark.parametrize('updates', [
    {'status': 'working'}, {'verification': 'invalid'},
    {'started_at': '2026-10-29T10:00:00'}, {'started_at': 'bad'},
    {'providers': 'not a list'},
])
def test_invalid_input_creates_no_report_directory(tmp_path, updates):
    root = tmp_path / 'absent'
    data = sample()
    data.update(updates)
    with pytest.raises(ValueError):
        save(root, data)
    assert not root.exists()


@pytest.mark.parametrize('elapsed', [-1, float('nan'), float('inf'), True])
def test_invalid_stage_elapsed_is_rejected(tmp_path, elapsed):
    data = sample()
    data['providers'][0]['stages'][0]['elapsed_seconds'] = elapsed
    with pytest.raises(ValueError):
        save(tmp_path / 'absent', data)


def test_credentials_and_signed_urls_are_not_saved(tmp_path):
    data = sample()
    data['token'] = 'TOP-SECRET'
    provider = data['providers'][0]
    provider['headers'] = {'Cookie': 'SECRET-COOKIE'}
    stage = provider['stages'][1]
    stage['url'] = 'https://cdn.example/private-signed-path?token=SECRET-URL'
    stage['reason'] = 'Failed https://cdn.example/private-signed-path?token=SECRET-URL Bearer SECRET-BEARER'
    run_id = save(tmp_path, data)
    for name in ('report.json', 'report.txt'):
        content = (tmp_path / run_id / name).read_text()
        for secret in ('TOP-SECRET', 'SECRET-COOKIE', 'SECRET-URL', 'SECRET-BEARER', 'private-signed-path'):
            assert secret not in content
        assert 'cdn.example' in content


def test_unique_runs_do_not_overwrite_and_files_are_private(tmp_path):
    first = save(tmp_path, sample())
    old = (tmp_path / first / 'report.json').read_bytes()
    second = save(tmp_path, sample())
    assert first != second
    assert (tmp_path / first / 'report.json').read_bytes() == old
    assert stat.S_IMODE((tmp_path / second).stat().st_mode) == 0o700
    for name in ('report.json', 'report.txt'):
        assert stat.S_IMODE((tmp_path / second / name).stat().st_mode) == 0o600


def test_failure_during_second_file_leaves_no_half_report(monkeypatch, tmp_path):
    assert hasattr(reports, 'save_health_report'), 'The report writer is missing'
    from media_cli.state import io
    original = io.write_private_text
    def fail_text(directory, filename, content):
        if filename == 'report.txt':
            raise OSError('disk full')
        return original(directory, filename, content)
    monkeypatch.setattr(io, 'write_private_text', fail_text)
    with pytest.raises(OSError):
        save(tmp_path, sample())
    assert reports.list_saved_reports(tmp_path) == ([], 0)
    assert list(tmp_path.iterdir()) == []


def test_pending_staging_is_not_a_finished_or_invalid_report(tmp_path):
    (tmp_path / '.pending-example').mkdir()
    assert reports.list_saved_reports(tmp_path) == ([], 0)


def test_deeply_nested_invalid_metadata_does_not_break_listing(tmp_path):
    folder = tmp_path / 'broken'
    folder.mkdir()
    (folder / 'report.json').write_text('[' * 1200 + '0' + ']' * 1200)
    assert reports.list_saved_reports(tmp_path) == ([], 1)
