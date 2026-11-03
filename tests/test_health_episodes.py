"""Sample matched title identities before checking their episode catalogues."""

import json

import pytest

from media_cli.health import catalogue
from media_cli.cli.args import parse_cli_args


def search(pid, title):
    sample = next(s for s in catalogue.SENSHI_SAMPLES if s['title'] == title)
    return {'status': 'ok', 'results': [{'malId': sample['mal_id']}]}


def run(episode_query, **kwargs):
    return catalogue.check_catalogue('senshi', catalogue.SENSHI_SAMPLES,
        query=search, episode_query=episode_query, seed=42, **kwargs)


def test_select_two_unique_matched_titles_per_group_and_save_seed():
    calls = []
    def episodes(pid, mal_id):
        calls.append(mal_id)
        return {'status': 'ok', 'ids': ['1', '2']}
    report = run(episodes)
    stages = [s for s in report['providers'][0]['stages'] if s['name'] == 'episode_catalogue']
    assert report['seed'] == 42
    assert len(stages) == len(calls) == len(set(calls)) == 6
    assert {g: sum(s['age_group'] == g for s in stages) for g in ('older', 'middle', 'recent')} == {
        'older': 2, 'middle': 2, 'recent': 2}
    assert all(s['status'] == 'passed' and s['episode_count'] == 2 for s in stages)
    assert all(s['mal_id'] in calls and s['release_year'] for s in stages)
    assert report['providers'][0]['status'] == 'inconclusive'


def test_same_seed_repeats_the_selected_ids():
    selected = []
    for _ in range(2):
        report = run(lambda *args: {'status': 'ok', 'ids': ['1']})
        selected.append([s['mal_id'] for s in report['providers'][0]['stages'] if s['name'] == 'episode_catalogue'])
    assert selected[0] == selected[1]


def test_only_verified_titles_can_reach_episode_lookup():
    calls = []
    samples = catalogue.SENSHI_SAMPLES[:3]
    def query(pid, title):
        return {'status': 'ok', 'results': [{'malId': 20}]}
    report = catalogue.check_catalogue('senshi', samples, query=query,
        episode_query=lambda pid, mid: calls.append(mid) or {'status': 'ok', 'ids': ['1']}, seed=42)
    assert calls == [20]
    assert any('reduced coverage' in s.get('reason', '').lower() for s in report['providers'][0]['stages'])


@pytest.mark.parametrize('outcome', [
    {'status': 'ok', 'ids': []}, {'status': 'timeout', 'ids': []},
    {'status': 'error', 'ids': [], 'reason': 'Invalid catalogue response'},
    {'status': 'ok', 'ids': ['', None]},
])
def test_empty_failed_or_invalid_catalogues_are_not_marked_working(outcome):
    report = run(lambda *args: outcome)
    stages = [s for s in report['providers'][0]['stages'] if s['name'] == 'episode_catalogue']
    assert len(stages) == 6
    assert all(s['status'] == 'inconclusive' for s in stages)
    assert report['providers'][0]['status'] == 'inconclusive'


def test_episode_cancellation_saves_completed_and_unattempted_selections():
    count = 0
    def episodes(*args):
        nonlocal count
        count += 1
        if count == 2:
            raise KeyboardInterrupt
        return {'status': 'ok', 'ids': ['1']}
    report = run(episodes)
    stages = [s for s in report['providers'][0]['stages'] if s['name'] == 'episode_catalogue']
    assert report['status'] == 'cancelled'
    assert len(stages) == 6
    assert sum(s['status'] == 'passed' for s in stages) == 1
    assert sum(s['status'] == 'not_verified' for s in stages) == 5


def test_episode_evidence_survives_report_writer(tmp_path):
    from media_cli.state.health_reports import save_health_report, read_saved_report
    report = run(lambda *args: {'status': 'ok', 'ids': ['1', '2']})
    run_id = save_health_report(tmp_path, report)
    data = json.loads((tmp_path / run_id / 'report.json').read_text())
    stages = [s for s in data['providers'][0]['stages'] if s['name'] == 'episode_catalogue']
    assert len(stages) == 6 and all(s['episode_count'] == 2 and s['mal_id'] for s in stages)
    assert 'Episode count: 2' in read_saved_report(tmp_path, run_id)


def test_seed_option_is_accepted_for_senshi():
    args, _ = parse_cli_args(['health', 'senshi', '--seed', '42'])
    assert args.health_seed == 42


@pytest.mark.parametrize('argv', [
    ['health', 'reports', '--seed', '42'], ['health', 'report', 'latest', '--seed', '42'],
    ['health', 'senshi', '--seed', '-1'],
])
def test_seed_rejects_invalid_scope_or_value(argv):
    with pytest.raises(SystemExit) as exc:
        parse_cli_args(argv)
    assert exc.value.code == 2
