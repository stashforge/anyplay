"""Translation selection must reach provider calls and every recorded sample."""
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock


def test_worker_requests_dub_sources_instead_of_sub(monkeypatch, capsys):
    from media_cli import providers
    from media_cli.health import search_worker
    instance = SimpleNamespace(episode_sources=Mock(return_value={'episode': {'sourceUrls': []}}))
    monkeypatch.setattr(providers, 'PROVIDER_FACTORIES', {'anipm': lambda: instance})
    request = {'provider': 'anipm', 'operation': 'resolve', 'translation': 'dub',
               'value': {'title_id': '101922', 'mal_id': 38000, 'episode': '1'}}
    monkeypatch.setattr(search_worker.sys, 'stdin', io.StringIO(json.dumps(request)))
    assert search_worker.main() == 0
    instance.episode_sources.assert_called_once_with('101922', '1', 'dub')
    assert json.loads(capsys.readouterr().out)['mirrors'] == []


def test_dub_selection_flows_through_all_runner_operations():
    from media_cli.health.catalogue import check_catalogue
    seen = []
    def operation(name, result):
        def call(*args, translation='sub'):
            seen.append((name, translation))
            return result
        return call
    report = check_catalogue('anipm', [{'title': 'Demon Slayer', 'year': 2019, 'mal_id': 38000}],
        seed=42, translation='dub',
        query=operation('search', {'status': 'ok', 'results': [{'malId': 38000, 'anilistId': 101922, 'provider_id': '101922'}]}),
        details_query=operation('details', {'status': 'ok', 'details': [{'status': 'passed', 'reason': 'Data returned'}]}),
        episode_query=operation('episodes', {'status': 'ok', 'ids': ['1']}),
        resolution_query=operation('resolution', {'status': 'ok', 'mirrors': []}))
    assert {name for name, tr in seen} == {'search', 'details', 'episodes', 'resolution'}
    assert all(tr == 'dub' for name, tr in seen)
    stages = report['providers'][0]['stages']
    assert all(s.get('translation') == 'dub' for s in stages if s.get('title'))


def test_cli_accepts_both_and_all_but_not_report_translation():
    import pytest
    from media_cli.cli.args import parse_cli_args
    args, _ = parse_cli_args(['health', 'all', '--translation', 'both', '--seed', '42'])
    assert args.health_action == 'all' and args.health_translation == 'both'
    args, _ = parse_cli_args(['health', 'anipm', '--translation', 'dub'])
    assert args.health_translation == 'dub'
    with pytest.raises(SystemExit):
        parse_cli_args(['health', 'report', 'latest', '--translation', 'both'])


def test_both_translations_keep_separate_verdicts(monkeypatch):
    from media_cli.health import catalogue
    from media_cli.health.runner import check_provider
    seen = []
    def check(pid, samples, **kwargs):
        channel = kwargs['translation']
        seen.append(channel)
        return {'started_at': '2026-10-30T00:00:00Z', 'status': 'complete', 'verification': 'http',
                'providers': [{'id': pid, 'name': 'AniPM',
                    'status': 'working' if channel == 'sub' else 'inconclusive',
                    'stages': [{'name': 'search', 'status': 'passed', 'translation': channel, 'title': 'Title'}]}]}
    monkeypatch.setattr(catalogue, 'check_catalogue', check)
    report = check_provider('anipm', seed=42, translation='both')
    assert seen == ['sub', 'dub']
    summaries = [s for s in report['providers'][0]['stages'] if s['name'] == 'translation_summary']
    assert [s['status'] for s in summaries] == ['passed', 'inconclusive']
    assert report['providers'][0]['status'] == 'partly_working'


def test_unsupported_dub_makes_no_provider_requests(monkeypatch):
    from media_cli.health import catalogue
    from media_cli.health.runner import check_provider
    monkeypatch.setattr(catalogue, 'check_catalogue', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('Unsupported Dub requested')))
    report = check_provider('animepahe', translation='dub', seed=42)
    assert report['providers'][0]['status'] == 'not_checked'
    assert report['providers'][0]['stages'][0]['status'] == 'not_applicable'


def test_worker_rejects_unknown_translation_before_provider_import(monkeypatch):
    from media_cli.health import search_worker
    monkeypatch.setattr(search_worker.sys, 'stdin', io.StringIO(json.dumps({
        'provider': 'anipm', 'operation': 'search', 'translation': 'invalid', 'value': 'Title'})))
    assert search_worker.main() == 2


def test_both_cancellation_marks_other_translation_unstarted(monkeypatch):
    from media_cli.health import catalogue
    from media_cli.health.runner import check_provider
    calls = []
    def check(pid, samples, **kwargs):
        calls.append(kwargs['translation'])
        return {'started_at': '2026-10-30T00:00:00Z', 'status': 'cancelled', 'verification': 'http',
                'providers': [{'id': pid, 'name': pid, 'status': 'inconclusive', 'stages': []}]}
    monkeypatch.setattr(catalogue, 'check_catalogue', check)
    result = check_provider('anipm', translation='both', seed=42)
    assert calls == ['sub'] and result['status'] == 'cancelled'
    assert any(s.get('translation') == 'dub' and s['status'] == 'not_verified'
               for s in result['providers'][0]['stages'])


def test_partly_working_translations_retain_partial_success(monkeypatch):
    from media_cli.health import catalogue
    from media_cli.health.runner import check_provider
    def check(pid, samples, **kwargs):
        return {'started_at': '2026-10-30T00:00:00Z', 'status': 'complete', 'verification': 'http',
                'providers': [{'id': pid, 'name': pid, 'status': 'partly_working',
                    'stages': [{'name': 'media_access', 'track': 'video', 'status': 'passed',
                                'translation': kwargs['translation']}]}]}
    monkeypatch.setattr(catalogue, 'check_catalogue', check)
    result = check_provider('anipm', translation='both', seed=42)
    assert result['providers'][0]['status'] == 'partly_working'
