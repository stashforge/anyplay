from types import SimpleNamespace

from media_cli.cli.args import parse_cli_args
from media_cli.cli import service


def test_service_actions_parse():
    for action in ('install', 'start', 'restart', 'stop', 'status', 'check'):
        args, _ = parse_cli_args(['service', action])
        assert args.command == 'service'
        assert args.service_action == action


def test_units_use_safe_python_and_no_browser():
    units = service.unit_contents('/tmp/a % b/python')
    worker = units['anyplay-history.service']
    assert 'ExecStart="/tmp/a %% b/python" -m media_cli service run' in worker
    assert 'ANYPLAY_BROWSER_ACCESS=off' in worker
    assert 'OnUnitInactiveSec=5min' in units['anyplay-history.timer']


def test_install_does_not_enable(monkeypatch, tmp_path):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    monkeypatch.setattr(service.shutil, 'which', lambda _: '/usr/bin/systemctl')
    calls = []
    monkeypatch.setattr(service.subprocess, 'run', lambda args, **kw: (calls.append(args) or SimpleNamespace(returncode=0)))
    assert service.handle_service_command('install') == 0
    assert (tmp_path / 'systemd/user/anyplay-history.timer').exists()
    assert calls == [['systemctl', '--user', 'daemon-reload']]


def test_stop_stops_running_check_too(monkeypatch):
    monkeypatch.setattr(service.shutil, 'which', lambda _: '/usr/bin/systemctl')
    calls = []
    monkeypatch.setattr(service.subprocess, 'run', lambda args, **kw: (calls.append(args) or SimpleNamespace(returncode=0)))
    assert service.handle_service_command('stop') == 0
    assert calls[0][-2:] == ['anyplay-history.timer', 'anyplay-history.service']


def test_worker_only_refreshes_due_entries(monkeypatch, tmp_path):
    from media_cli.core import storage
    from media_cli.domain import history
    from media_cli.state import paths
    monkeypatch.setattr(paths, 'STATE_DIR', str(tmp_path))
    monkeypatch.setattr(storage, 'load_config', lambda: {})
    monkeypatch.setattr(storage, 'load_history', lambda: [{'due': False}, {'due': True}])
    monkeypatch.setattr(history, 'history_entry_should_auto_refresh', lambda row: row['due'])
    refreshed, saved = [], []
    monkeypatch.setattr(history, 'refresh_history_entry_provider_catalog', lambda row: (refreshed.append(row) or True))
    monkeypatch.setattr(storage, 'patch_history_entry_state', saved.append)
    monkeypatch.setenv('ANYPLAY_BROWSER_ACCESS', 'off')
    monkeypatch.setenv('ANYPLAY_BROWSER_COOKIES', 'off')
    assert service.run_history_check() == 0
    assert refreshed == saved
    assert len(refreshed) == 1 and refreshed[0]['due'] is True


def test_worker_unregisters_terminal_cleanup_on_error(monkeypatch):
    import atexit
    import sys
    import pytest
    cleanup = lambda: None
    monkeypatch.setitem(sys.modules, 'media_cli.ui.display', SimpleNamespace(restore_terminal=cleanup))
    monkeypatch.setattr(service.shutil, 'which', lambda _: '/usr/bin/systemctl')
    def fail():
        raise RuntimeError('refresh failed')
    monkeypatch.setattr(service, 'run_history_check', fail)
    removed = []
    monkeypatch.setattr(atexit, 'unregister', removed.append)
    with pytest.raises(RuntimeError, match='refresh failed'):
        service.handle_service_command('run')
    assert removed == [cleanup]
