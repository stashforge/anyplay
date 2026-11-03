"""Delete only one explicitly selected saved report, preserving unrelated state."""

import json

import pytest

from media_cli.cli import health
from media_cli.cli.args import parse_cli_args
from media_cli.state import health_reports as reports


def create(root, run_id='run-1', started_at='2026-10-29T10:00:00Z'):
    folder = root / run_id
    folder.mkdir(parents=True)
    (folder / 'report.json').write_text(json.dumps(dict(
        schema_version=1, run_id=run_id, started_at=started_at,
        status='complete', verification='http')))
    (folder / 'report.txt').write_text('Sample report\n')
    return folder


def delete(root, selector):
    assert hasattr(reports, 'delete_saved_report'), 'Report deletion is missing'
    return reports.delete_saved_report(root, selector)


def test_parser_accepts_explicit_delete():
    args, _ = parse_cli_args(['health', 'report', 'run-1', '--delete'])
    assert args.health_delete and args.health_report_id == 'run-1'


@pytest.mark.parametrize('argv', [
    ['health', 'reports', '--delete'], ['health', 'report', '--delete'],
    ['health', '--delete'],
])
def test_deletion_requires_one_report_selector(argv):
    with pytest.raises(SystemExit) as exc:
        parse_cli_args(argv)
    assert exc.value.code == 2


def test_delete_exact_report_preserves_other_report_and_parent(tmp_path):
    target = create(tmp_path)
    other = create(tmp_path, 'other')
    assert delete(tmp_path, 'run-1') == 'run-1'
    assert not target.exists()
    assert other.is_dir() and tmp_path.is_dir()
    rows, _ = reports.list_saved_reports(tmp_path)
    assert [row.run_id for row in rows] == ['other']


def test_delete_latest_updates_latest_without_stale_index(tmp_path):
    create(tmp_path, 'old', '2026-10-28T10:00:00Z')
    create(tmp_path, 'new')
    assert delete(tmp_path, 'latest') == 'new'
    assert reports.list_saved_reports(tmp_path)[0][0].run_id == 'old'
    assert reports.read_saved_report(tmp_path, 'latest') == 'Sample report\n'


@pytest.mark.parametrize('selector', ['../outside', '/tmp/outside', 'bad\x1b[2J'])
def test_delete_rejects_paths_and_controls(tmp_path, selector):
    target = create(tmp_path)
    with pytest.raises(ValueError):
        delete(tmp_path, selector)
    assert (target / 'report.json').exists()


@pytest.mark.parametrize('extra', ['notes.txt', 'subfolder'])
def test_unknown_files_or_folders_prevent_any_deletion(tmp_path, extra):
    target = create(tmp_path)
    if extra == 'subfolder':
        (target / extra).mkdir()
    else:
        (target / extra).write_text('keep')
    with pytest.raises(ValueError):
        delete(tmp_path, 'run-1')
    assert (target / 'report.txt').exists()
    assert (target / 'report.json').exists()
    assert (target / extra).exists()


@pytest.mark.parametrize('link_folder', [False, True])
def test_symlinks_never_delete_external_report(tmp_path, link_folder):
    root = tmp_path / 'reports'
    target = create(tmp_path / 'outside')
    root.mkdir()
    if link_folder:
        (root / 'run-1').symlink_to(target, target_is_directory=True)
    else:
        local = create(root)
        (local / 'report.txt').unlink()
        (local / 'report.txt').symlink_to(target / 'report.txt')
    with pytest.raises(ValueError):
        delete(root, 'run-1')
    assert (target / 'report.txt').read_text() == 'Sample report\n'
    assert (target / 'report.json').exists()


def test_missing_report_does_not_create_directory(tmp_path):
    root = tmp_path / 'absent'
    with pytest.raises(FileNotFoundError):
        delete(root, 'latest')
    assert not root.exists()


def test_metadata_only_report_can_be_deleted(tmp_path):
    folder = create(tmp_path)
    (folder / 'report.txt').unlink()
    assert delete(tmp_path, 'run-1') == 'run-1'
    assert not folder.exists()


def test_cli_deletes_without_opening_pager(monkeypatch, tmp_path, capsys):
    folder = create(tmp_path)
    monkeypatch.setattr(reports, 'reports_directory', lambda: tmp_path)
    monkeypatch.setattr(health, 'show_report', lambda *args, **kwargs: pytest.fail('Deletion must not open pager'))
    assert hasattr(reports, 'delete_saved_report'), 'Report deletion is missing'
    assert health.handle_health_command('report', 'run-1', delete=True) == 0
    assert 'Deleted health report: run-1' in capsys.readouterr().out
    assert not folder.exists()
