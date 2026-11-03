"""Read saved reports safely, with a pager only when the terminal is interactive."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from media_cli.cli import health
from media_cli.cli.args import parse_cli_args
from media_cli.state import health_reports as reports


def create(root, run_id='run-1', started_at='2026-10-29T10:00:00Z', text='Provider health\nExample: working\n'):
    folder = root / run_id
    folder.mkdir(parents=True)
    (folder / 'report.json').write_text(json.dumps(dict(
        schema_version=1, run_id=run_id, started_at=started_at,
        status='complete', verification='http')))
    (folder / 'report.txt').write_text(text)
    return folder


def read(root, selector):
    assert hasattr(reports, 'read_saved_report'), 'Report viewing needs a shared reader'
    return reports.read_saved_report(root, selector)


def test_parser_accepts_explicit_report_and_latest():
    for selector in ('run-1', 'latest'):
        args, _ = parse_cli_args(['health', 'report', selector])
        assert args.health_action == 'report' and args.health_report_id == selector


@pytest.mark.parametrize('argv', [
    ['health', 'report'], ['health', 'reports', 'extra'],
    ['health', 'report', 'one', 'two'],
])
def test_invalid_report_arguments_are_usage_errors(argv):
    with pytest.raises(SystemExit) as exc:
        parse_cli_args(argv)
    assert exc.value.code == 2


def test_read_exact_and_latest_preserves_files(tmp_path):
    old = create(tmp_path, 'older', '2026-10-29T08:00:00Z', 'Old report\n')
    new = create(tmp_path, 'newer', text='New report\n')
    before = {p: p.read_bytes() for p in tmp_path.glob('*/*')}
    assert read(tmp_path, 'older') == 'Old report\n'
    assert read(tmp_path, 'latest') == 'New report\n'
    assert before == {p: p.read_bytes() for p in before}


@pytest.mark.parametrize('selector', ['../outside', '/tmp/outside', 'bad\x1b[2J'])
def test_report_id_cannot_escape_directory(tmp_path, selector):
    with pytest.raises(ValueError):
        read(tmp_path, selector)


def test_missing_reports_do_not_create_state(tmp_path):
    root = tmp_path / 'absent'
    with pytest.raises(FileNotFoundError):
        read(root, 'latest')
    assert not root.exists()


def test_reader_rejects_symbolic_report_text_and_invalid_metadata(tmp_path):
    folder = create(tmp_path)
    text = folder / 'report.txt'
    text.unlink()
    text.symlink_to(folder / 'report.json')
    with pytest.raises((ValueError, OSError)):
        read(tmp_path, 'run-1')
    text.unlink()
    text.write_text('Readable\n')
    (folder / 'report.json').write_text('{}')
    with pytest.raises(ValueError):
        read(tmp_path, 'run-1')


def test_reader_removes_terminal_controls_but_preserves_lines(tmp_path):
    create(tmp_path, text='\x1b[31mTitle\x1b[0m\n\x1b]0;fake title\x07Safe\ttext\x08\r\n')
    assert read(tmp_path, 'run-1') == 'Title\nSafe\ttext\n'


def test_noninteractive_view_prints_text_without_launching_pager(monkeypatch, tmp_path, capsys):
    create(tmp_path)
    monkeypatch.setattr(reports, 'reports_directory', lambda: tmp_path)
    assert hasattr(health, 'show_report'), 'Report pager entry point is missing'
    pager = Mock(side_effect=AssertionError('Must not open pager'))
    monkeypatch.setattr(health.subprocess, 'run', pager)
    assert health.handle_health_command('report', 'latest') == 0
    assert 'Example: working' in capsys.readouterr().out
    pager.assert_not_called()


def test_interactive_view_passes_report_to_pager_without_shell(monkeypatch):
    assert hasattr(health, 'show_report'), 'Report pager entry point is missing'
    monkeypatch.setattr(health.sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(health.sys, 'stdout', SimpleNamespace(isatty=lambda: True, write=lambda text: None, flush=lambda: None))
    monkeypatch.setenv('PAGER', 'less -S')
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(health.subprocess, 'run', run)
    assert health.show_report('Report\n') == 0
    assert run.call_args.args[0] == ['less', '-S']
    assert run.call_args.kwargs['input'] == 'Report\n'
    assert not run.call_args.kwargs.get('shell', False)


def test_explicit_plain_view_avoids_pager(monkeypatch, capsys):
    assert hasattr(health, 'show_report'), 'Report pager entry point is missing'
    monkeypatch.setattr(health.sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    run = Mock(side_effect=AssertionError('Must not open pager'))
    monkeypatch.setattr(health.subprocess, 'run', run)
    assert health.show_report('Report\n', plain=True) == 0
    assert capsys.readouterr().out == 'Report\n'


def test_command_missing_report_returns_clear_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(reports, 'reports_directory', lambda: tmp_path)
    assert hasattr(health, 'show_report'), 'Report viewing command is missing'
    assert health.handle_health_command('report', 'latest') == 1
    assert 'No saved health reports' in capsys.readouterr().err


@pytest.mark.parametrize('pager', ['missing-pager', '"unterminated'])
def test_unavailable_or_invalid_pager_falls_back_to_plain_text(monkeypatch, capsys, pager):
    monkeypatch.setattr(health.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(health.sys.stdout, 'isatty', lambda: True)
    monkeypatch.setenv('PAGER', pager)
    monkeypatch.setattr(health.subprocess, 'run', Mock(side_effect=FileNotFoundError()))
    assert health.show_report('Report\n') == 0
    captured = capsys.readouterr()
    assert captured.out.replace('\x1b[?25l', '').replace('\x1b[?25h', '') == 'Report\n'
    assert 'Pager unavailable' in captured.err


def test_oversized_report_text_is_rejected(tmp_path):
    folder = create(tmp_path)
    (folder / 'report.txt').write_bytes(b'x' * (reports.MAX_REPORT_BYTES + 1))
    with pytest.raises(ValueError):
        read(tmp_path, 'run-1')
