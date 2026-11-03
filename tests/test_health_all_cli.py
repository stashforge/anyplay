"""Exercise real CLI aggregation/storage with offline provider results."""
import json

from media_cli.cli import health
from media_cli.health import runner
from media_cli.health.profiles import PROFILES
from media_cli.state import health_reports


def setup(monkeypatch, tmp_path, callback):
    monkeypatch.setattr(health, 'PROFILES', {pid: PROFILES[pid] for pid in ('senshi', 'animepahe', 'torrent')})
    monkeypatch.setattr(health_reports, 'reports_directory', lambda: tmp_path)
    monkeypatch.setattr(runner, 'check_provider', callback)
    from media_cli import provider_manifest
    monkeypatch.setattr(provider_manifest, 'disabled_providers_from_config', lambda: {'animepahe'})


def outcome(pid, kwargs, status='complete'):
    return {'started_at': '2026-10-30T00:00:00+00:00', 'status': status,
            'verification': kwargs['verify'], 'providers': [{'id': pid, 'name': pid,
                'status': 'inconclusive', 'stages': [{'name': 'search', 'status': 'passed',
                    'title': 'Fixture', 'reason': 'Offline integration fixture', 'translation': 'sub'}]}]}


def test_all_honors_disabled_config_and_keeps_independent_depths(monkeypatch, tmp_path, capsys):
    calls = []
    def collect(pid, **kwargs):
        calls.append((pid, kwargs['verify'], kwargs['translation']))
        return outcome(pid, kwargs)
    setup(monkeypatch, tmp_path, collect)
    assert health.handle_health_command('all', seed=42, translation='both', plain=True) == 0
    assert calls == [('senshi', 'http', 'both'), ('torrent', 'catalogue', 'both')]
    saved = json.loads(next(tmp_path.glob('*/report.json')).read_text())
    assert saved['verification'] == 'mixed'
    assert [p['verification'] for p in saved['providers']] == ['http', 'catalogue']
    assert saved['translation'] == 'both'
    assert 'enabled providers sequentially' in capsys.readouterr().out


def test_all_cancellation_saves_completed_and_unstarted_providers(monkeypatch, tmp_path):
    def collect(pid, **kwargs):
        return outcome(pid, kwargs, 'cancelled')
    setup(monkeypatch, tmp_path, collect)
    monkeypatch.setattr(health, 'show_report', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('Pager after cancellation')))
    assert health.handle_health_command('all', seed=42, plain=True) == 130
    saved = json.loads(next(tmp_path.glob('*/report.json')).read_text())
    assert saved['status'] == 'cancelled'
    assert saved['providers'][0]['run_status'] == 'cancelled'
    assert saved['providers'][1]['run_status'] == 'not_started'
    assert saved['providers'][1]['stages'][0]['status'] == 'not_verified'
