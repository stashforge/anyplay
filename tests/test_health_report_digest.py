"""Offline rendering regressions for scope, attribution and recorded outcomes."""
from copy import deepcopy

import pytest

from media_cli.core.terminal import display_width
from media_cli.domain.health_report import format_health_report


def report(stages, **provider):
    return dict(started_at='2026-10-30T10:00:00+00:00', status='complete',
                verification='http', providers=[dict(id='example', name='Example',
                status='inconclusive', stages=stages, **provider)])


def stage(name, status='passed', **fields):
    return dict(name=name, status=status, title='Sample', **fields)


@pytest.mark.parametrize(('index', 'number'), [(0, 1), (1, 2)])
def test_probe_and_descriptor_use_same_human_mirror_number_without_mutation(index, number):
    mirrors = [dict(resolution=resolution, type='hls', host='cdn.example',
                    separate_audio=False, subtitle_count=0, headers_present=False)
               for resolution in ('720p', '480p')]
    data = report([stage('source_resolution', mirrors=mirrors),
                   stage('media_access', track='video', mirror_index=index)])
    before = deepcopy(data)
    text = format_health_report(data)
    assert f'Mirror {number}' in text and f'Mirror number: {number}' in text
    assert 'Mirror index:' not in text
    assert data == before


@pytest.mark.parametrize(('translations', 'expected'), [
    (['sub'], 'Sub only'), (['dub'], 'Dub only'),
    (['sub', 'dub'], 'Sub and Dub'), ([], 'Unspecified / unverified'),
])
def test_scope_uses_only_recorded_stage_translations(translations, expected):
    rows = [stage('episode_catalogue', translation=value) for value in translations]
    text = format_health_report(report(rows))
    assert f'Translation scope: {expected}' in text
    if translations == ['sub']:
        assert 'Sub and Dub' not in text


def test_scope_counts_each_stage_and_cohort_without_borrowing_search_success():
    data = report([stage('search', age_group='older'), stage('search')])
    data['providers'][0]['stages'][1].update(title='Other', age_group='recent')
    data['providers'][0]['stages'].append(stage('media_access', 'failed', track='video',
                                              age_group='older', translation='sub'))
    text = format_health_report(data)
    scope = text.split('SAMPLE SCOPE')[1].split('CHECK SUMMARY')[0]
    assert '2 recorded title labels' in scope
    assert 'Search: 2 stages; 2 title labels' in scope
    assert 'Video access' not in scope
    assert 'Video access' in text.split('CHECK SUMMARY')[1]
    assert 'older: 1' in scope and 'recent: 1' in scope
    assert 'complete coverage' not in scope.lower()


def test_digest_precedes_details_and_separates_catalogue_media_and_decoder():
    data = report([stage('title_details', 'inconclusive', reason='Metadata fallback'),
                   stage('media_access', track='video', bytes_sampled=24),
                   stage('media_access', 'not_verified', track='audio', required=False),
                   stage('media_access', 'skipped', track='subtitle'),
                   stage('playback', 'not_verified', issue_scope='local', reason='Decoder unavailable')])
    before = deepcopy(data)
    text = format_health_report(data)
    assert text.index('TITLE DIGEST') < text.index('TITLE DETAILS')
    digest = text.split('TITLE DIGEST')[1].split('TITLE DETAILS')[0]
    assert 'Catalogue confidence' in digest and '1 Inconclusive' in digest
    assert 'Observed media / decoder' in digest
    for value in ('Video access: 1 Passed', 'Audio access: 1 Not required',
                  'Subtitle access: 1 Skipped', '1 Unavailable locally (Unverified)'):
        assert value in digest
    assert 'Recorded verdict: Inconclusive' in text
    assert 'Metadata fallback' in text and 'Bytes sampled: 24' in text
    assert data == before


def test_provider_summary_does_not_turn_catalogue_success_into_media_verification():
    text = format_health_report(report([stage('search')]))
    summary = text.split('SAMPLE SCOPE')[0]
    assert 'Catalogue confidence: 1 Passed' in summary
    assert 'Observed media / decoder: Unverified' in summary


def test_missing_and_conflicting_ids_remain_local_under_duplicate_title_labels():
    data = report([stage('title_details', provider_title_id='id-A'),
                   stage('episode_catalogue', mal_id=123),
                   stage('source_resolution', provider_title_id='id-B'),
                   stage('media_access', track='video', mal_id=123)])
    text = format_health_report(data)
    details = text.split('TITLE DETAILS')[1]
    assert 'TITLE REFERENCES' not in details
    assert details.count('MAL ID: 123') == 2
    assert 'Provider title id: id-A' in details and 'Provider title id: id-B' in details
    assert 'matching labels do not establish identity' in ' '.join(text.split())


def test_consistent_references_can_still_be_shown_once_when_search_has_no_id():
    rows = [stage('search')] + [stage(name, provider_title_id='shared-id') for name in
                               ('title_details', 'source_resolution', 'media_access')]
    text = format_health_report(report(rows))
    assert text.count('shared-id') == 1
    assert 'TITLE REFERENCES' in text


def test_provider_and_recorded_stream_type_are_visible_without_inferring_translation():
    mirror = dict(resolution='720p', type='hls', host='cdn.example', separate_audio=False,
                  subtitle_count=0, headers_present=False)
    text = format_health_report(report([stage('source_resolution', mirrors=[mirror])]))
    assert 'Example (example)' in text
    assert 'Recorded stream types: hls' in text
    assert 'Translation scope: Unspecified / unverified' in text


def test_narrow_digest_preserves_unicode_and_all_statuses():
    rows = [stage('media_access', status, track=track, translation='dub') for status, track in
            [('passed', 'video'), ('skipped', 'subtitle'), ('not_applicable', 'audio')]]
    for row in rows:
        row['title'] = '斗罗大陆 很长的标题'
    text = format_health_report(report(rows), width=32)
    assert all(display_width(line) <= 32 for line in text.splitlines())
    compact = ' '.join(text.split())
    for value in ('Dub only', 'Passed', 'Skipped', 'Not applicable', 'TITLE DIGEST'):
        assert value in compact


def test_recorded_working_verdict_does_not_invent_http_checks_from_search_success():
    data = report([stage('search')])
    data['providers'][0]['status'] = 'working'
    text = format_health_report(data)
    assert 'Recorded verdict: Working' in text
    assert 'HTTP checked' not in text
    assert 'Observed media / decoder: Unverified' in text


def test_summary_does_not_count_derived_catalogue_verdict_as_another_sample():
    text = format_health_report(report([stage('title_details', 'failed'),
                                       dict(name='catalogue_summary', status='passed')]))
    summary = text.split('SAMPLE SCOPE')[0]
    assert 'Catalogue confidence: 1 Failed' in summary
    assert '1 Passed' not in summary


def test_unverified_and_not_required_remain_distinct_in_digest():
    text = format_health_report(report([
        stage('media_access', 'not_verified', track='audio', required=True),
        stage('media_access', 'not_verified', track='subtitle', required=False),
    ]))
    digest = text.split('TITLE DIGEST')[1].split('TITLE DETAILS')[0]
    assert 'Audio access: 1 Unverified' in digest
    assert 'Subtitle access: 1 Not required' in digest


def test_source_extraction_does_not_count_as_observed_media_or_decoder():
    text = format_health_report(report([stage('source_resolution')]))
    summary = text.split('SAMPLE SCOPE')[0]
    assert 'Observed media / decoder: Unverified' in summary


def test_per_title_scope_does_not_borrow_other_titles_translation():
    data = report([stage('episode_catalogue', translation='sub'),
                   dict(name='episode_catalogue', status='not_verified', title='Other', translation='dub')])
    text = format_health_report(data)
    assert 'Translation scope: Sub and Dub' in text.split('TITLE DIGEST')[0]
    digest = text.split('TITLE DIGEST')[1].split('TITLE DETAILS')[0]
    assert 'Sample\n  Translation scope: Sub only' in digest
    assert 'Other\n  Translation scope: Dub only' in digest
    assert 'Catalogue confidence: 1 Unverified' in digest


@pytest.mark.parametrize(('verification', 'name', 'label'), [
    ('http', 'media_access', 'HTTP checked'), ('mpv', 'playback', 'decode checked'),
])
def test_mixed_batch_uses_effective_provider_verification(verification, name, label):
    data = report([stage(name, track='video')], verification=verification,
                  run_status='complete', translation='both', scope_note='Provider default depth.')
    data['verification'] = 'mixed'
    data['providers'][0]['status'] = 'working'
    before = deepcopy(data)
    text = format_health_report(data)
    assert f'Working — {label}' in text
    assert f'Effective verification: {verification}' in text
    assert 'Provider run state: complete' in text
    assert 'Translation requested: both' in text
    assert 'Scope note: Provider default depth.' in text
    assert 'mixed checked' not in text
    assert data == before


def test_provider_metadata_remains_visible_when_no_run_started():
    data = report([], verification='catalogue', run_status='not_started',
                  translation='sub', scope_note='Not reached before cancellation.')
    data.update(status='cancelled', verification='mixed')
    text = format_health_report(data)
    assert 'Effective verification: catalogue' in text
    assert 'Provider run state: not_started' in text
    assert 'Scope note: Not reached before cancellation.' in text
    assert 'Translation scope: Unspecified / unverified' in text


def test_translation_results_keep_recorded_channel_verdicts_separate_from_overall():
    rows = [stage('episode_catalogue', translation='sub'),
            dict(name='translation_summary', status='inconclusive', translation='sub',
                 reason='Recorded Sub verdict: partly_working; some sampled sources failed.'),
            dict(name='translation_summary', status='not_verified', translation='dub',
                 reason='Recorded Dub verdict: not_checked; cancelled before work.')]
    data = report(rows, translation='both')
    text = format_health_report(data)
    assert text.index('TRANSLATION RESULTS') < text.index('CHECK SUMMARY')
    results = ' '.join(text.split('TRANSLATION RESULTS')[1].split('CHECK SUMMARY')[0].split())
    assert 'Sub — Inconclusive' in results and 'Dub — Unverified' in results
    assert 'Recorded Sub verdict: partly_working;' in results
    assert 'Recorded Dub verdict: not_checked;' in results
    summary = text.split('CHECK SUMMARY')[1].split('FINDINGS')[0]
    assert 'Translation summary' not in summary
    assert text.count('Recorded Sub verdict:') == 1
    assert text.count('Recorded Dub verdict:') == 1
    assert 'Recorded verdict: Inconclusive' in text
    assert 'Translation scope: Sub only' in text


def test_unsupported_dub_does_not_expand_observed_scope_from_requested_both():
    data = report([stage('episode_catalogue', translation='sub'),
                   dict(name='translation_support', status='not_applicable', translation='dub',
                        reason='Provider declares Dub unsupported.')], translation='both')
    text = format_health_report(data)
    scope = text.split('SAMPLE SCOPE')[1].split('CHECK SUMMARY')[0]
    assert 'Translation scope: Sub only' in scope
    assert 'Sub and Dub' not in scope
    assert 'Provider declares Dub unsupported.' in text
    unsupported_only = report([dict(name='translation_support', status='not_applicable',
                                   translation='dub', reason='Unsupported')])
    assert 'Translation scope: Unspecified / unverified' in format_health_report(unsupported_only)


def test_sample_scope_stays_bounded_when_media_and_placeholders_grow():
    samples = [stage(name, age_group='recent', translation='sub') for name in
               ('search', 'title_identity', 'title_details', 'episode_catalogue',
                'source_discovery', 'mirror_extraction', 'source_resolution')]
    before = format_health_report(report(samples)).split('SAMPLE SCOPE')[1].split('CHECK SUMMARY')[0]
    rows = samples + [stage('media_access', track='video', age_group='older',
                            translation='sub', bytes_sampled=123) for _ in range(200)]
    rows += [dict(name=name, status='not_verified', reason='Not checked') for name in
             ('playback', 'source_coverage', 'diagnostic_run', 'source_discovery', 'title_details')]
    text = format_health_report(report(rows))
    after = text.split('SAMPLE SCOPE')[1].split('CHECK SUMMARY')[0]
    assert after == before
    assert 'Video access' not in after and 'Playback' not in after
    assert 'Source discovery' in after and 'Mirror extraction' in after
    assert 'recent: 1' in after
    assert text.count('Bytes sampled: 123') == 200
    assert len(after.splitlines()) <= 18


def test_batch_metadata_and_translation_results_wrap_at_32_columns():
    data = report([dict(name='translation_summary', status='passed', translation='sub',
                        reason='Recorded channel verdict: working for sampled titles.')],
                  verification='http', translation='both', run_status='complete',
                  scope_note='Bounded samples; whole-library coverage remains unverified.')
    text = format_health_report(data, width=32)
    assert all(display_width(line) <= 32 for line in text.splitlines())
    compact = ' '.join(text.split())
    assert 'TRANSLATION RESULTS' in compact
    assert 'Recorded channel verdict: working for sampled titles.' in compact
