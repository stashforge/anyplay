"""Foreground extraction races bounded jobs and hands them to the background."""

import threading
from collections import Counter

import pytest

from media_cli.core import streams


def payload():
    return {'episode': {'sourceUrls': [
        {'sourceName': name, 'sourceUrl': f'https://example.com/{name}'}
        for name in ('Rumble', 'Dailymotion', 'Odysee')
    ]}}


def video(name, quality):
    return {'source_name': name, 'source_parent_name': name, 'resolution': quality,
            'link': f'https://cdn.example.com/{name}/{quality}.mp4'}


def test_mirrors_publish_fast_source_while_first_source_is_blocked(monkeypatch, configured):
    release = threading.Event()
    published = threading.Event()
    started = threading.Event()
    calls = Counter()
    original_publish = streams._publish_stream

    def resolve(source, **kwargs):
        name = source['sourceName']
        calls[name] += 1
        if name == 'Rumble':
            started.set()
            release.wait(2)
        return [video(name, '1080p')]

    def publish(row, *args, **kwargs):
        result = original_publish(row, *args, **kwargs)
        if result and row['source_name'] == 'Dailymotion':
            published.set()
        return result

    monkeypatch.setattr(streams, 'resolve_source', resolve)
    monkeypatch.setattr(streams, '_publish_stream', publish)
    try:
        streams.start_bg_resolve(payload(), set(), 'show', '1')
        assert started.wait(1)
        assert published.wait(.75), 'Fast mirror waited for the first slow source'
        assert not release.is_set()
    finally:
        release.set()
        streams._bg_thread.join(3)
    assert calls == Counter({'Rumble': 1, 'Dailymotion': 1, 'Odysee': 1})


def test_mirrors_refill_free_slot_before_slow_sources_finish(monkeypatch, configured):
    release = threading.Event()
    fourth_started = threading.Event()

    def resolve(source, **kwargs):
        name = source['sourceName']
        if name in ('Rumble', 'Odysee'):
            release.wait(2)
        if name == 'Fourth':
            fourth_started.set()
        return [video(name, '1080p')]

    monkeypatch.setattr(streams, 'resolve_source', resolve)
    data = payload()
    data['episode']['sourceUrls'].append({'sourceName': 'Fourth', 'sourceUrl': 'https://example.com/fourth'})
    try:
        streams.start_bg_resolve(data, set(), 'show', '1')
        assert fourth_started.wait(.75), 'Free extraction slot was not refilled'
        assert not release.is_set()
    finally:
        release.set()
        streams._bg_thread.join(3)


def test_mirrors_retry_only_failed_sources_and_deduplicate_rows(monkeypatch, configured):
    calls = Counter()

    def resolve(source, **kwargs):
        name = source['sourceName']
        calls[name] += 1
        if name == 'Rumble' and calls[name] < 3:
            return []
        return [video(name, '1080p')]

    monkeypatch.setattr(streams, 'resolve_source', resolve)
    data = payload()
    data['episode']['sourceUrls'].append(dict(data['episode']['sourceUrls'][0]))
    streams.start_bg_resolve(data, set(), 'show', '1')
    streams._bg_thread.join(3)
    assert not streams._bg_thread.is_alive()
    assert calls == Counter({'Rumble': 3, 'Dailymotion': 1, 'Odysee': 1})
    assert len(streams.all_streams) == 3


@pytest.fixture
def configured(monkeypatch):
    streams._clear_streams(clear_cache=True)
    monkeypatch.setattr(streams, 'expand_direct_sources', lambda rows: rows)
    monkeypatch.setattr(streams, 'get_preferred_mirror', lambda *args: {})
    monkeypatch.setattr(streams, '_start_revalidation_timer', lambda: None)
    monkeypatch.setattr(streams, '_stream_snapshot', lambda *args, **kwargs: [])
    yield
    streams._clear_streams(clear_cache=True)


def test_slow_first_source_does_not_block_requested_quality(monkeypatch, configured):
    other_started = threading.Event()
    release = threading.Event()
    calls = Counter()

    def resolve(source, **kwargs):
        name = source['sourceName']
        calls[name] += 1
        if name == 'Rumble':
            assert other_started.wait(.75), 'Sources were extracted sequentially'
            release.wait(1)
            return [video(name, '720p')]
        other_started.set()
        return [video(name, '1080p')]

    monkeypatch.setattr(streams, 'resolve_source', resolve)
    try:
        result = streams.fetch_episode_stream('show', '1', quality='1080p', ep_data=payload())
        assert result[0]['resolution'] == '1080p'
        assert result[0]['source_name'] != 'Rumble'
        assert calls['Rumble'] == 1
    finally:
        release.set()


def test_background_reuses_started_jobs_without_duplicate_extraction(monkeypatch, configured):
    release = threading.Event()
    calls = Counter()
    published = []

    def resolve(source, **kwargs):
        name = source['sourceName']
        calls[name] += 1
        if name != 'Dailymotion':
            release.wait(2)
        return [video(name, '1080p')]

    monkeypatch.setattr(streams, 'resolve_source', resolve)
    original_publish = streams._publish_stream

    def publish(row, *args, **kwargs):
        published.append(row['source_name'])
        return original_publish(row, *args, **kwargs)

    monkeypatch.setattr(streams, '_publish_stream', publish)
    data = payload()
    try:
        result = streams.fetch_episode_stream('show', '1', quality='1080p', ep_data=data)
        assert result[0]['source_name'] == 'Dailymotion'
        streams.start_bg_resolve(data, {'Dailymotion'}, 'show', '1', quality='1080p')
        release.set()
        streams._bg_thread.join(3)
        assert not streams._bg_thread.is_alive()
        assert calls == Counter({'Rumble': 1, 'Dailymotion': 1, 'Odysee': 1})
        assert set(published) == {'Rumble', 'Odysee'}
    finally:
        release.set()


def test_cancellation_returns_without_waiting_for_slow_jobs(monkeypatch, configured):
    started = threading.Event()
    release = threading.Event()

    def resolve(source, **kwargs):
        started.set()
        release.wait(2)
        return [video(source['sourceName'], '1080p')]

    monkeypatch.setattr(streams, 'resolve_source', resolve)
    try:
        result = streams.fetch_episode_stream('show', '1', ep_data=payload(), cancel_check=started.is_set)
        assert result is None
        assert not release.is_set()
    finally:
        release.set()


def test_at_most_three_extractions_are_active(monkeypatch, configured):
    release = threading.Event()
    three_started = threading.Event()
    lock = threading.Lock()
    counts = {'active': 0, 'peak': 0, 'started': 0}
    result = []
    data = {'episode': {'sourceUrls': [{'sourceName': f'Mirror{i}', 'sourceUrl': f'https://example.com/{i}'} for i in range(6)]}}

    def resolve(source, **kwargs):
        with lock:
            counts['active'] += 1
            counts['started'] += 1
            counts['peak'] = max(counts['peak'], counts['active'])
            if counts['active'] == 3:
                three_started.set()
        try:
            release.wait(2)
            return [video(source['sourceName'], '1080p')]
        finally:
            with lock:
                counts['active'] -= 1

    monkeypatch.setattr(streams, 'resolve_source', resolve)
    owner = threading.Thread(target=lambda: result.append(streams.fetch_episode_stream('show', '1', quality='1080p', ep_data=data)), daemon=True)
    owner.start()
    try:
        assert three_started.wait(1)
        assert counts['started'] == 3
        release.set()
        owner.join(2)
        assert not owner.is_alive()
        assert result[0][0]['resolution'] == '1080p'
        assert counts['peak'] <= 3
    finally:
        release.set()
        owner.join(2)


def test_higher_quality_can_arrive_after_lower_candidate(monkeypatch, configured):
    first_ready = threading.Event()

    def resolve(source, **kwargs):
        name = source['sourceName']
        if name == 'Rumble':
            first_ready.set()
            return [video(name, '720p')]
        assert first_ready.wait(1)
        return [video(name, '1080p')]

    monkeypatch.setattr(streams, 'resolve_source', resolve)
    result = streams.fetch_episode_stream('show', '1', quality='1080p', ep_data=payload())
    assert result[0]['resolution'] == '1080p'


@pytest.mark.parametrize('higher_arrives', [True, False])
def test_requested_quality_waits_past_old_three_second_cutoff(monkeypatch, configured, higher_arrives):
    from concurrent.futures import Future

    from media_cli.core import source_batch

    clock = [0.0]

    class TimedBatch:
        def __init__(self, resolve, *, cancel_check=None):
            self.futures = {}
            self.polls = 0

        def detach_cancel_check(self):
            pass

        def poll(self, sources, seen):
            self.polls += 1
            if self.polls == 1:
                source, quality = sources[0], '720p'
            elif self.polls == 2:
                clock[0] = 4.0
                return []
            elif higher_arrives:
                clock[0] = 5.0
                source, quality = sources[1], '1080p'
            else:
                clock[0] = 10.1
                return []
            future = Future()
            future.set_result([video(source['sourceName'], quality)])
            self.futures[source_batch.source_identity(source)] = future
            return [(source, future)]

        def close(self):
            pass

    monkeypatch.setattr(source_batch, 'SourceBatch', TimedBatch)
    monkeypatch.setattr(streams.time, 'monotonic', lambda: clock[0])
    result = streams.fetch_episode_stream('show', '1', quality='1080p', ep_data=payload())
    assert result[0]['resolution'] == ('1080p' if higher_arrives else '720p')
    assert clock[0] == (5.0 if higher_arrives else 10.1)


def test_lower_quality_is_kept_after_bounded_grace(monkeypatch, configured):
    release = threading.Event()
    monkeypatch.setattr(streams, '_QUALITY_GRACE_SECONDS', .05)

    def resolve(source, **kwargs):
        if source['sourceName'] == 'Rumble':
            return [video('Rumble', '720p')]
        release.wait(2)
        return [video(source['sourceName'], '1080p')]

    monkeypatch.setattr(streams, 'resolve_source', resolve)
    try:
        result = streams.fetch_episode_stream('show', '1', quality='1080p', ep_data=payload())
        assert result[0]['resolution'] == '720p'
        assert not release.is_set()
    finally:
        release.set()


def test_cancelled_jobs_cannot_report_to_later_screen(monkeypatch, configured):
    from media_cli.core import reporting

    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    visible = []
    monkeypatch.setattr(reporting, '_status_sink', lambda message, color: visible.append(message) or True)

    def resolve(source, **kwargs):
        started.set()
        release.wait(2)
        reporting.info('LATE WORKER MESSAGE')
        finished.set()
        return []

    monkeypatch.setattr(streams, 'resolve_source', resolve)
    try:
        assert streams.fetch_episode_stream('show', '1', ep_data=payload(), cancel_check=started.is_set) is None
        release.set()
        assert finished.wait(1)
        assert not any('LATE WORKER MESSAGE' in message for message in visible)
    finally:
        release.set()


def test_duplicate_failed_source_finishes_without_busy_loop(monkeypatch, configured):
    calls = []
    cancelled = threading.Event()
    done = threading.Event()
    row = {'sourceName': 'Duplicate', 'sourceUrl': 'https://example.com/duplicate'}
    monkeypatch.setattr(streams, 'resolve_source', lambda source: calls.append(source) or [])

    def run():
        streams.fetch_episode_stream('show', '1', ep_data={'episode': {'sourceUrls': [row, dict(row)]}}, cancel_check=cancelled.is_set)
        done.set()

    owner = threading.Thread(target=run, daemon=True)
    owner.start()
    try:
        assert done.wait(1), 'Duplicate source must not prevent completion'
        assert len(calls) == 2  # One extraction per pass, not one per duplicate row.
    finally:
        cancelled.set()
        owner.join(1)


def test_cleared_episode_rejects_result_at_selection_boundary(monkeypatch, configured):
    from media_cli.media.sources import stream_quality_preference_key

    visible = []
    from media_cli.core import reporting
    monkeypatch.setattr(reporting, '_status_sink', lambda message, color: visible.append(message) or True)
    monkeypatch.setattr(streams, 'resolve_source', lambda source: [video(source['sourceName'], '1080p')])

    def rank(row, quality):
        if row.get('source_name'):
            streams._clear_streams(clear_cache=True)
        return stream_quality_preference_key(row, quality)

    monkeypatch.setattr(streams, 'stream_quality_preference_key', rank)
    result = streams.fetch_episode_stream('show', '1', quality='1080p', ep_data=payload())
    assert result is None
    assert not streams._foreground_batches
    assert not any('Connected:' in message for message in visible)


def test_background_only_job_shares_global_capacity(monkeypatch, configured):
    from media_cli.core.source_batch import SourceBatch

    release = threading.Event()
    three_started = threading.Event()
    fourth_started = threading.Event()
    count = [0]
    lock = threading.Lock()

    def blocked(source):
        with lock:
            count[0] += 1
            if count[0] == 3:
                three_started.set()
        release.wait(2)
        return []

    batch = SourceBatch(blocked)
    batch.poll(payload()['episode']['sourceUrls'], set())
    try:
        assert three_started.wait(1)
        batch.close()  # Running calls cannot be interrupted, but retain their slots.
        monkeypatch.setattr(streams, 'resolve_source', lambda source, **kwargs: fourth_started.set() or [])
        data = {'episode': {'sourceUrls': [{'sourceName': 'Background', 'sourceUrl': 'https://example.com/bg'}]}}
        streams.start_bg_resolve(data, set(), 'other', '2')
        assert not fourth_started.wait(.15), 'Background work bypassed the global extraction limit'
        release.set()
        streams._bg_thread.join(2)
        assert fourth_started.is_set()
        assert not streams._bg_thread.is_alive()
    finally:
        release.set()
        batch.close()
