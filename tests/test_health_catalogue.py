"""First live checkpoint: bounded Senshi catalogue evidence, no playback claim."""

from datetime import datetime, timezone
import importlib.util

import pytest

from media_cli.cli.args import parse_cli_args


def catalogue():
    assert importlib.util.find_spec('media_cli.health.catalogue'), 'Catalogue health runner is missing'
    from media_cli.health import catalogue
    return catalogue


def test_parser_accepts_first_live_provider_only():
    args, _ = parse_cli_args(['health', 'senshi', '--plain'])
    assert args.health_action == 'senshi'


def test_built_in_samples_cover_all_age_groups():
    module = catalogue()
    samples = module.SENSHI_SAMPLES
    assert len(samples) == 10
    groups = {module.age_group(item['year'], 2026) for item in samples}
    assert groups == {'older', 'middle', 'recent'}
    assert len({s['mal_id'] for s in samples}) == len(samples)


def run(query):
    module = catalogue()
    samples = module.SENSHI_SAMPLES[:3]
    return module.check_catalogue('senshi', samples, query=query)


def test_exact_mal_matches_record_stages_but_not_playback_health():
    module = catalogue()
    by_title = {s['title']: s['mal_id'] for s in module.SENSHI_SAMPLES}
    report = run(lambda pid, title: {'status': 'ok', 'results': [{'malId': by_title[title], 'name': 'Alias'}]})
    assert report['verification'] == 'catalogue'
    assert report['status'] == 'complete'
    provider = report['providers'][0]
    assert provider['status'] == 'inconclusive'
    identities = [s for s in provider['stages'] if s['name'] == 'title_identity']
    assert len(identities) == 3 and all(s['status'] == 'passed' for s in identities)
    assert any(s['name'] == 'playback' and s['status'] == 'not_verified' for s in provider['stages'])


def test_similar_title_with_wrong_mal_is_not_a_pass():
    report = run(lambda pid, title: {'status': 'ok', 'results': [{'malId': 999999, 'name': title}]})
    stages = report['providers'][0]['stages']
    assert all(s['status'] != 'passed' for s in stages if s['name'] == 'title_identity')


@pytest.mark.parametrize('outcome', [
    {'status': 'ok', 'results': []}, {'status': 'timeout', 'results': []},
    {'status': 'error', 'results': [], 'reason': 'ConnectionError'},
])
def test_empty_or_slow_results_do_not_prove_provider_broken(outcome):
    report = run(lambda *args: outcome)
    assert report['providers'][0]['status'] == 'inconclusive'
    assert not any(s['status'] == 'failed' for s in report['providers'][0]['stages'])


def test_missing_dependency_is_recorded_and_remaining_searches_are_skipped():
    calls = []
    def query(*args):
        calls.append(args)
        return {'status': 'missing_dependency', 'results': [], 'reason': 'Install required package'}
    report = run(query)
    assert len(calls) == 1
    assert report['providers'][0]['status'] == 'inconclusive'
    assert report['providers'][0]['local_issues']


def test_cancellation_retains_completed_evidence():
    calls = []
    def query(*args):
        calls.append(args)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return {'status': 'ok', 'results': []}
    report = run(query)
    assert report['status'] == 'cancelled'
    assert len([s for s in report['providers'][0]['stages'] if s['name'] == 'search']) == 1


def test_catalogue_report_round_trips_through_writer(tmp_path):
    from media_cli.state import health_reports
    report = run(lambda *args: {'status': 'ok', 'results': []})
    run_id = health_reports.save_health_report(tmp_path, report)
    assert 'catalogue' in health_reports.read_saved_report(tmp_path, run_id)
    assert health_reports.list_saved_reports(tmp_path)[0][0].verification == 'catalogue'


def test_isolated_search_uses_hard_timeout_without_shell(monkeypatch):
    module = catalogue()
    calls = []
    def timed_out(*args, **kwargs):
        calls.append((args, kwargs))
        raise module.subprocess.TimeoutExpired(args[0], module.SEARCH_TIMEOUT)
    monkeypatch.setattr(module, '_run_extraction_worker', timed_out)
    outcome = module.search_isolated('senshi', 'Naruto')
    assert outcome['status'] == 'timeout'
    assert calls[0][1]['timeout'] == 12
    assert not calls[0][1].get('shell', False)


def test_cli_saves_cancelled_check_without_opening_pager(monkeypatch, tmp_path, capsys):
    from media_cli.cli import health
    from media_cli.state import health_reports
    module = catalogue()
    def cancelled(*args, **kwargs):
        return dict(started_at=datetime.now(timezone.utc).isoformat(), status='cancelled',
                    verification='catalogue', providers=[])
    monkeypatch.setattr(module, 'check_catalogue', cancelled)
    monkeypatch.setattr(health_reports, 'reports_directory', lambda: tmp_path)
    monkeypatch.setattr(health, 'show_report', lambda *args, **kwargs: pytest.fail('Cancellation must return to shell'))
    assert health.handle_health_command('senshi') == 130
    assert health_reports.list_saved_reports(tmp_path)[0][0].status == 'cancelled'
    assert 'Check cancelled' in capsys.readouterr().out
