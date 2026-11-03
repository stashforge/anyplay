"""Sequential batch coordination using reports only, without provider calls."""

from copy import deepcopy
from datetime import datetime, timedelta

import pytest


def check_batch(*args, **kwargs):
    from media_cli.health.batch import check_batch as coordinate

    return coordinate(*args, **kwargs)


def single_report(pid, *, status='complete', verdict='working', stages=None):
    return {
        'started_at': '2026-10-30T00:00:00+00:00',
        'status': status,
        'verification': 'http',
        'providers': [{
            'id': pid, 'name': pid, 'status': verdict,
            'stages': stages if stages is not None else [
                {'name': 'search', 'status': 'passed', 'reason': 'Fixture search'},
            ],
        }],
    }


def test_defaults_sequence_deduplication_seed_translation_and_progress():
    calls = []
    events = []

    def run(pid, **kwargs):
        events.append(('run', pid))
        calls.append((pid, kwargs))
        return single_report(pid)

    def progress(index, total, pid):
        events.append(('progress', index, total, pid))

    before = datetime.now().astimezone()
    report = check_batch(['senshi', 'movies', 'senshi', 'torrent'], run_provider=run,
                         seed=42, translation='both', progress=progress)
    after = datetime.now().astimezone()
    assert calls == [
        ('senshi', {'seed': 42, 'verify': 'http', 'translation': 'both'}),
        ('movies', {'seed': 42, 'verify': 'extraction', 'translation': 'both'}),
        ('torrent', {'seed': 42, 'verify': 'catalogue', 'translation': 'both'}),
    ]
    assert events == [
        ('progress', 1, 3, 'senshi'), ('run', 'senshi'),
        ('progress', 2, 3, 'movies'), ('run', 'movies'),
        ('progress', 3, 3, 'torrent'), ('run', 'torrent'),
    ]
    assert [p['id'] for p in report['providers']] == ['senshi', 'movies', 'torrent']
    assert [p['verification'] for p in report['providers']] == ['http', 'extraction', 'catalogue']
    assert all('default' in p['scope_note'].lower() for p in report['providers'])
    assert report['verification'] == 'mixed'
    assert report['status'] == 'complete' and report['seed'] == 42
    assert report['translation'] == 'both'
    assert [p['run_status'] for p in report['providers']] == ['complete'] * 3
    started = datetime.fromisoformat(report['started_at'])
    assert started.utcoffset() == timedelta(0) and before <= started <= after


@pytest.mark.parametrize('translation', ['sub', 'dub', 'both'])
def test_translation_is_forwarded_once_without_expanding_provider_runs(translation):
    calls = []

    def run(pid, **kwargs):
        calls.append(kwargs)
        return single_report(pid)

    report = check_batch(['senshi'], run_provider=run, seed=0, translation=translation)
    assert calls == [{'seed': 0, 'verify': 'http', 'translation': translation}]
    assert report['translation'] == translation


@pytest.mark.parametrize('requested,expected', [
    ('mpv', ['mpv', 'extraction', 'catalogue']),
    ('http', ['http', 'extraction', 'catalogue']),
    ('extraction', ['extraction', 'extraction', 'catalogue']),
])
def test_explicit_level_uses_exact_or_deepest_supported_shallower_level(requested, expected):
    calls = []

    def run(pid, **kwargs):
        calls.append(kwargs['verify'])
        return single_report(pid)

    report = check_batch(['senshi', 'movies', 'torrent'], run_provider=run, verify=requested)
    assert calls == expected
    assert [p['verification'] for p in report['providers']] == expected
    assert report['verification'] == 'mixed'
    for provider, effective in zip(report['providers'], expected):
        assert requested in provider['scope_note']
        if effective != requested:
            assert effective in provider['scope_note']
            assert 'support' in provider['scope_note'].lower()


def test_fallback_uses_depth_not_profile_order(monkeypatch):
    from media_cli.health.profiles import PROFILES

    monkeypatch.setitem(PROFILES, 'fixture', {
        'name': 'Fixture', 'levels': ('extraction', 'catalogue'), 'default': 'catalogue',
    })
    calls = []

    def run(pid, **kwargs):
        calls.append(kwargs['verify'])
        return single_report(pid)

    report = check_batch(['fixture'], run_provider=run, verify='http')
    assert calls == ['extraction']
    assert report['verification'] == 'extraction'


def test_no_supported_shallower_level_is_not_run_deeper():
    calls = []

    def run(pid, **kwargs):
        calls.append(pid)
        return single_report(pid)

    report = check_batch(['movies', 'torrent'], run_provider=run, verify='catalogue')
    assert calls == ['torrent']
    skipped = report['providers'][0]
    assert skipped['id'] == 'movies' and skipped['status'] == 'not_checked'
    assert skipped['verification'] == 'catalogue'
    assert skipped['run_status'] == 'not_started'
    assert skipped['stages'][0]['name'] == 'diagnostic_run'
    assert skipped['stages'][0]['status'] == 'not_applicable'
    assert 'support' in skipped['scope_note'].lower()
    assert 'catalogue' in skipped['scope_note']
    assert 'none executed' in skipped['scope_note'].lower()
    assert report['verification'] == 'catalogue'
    assert not any(key.startswith('_') for key in skipped)


def test_same_effective_levels_produce_same_root_level():
    report = check_batch(['movies', 'animepahe'], run_provider=lambda pid, **kw: single_report(pid),
                         verify='mpv')
    assert report['verification'] == 'extraction'


def test_provider_verdicts_and_translation_evidence_are_preserved_without_input_mutation():
    completed = single_report('senshi', verdict='working', stages=[
        {'name': 'http_media', 'status': 'passed', 'translation': 'sub', 'mirror_index': 0},
        {'name': 'http_media', 'status': 'inconclusive', 'translation': 'dub', 'mirror_index': 1},
    ])
    incomplete = single_report('movies', status='incomplete', verdict='missing_dependency')
    incomplete['providers'][0]['local_issues'] = [
        {'kind': 'executable', 'stage': 'decode', 'reason': 'Fixture dependency'},
    ]
    fixtures = {'senshi': completed, 'movies': incomplete, 'torrent': single_report('torrent')}
    original = deepcopy(fixtures)
    calls = []

    def run(pid, **kwargs):
        calls.append(pid)
        return fixtures[pid]

    report = check_batch(fixtures, run_provider=run)
    assert calls == ['senshi', 'movies', 'torrent']
    assert report['status'] == 'incomplete'
    assert [p['status'] for p in report['providers']] == ['working', 'missing_dependency', 'working']
    assert [p['run_status'] for p in report['providers']] == ['complete', 'incomplete', 'complete']
    for provider in report['providers']:
        assert {k: v for k, v in provider.items() if k not in ('verification', 'scope_note', 'run_status')} == original[provider['id']]['providers'][0]
    assert fixtures == original


@pytest.mark.parametrize('returned_cancel', [True, False])
def test_cancellation_preserves_completed_and_marks_pending(returned_cancel):
    calls = []
    finished = single_report('senshi')
    cancelled = single_report('movies', status='cancelled', verdict='inconclusive', stages=[
        {'name': 'search', 'status': 'passed'},
        {'name': 'title_details', 'status': 'not_verified'},
    ])

    def run(pid, **kwargs):
        calls.append(pid)
        if pid == 'senshi':
            return finished
        if returned_cancel:
            return cancelled
        raise KeyboardInterrupt

    report = check_batch(['senshi', 'movies', 'torrent'], run_provider=run)
    assert calls == ['senshi', 'movies']
    assert report['status'] == 'cancelled'
    assert [p['id'] for p in report['providers']] == ['senshi', 'movies', 'torrent']
    assert report['providers'][0]['status'] == 'working'
    assert [p['run_status'] for p in report['providers']] == ['complete', 'cancelled', 'not_started']
    if returned_cancel:
        assert report['providers'][1]['stages'] == cancelled['providers'][0]['stages']
    else:
        assert report['providers'][1]['status'] == 'not_checked'
        assert report['providers'][1]['stages'][0]['status'] == 'not_verified'
    pending = report['providers'][2]
    assert pending['status'] == 'not_checked'
    assert pending['stages'][0]['name'] == 'diagnostic_run'
    assert pending['stages'][0]['status'] == 'not_verified'
    assert 'not started' in pending['stages'][0]['reason'].lower()
    assert all('verification' in p and 'scope_note' in p for p in report['providers'])


def test_cancellation_overrides_prior_incomplete_run():
    fixtures = {'senshi': single_report('senshi', status='incomplete', verdict='missing_dependency'),
                'movies': single_report('movies', status='cancelled', verdict='inconclusive')}
    report = check_batch(['senshi', 'movies', 'torrent'], run_provider=lambda pid, **kw: fixtures[pid])
    assert report['status'] == 'cancelled'
    assert report['providers'][0]['status'] == 'missing_dependency'


def test_keyboard_interrupt_in_progress_leaves_current_and_remaining_unstarted():
    calls = []

    def progress(index, total, pid):
        raise KeyboardInterrupt

    report = check_batch(['senshi', 'movies'], progress=progress,
                         run_provider=lambda pid, **kw: calls.append(pid))
    assert calls == []
    assert report['status'] == 'cancelled'
    assert [p['status'] for p in report['providers']] == ['not_checked', 'not_checked']
    assert [p['run_status'] for p in report['providers']] == ['not_started', 'not_started']


@pytest.mark.parametrize('exception', [RuntimeError('token=PRIVATE'), ValueError('https://secret.example/?key=PRIVATE')])
def test_unexpected_exception_records_class_only_and_continues(exception):
    calls = []

    def run(pid, **kwargs):
        calls.append(pid)
        if pid == 'senshi':
            raise exception
        return single_report(pid)

    report = check_batch(['senshi', 'movies'], run_provider=run)
    assert calls == ['senshi', 'movies']
    provider = report['providers'][0]
    assert provider['status'] == 'inconclusive'
    assert provider['stages'] == [{'name': 'diagnostic_run', 'status': 'inconclusive',
                                    'reason': type(exception).__name__}]
    assert 'PRIVATE' not in str(report) and 'secret.example' not in str(report)
    assert report['providers'][1]['status'] == 'working'
    assert provider['run_status'] == 'incomplete'
    assert report['providers'][1]['run_status'] == 'complete'
    assert report['status'] == 'incomplete'


def test_empty_batch_has_utc_run_info_and_no_runner_calls():
    report = check_batch([], run_provider=lambda *a, **kw: pytest.fail('Unexpected run'))
    assert report['providers'] == [] and report['status'] == 'complete'
    assert report['verification'] == 'catalogue'
    assert type(report['seed']) is int and 0 <= report['seed'] < 2**63
    assert report['translation'] == 'sub'
    assert datetime.fromisoformat(report['started_at']).utcoffset() == timedelta(0)


@pytest.mark.parametrize('providers,verify', [(['unknown'], None), (['senshi'], 'invalid')])
def test_invalid_scope_is_rejected_before_running(providers, verify):
    with pytest.raises(ValueError):
        check_batch(providers, verify=verify, run_provider=lambda *a, **kw: pytest.fail('Unexpected run'))


def test_generated_seed_is_shared_and_can_replay_batch():
    calls = []

    def run(pid, **kwargs):
        calls.append((pid, kwargs))
        return single_report(pid)

    report = check_batch(['senshi', 'movies', 'torrent'], run_provider=run)
    first_calls = list(calls)
    actual_seed = report['seed']
    assert type(actual_seed) is int and 0 <= actual_seed < 2**63
    assert [kwargs['seed'] for _, kwargs in calls] == [actual_seed] * 3
    calls.clear()
    replay = check_batch(['senshi', 'movies', 'torrent'], run_provider=run, seed=actual_seed)
    assert calls == first_calls and replay['seed'] == actual_seed


def test_generated_seed_is_present_even_when_cancelled_before_first_run():
    def progress(*args):
        raise KeyboardInterrupt

    report = check_batch(['senshi'], run_provider=lambda *a, **kw: pytest.fail('Unexpected run'),
                         progress=progress, translation='both')
    assert type(report['seed']) is int and 0 <= report['seed'] < 2**63
    assert report['translation'] == 'both'
    assert report['providers'][0]['run_status'] == 'not_started'


def test_exception_after_completed_provider_keeps_earlier_verdict():
    def run(pid, **kwargs):
        if pid == 'movies':
            raise RuntimeError('PRIVATE')
        return single_report(pid)

    report = check_batch(['senshi', 'movies'], run_provider=run)
    assert report['status'] == 'incomplete'
    assert report['providers'][0]['status'] == 'working'
    assert report['providers'][0]['run_status'] == 'complete'
    assert report['providers'][1]['run_status'] == 'incomplete'
