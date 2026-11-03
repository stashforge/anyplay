"""The first health feature only lists local report metadata."""

import importlib.util
import json
from pathlib import Path

import pytest

from media_cli.cli.args import parse_cli_args


def modules():
    assert importlib.util.find_spec('media_cli.cli.health'), 'Health report command is missing'
    from media_cli.cli import health
    from media_cli.state import health_reports
    return health, health_reports


def write_report(root, folder_id='run-1', **updates):
    folder = root / folder_id
    folder.mkdir(parents=True, exist_ok=True)
    data = dict(schema_version=1, run_id=folder_id, started_at='2026-10-29T10:00:00Z',
                status='complete', verification='http', providers=[])
    data.update(updates)
    (folder / 'report.json').write_text(json.dumps(data))
    return folder


def test_health_parser_accepts_only_implemented_action():
    args, _ = parse_cli_args(['health', 'reports'])
    assert args.command == 'health' and args.health_action == 'reports'


@pytest.mark.parametrize('argv', [['health'], ['health', 'unwired-provider'], ['health', '--edit']])
def test_unimplemented_health_actions_do_not_enter_media_application(argv):
    with pytest.raises(SystemExit) as exc:
        parse_cli_args(argv)
    assert exc.value.code == 2


def test_empty_listing_does_not_create_state(monkeypatch, tmp_path, capsys):
    command, reports = modules()
    root = tmp_path / 'missing'
    monkeypatch.setattr(reports, 'reports_directory', lambda: root)
    assert command.handle_health_command('reports') == 0
    assert 'No saved health reports yet.' in capsys.readouterr().out
    assert not root.exists()


def test_listing_is_newest_first_and_displays_metadata(monkeypatch, tmp_path, capsys):
    command, reports = modules()
    write_report(tmp_path, 'older', started_at='2026-10-29T08:00:00+00:00')
    write_report(tmp_path, 'newer', started_at='2026-10-29T12:00:00+01:00', status='cancelled', verification='mpv')
    monkeypatch.setattr(reports, 'reports_directory', lambda: tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.glob('*/report.json')}
    assert command.handle_health_command('reports') == 0
    output = capsys.readouterr().out
    assert output.index('newer') < output.index('older')
    assert 'cancelled' in output and 'mpv' in output and 'http' in output
    assert '2026-10-29' in output
    assert '\x1b' not in output
    assert before == {p: p.read_bytes() for p in before}


@pytest.mark.parametrize('updates', [
    {'schema_version': 99}, {'schema_version': True}, {'run_id': 'other'},
    {'run_id': '\u001b[31mBAD'}, {'started_at': 'not-a-date'},
    {'started_at': '2026-10-29T10:00:00'}, {'status': 'bad\nextra'},
    {'verification': 'unverified\x1b[2J'},
])
def test_invalid_reports_are_skipped_without_rendering_payload(monkeypatch, tmp_path, capsys, updates):
    command, reports = modules()
    write_report(tmp_path, **updates)
    monkeypatch.setattr(reports, 'reports_directory', lambda: tmp_path)
    assert command.handle_health_command('reports') == 0
    output = capsys.readouterr().out
    assert 'Skipped 1 unreadable or unsupported report.' in output
    assert '\x1b' not in output


def test_reader_skips_corrupt_oversized_and_symlinked_files(tmp_path):
    _, reports = modules()
    valid = write_report(tmp_path, 'valid')
    broken = write_report(tmp_path, 'broken')
    (broken / 'report.json').write_text('{bad')
    huge = write_report(tmp_path, 'huge')
    (huge / 'report.json').write_bytes(b' ' * (reports.MAX_REPORT_BYTES + 1))
    (tmp_path / 'linked-dir').symlink_to(valid, target_is_directory=True)
    linked = tmp_path / 'linked-file'
    linked.mkdir()
    (linked / 'report.json').symlink_to(valid / 'report.json')
    rows, skipped = reports.list_saved_reports(tmp_path)
    assert [row.run_id for row in rows] == ['valid']
    assert skipped == 4


def test_root_read_error_is_visible_and_exits_nonzero(monkeypatch, capsys):
    command, reports = modules()
    def denied(*args):
        raise PermissionError('private diagnostic must not be dumped')
    monkeypatch.setattr(reports, 'list_saved_reports', denied)
    assert command.handle_health_command('reports') == 1
    output = capsys.readouterr()
    assert 'Could not list health reports' in output.err
    assert 'private diagnostic' not in output.err


def test_health_help_is_available_without_claiming_live_checks(capsys):
    with pytest.raises(SystemExit) as exc:
        parse_cli_args(['health', '--help'])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert 'reports' in output and 'not available yet' in output


@pytest.mark.parametrize('shell', ['bash', 'zsh', 'fish'])
def test_health_command_and_reports_action_are_in_shell_completion(shell):
    from media_cli.cli.completion import generate_completion
    script = generate_completion(shell)
    assert 'health' in script and 'reports' in script
