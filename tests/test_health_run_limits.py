"""Bound whole diagnostic work and report omitted samples honestly."""
from types import SimpleNamespace

from media_cli.health import catalogue as c
from media_cli.health import limits


def test_all_catalogue_workers_use_process_group_lifecycle(monkeypatch):
    seen = []
    def execute(command, payload, **kwargs):
        seen.append(kwargs['timeout'])
        return SimpleNamespace(returncode=0, stdout='{"status":"ok","results":[]}', stderr='')
    monkeypatch.setattr(c, '_run_extraction_worker', execute)
    assert c.search_isolated('senshi', 'Title')['status'] == 'ok'
    assert seen == [12]


def test_worker_timeout_is_clipped_by_run_deadline(monkeypatch):
    monkeypatch.setattr(limits, 'monotonic', lambda: 10)
    seen = []
    def execute(command, payload, **kwargs):
        seen.append(kwargs['timeout'])
        return SimpleNamespace(returncode=0, stdout='{"status":"ok","mirrors":[]}', stderr='')
    monkeypatch.setattr(c, '_run_extraction_worker', execute)
    with limits.diagnostic_budget(5):
        c.resolve_mpv_isolated('anipm', {'title_id': '101922', 'mal_id': 38000}, '1')
    assert seen == [5]


def test_expired_budget_saves_partial_results_instead_of_blames_provider(monkeypatch):
    clock = [0]
    monkeypatch.setattr(limits, 'monotonic', lambda: clock[0])
    def query(pid, title):
        clock[0] = 2
        return {'status': 'ok', 'results': [{'malId': 20}]}
    with limits.diagnostic_budget(1):
        data = c.check_catalogue('senshi', c.SENSHI_SAMPLES[:2], query=query)
    assert data['status'] == 'incomplete'
    rows = data['providers'][0]['stages']
    assert any(s['name'] == 'search' and s['status'] == 'passed' for s in rows)
    assert any(s['name'] == 'run_budget' and s['status'] == 'not_verified' for s in rows)


def test_source_work_is_seeded_capped_and_omissions_are_reported():
    seen = []
    data = c.check_catalogue('senshi', [{'title': 'Naruto', 'year': 2002, 'mal_id': 20}], seed=42,
        query=lambda *a: {'status': 'ok', 'results': [{'malId': 20}]},
        episode_query=lambda *a: {'status': 'ok', 'ids': ['1']},
        source_query=lambda *a: {'status': 'ok', 'sources': ['A', 'B', 'C', 'D', 'E']},
        extraction_query=lambda pid, mid, ep, source: seen.append(source) or {'status': 'ok', 'mirrors': []})
    assert len(seen) == 3 and len(set(seen)) == 3
    rows = data['providers'][0]['stages']
    assert sum(s['name'] == 'mirror_extraction' and s['status'] == 'not_verified' for s in rows) == 2
    assert any(s['name'] == 'source_coverage' and 'budget' in s['reason'].lower() for s in rows)


def test_aging_default_sample_warning_names_diagnostic_gap(monkeypatch):
    from datetime import datetime, timezone
    class Future(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2028, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(c, 'datetime', Future)
    data = c.check_catalogue('senshi', c.SENSHI_SAMPLES,
        query=lambda *a: {'status': 'ok', 'results': []})
    warning = next(s for s in data['providers'][0]['stages'] if s['name'] == 'sample_set')
    assert warning['status'] == 'inconclusive'
    assert 'recent' in warning['reason'] and 'not a provider failure' in warning['reason']
