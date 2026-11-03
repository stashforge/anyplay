"""Source discovery is separate from mirror extraction and playback."""

import json

import pytest

from media_cli.health import catalogue
from media_cli.providers.senshi import Senshi


def test_senshi_discovery_uses_embeds_without_extracting(monkeypatch):
    provider = Senshi()
    monkeypatch.setattr(provider, '_fetch_json', lambda url: [
        {'status': 'sub', 'remote_source_id': 'remote', 'server2': 'https://embed.test/s'},
        {'status': 'dub', 'serverFM': 'https://embed.test/d'},
    ])
    monkeypatch.setattr(provider, '_extract_embed_sources', lambda *args, **kwargs: pytest.fail('Discovery must not extract'))
    assert hasattr(provider, 'discover_episode_sources'), 'Provider needs a discovery-only boundary'
    assert provider.discover_episode_sources('20', '1', 'sub') == ['Vidcloud', 'StreamNin']
    assert provider.discover_episode_sources('20', '1', 'dub') == ['FileMoon']


def search(pid, title):
    sample = next(s for s in catalogue.SENSHI_SAMPLES if s['title'] == title)
    return {'status': 'ok', 'results': [{'malId': sample['mal_id']}]}


def run(source_query, episode_query=None):
    return catalogue.check_catalogue('senshi', catalogue.SENSHI_SAMPLES, query=search,
        episode_query=episode_query or (lambda *args: {'status': 'ok', 'ids': ['1', '2', '3']}),
        source_query=source_query, seed=42)


def test_one_successful_title_per_group_with_an_available_episode():
    calls = []
    def sources(pid, mal_id, episode):
        calls.append((mal_id, episode))
        return {'status': 'ok', 'sources': ['Vidcloud', 'FileMoon']}
    report = run(sources)
    stages = [s for s in report['providers'][0]['stages'] if s['name'] == 'source_discovery']
    assert len(stages) == len(calls) == 3
    assert {s['age_group'] for s in stages} == {'older', 'middle', 'recent'}
    assert all(s['source_count'] == 2 and s['status'] == 'passed' for s in stages)
    assert all(episode in ('1', '2', '3') for mid, episode in calls)
    assert all((s['mal_id'], s['episode']) in calls for s in stages)
    assert report['providers'][0]['status'] == 'inconclusive'
    assert any(s['name'] == 'mirror_extraction' and s['status'] == 'not_verified' for s in report['providers'][0]['stages'])


def test_no_failed_catalogue_is_selected_for_sources():
    calls = []
    allowed = {20, 38000, 52991}
    def episodes(pid, mal_id):
        return {'status': 'ok', 'ids': ['5'] if mal_id in allowed else []}
    report = run(lambda pid, mid, ep: calls.append((mid, ep)) or {'status': 'ok', 'sources': []}, episodes)
    assert calls and all(mid in allowed and ep == '5' for mid, ep in calls)


def test_source_selection_and_episode_are_repeatable():
    selections = []
    for _ in range(2):
        calls = []
        run(lambda pid, mid, ep: calls.append((mid, ep)) or {'status': 'ok', 'sources': ['Vidcloud']})
        selections.append(calls)
    assert selections[0] == selections[1]


@pytest.mark.parametrize('outcome', [
    {'status': 'ok', 'sources': []}, {'status': 'timeout', 'sources': []},
    {'status': 'error', 'sources': []}, {'status': 'ok', 'sources': [None]},
])
def test_empty_or_invalid_discovery_does_not_claim_dead_provider(outcome):
    report = run(lambda *args: outcome)
    stages = [s for s in report['providers'][0]['stages'] if s['name'] == 'source_discovery']
    assert len(stages) == 3
    assert all(s['status'] == 'inconclusive' for s in stages)
    assert report['providers'][0]['status'] == 'inconclusive'


def test_source_cancellation_retains_selected_and_completed_evidence():
    calls = []
    def sources(*args):
        calls.append(args)
        if len(calls) == 2:
            raise KeyboardInterrupt
        return {'status': 'ok', 'sources': ['Vidcloud']}
    report = run(sources)
    stages = [s for s in report['providers'][0]['stages'] if s['name'] == 'source_discovery']
    assert report['status'] == 'cancelled'
    assert [s['status'] for s in stages] == ['passed', 'not_verified', 'not_verified']


def test_source_evidence_round_trip(tmp_path):
    from media_cli.state.health_reports import save_health_report, read_saved_report
    data = run(lambda *args: {'status': 'ok', 'sources': ['Vidcloud', 'FileMoon']})
    run_id = save_health_report(tmp_path, data)
    saved = json.loads((tmp_path / run_id / 'report.json').read_text())
    stages = [s for s in saved['providers'][0]['stages'] if s['name'] == 'source_discovery']
    assert len(stages) == 3 and all(s['source_names'] == ['Vidcloud', 'FileMoon'] for s in stages)
    assert 'Source count: 2' in read_saved_report(tmp_path, run_id)


def test_no_passed_catalogues_reports_reduced_source_coverage():
    report = run(lambda *args: pytest.fail('No source checks without catalogue success'),
                 lambda *args: {'status': 'ok', 'ids': []})
    stages = report['providers'][0]['stages']
    assert any(s['name'] == 'source_coverage' and '0/3' in s['reason'] for s in stages)
    assert all(s['status'] == 'not_verified' for s in stages if s['name'] == 'source_discovery')


def test_discovery_retains_direct_routes_but_rejects_unexpected_response(monkeypatch):
    provider = Senshi()
    monkeypatch.setattr(provider, '_fetch_json', lambda *args: [
        {'status': 'sub', 'embedUrl': 'https://embed.test/item'},
        {'status': 'sub', 'remoteSourceId': 'opaque'},
        {'status': 'sub', 'irrelevant': True},
    ])
    assert provider.discover_episode_sources('20', '1') == ['Direct link', 'Vidcloud']
    monkeypatch.setattr(provider, '_fetch_json', lambda *args: {'error': 'blocked'})
    assert provider.discover_episode_sources('20', '1') == []
