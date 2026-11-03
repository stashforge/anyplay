"""Cached rows stay usable while stale links are checked off the UI owner."""

import threading
from types import SimpleNamespace

import pytest

from media_cli.app import playback_menu as menu
from media_cli.context import CliFlags, MachineState, UiState
from media_cli.core import streams
from media_cli.core.terminal import strip_ansi


@pytest.mark.parametrize('outcome', ['valid', 'dead', 'cancel'])
def test_cached_mirrors_open_while_validation_is_blocked_and_clear_footer(monkeypatch, outcome):
    key = streams.make_stream_key('show', '1', 'sub', 'example', 'best')
    row = {'link': 'https://example.test/video', 'source_name': 'Example', 'resolution': '720p', 'validated_at': 1}
    data = {'episode': {'sourceUrls': []}}
    entry = {'streams': [row], 'state': 'READY'}
    entered, release, completed = (threading.Event() for _ in range(3))
    owner = threading.current_thread()
    monkeypatch.setattr(streams, '_streams_cache', {key: entry})
    monkeypatch.setattr(streams, '_streams_lock', threading.RLock())
    monkeypatch.setattr(streams, '_bg_thread', None)
    monkeypatch.setattr(streams, '_bg_stats', {'resolved': 1, 'failed': 0, 'total': 1})
    monkeypatch.setattr(menu.app_core, '_get_cached_ep_data', lambda *a: data)
    monkeypatch.setattr(menu.app_core, '_set_cached_ep_data', lambda *a: None)
    monkeypatch.setattr(menu.app_core, '_stream_snapshot', streams._stream_snapshot)
    monkeypatch.setattr(menu.app_core, 'get_preferred_mirror', lambda *a: {})
    monkeypatch.setattr(menu.app_core, 'make_single_show_info_fn', lambda *a: None)
    monkeypatch.setattr(menu, '_start_torrent_mirror_augmentation', lambda *a: None)
    monkeypatch.setattr(streams, 'resolution_feedback', lambda *a: ((), None))
    jobs = []

    def health(candidate, now):
        assert threading.current_thread() is not owner
        entered.set()
        assert release.wait(2)
        candidate['validated_at'] = now
        return outcome != 'dead'

    monkeypatch.setattr(streams, 'check_stream_health_and_refresh', health)

    def pick(*args, **kwargs):
        assert entered.wait(1)
        assert args[3] and 'Example' in args[3][0]
        assert 'Checking mirror availability' in strip_ansi(kwargs['header'])
        if outcome == 'cancel':
            completed.set()
            return -2
        release.set()
        jobs[0].thread.join(1)
        options, header, finished = kwargs['live_fn']()
        assert bool(options) is (outcome == 'valid')
        assert 'Checking mirror availability' not in strip_ansi(header)
        assert finished
        completed.set()
        return -2

    original = getattr(streams, 'StreamRevalidation', None)

    def create(*args, **kwargs):
        job = original(*args, **kwargs)
        jobs.append(job)
        return job

    if original is not None:
        monkeypatch.setattr(streams, 'StreamRevalidation', create)
    monkeypatch.setattr(menu, 'tui_pick', pick)
    try:
        assert menu.handle_mirrors_state(
            CliFlags(), UiState(), MachineState(show_id='show', current_ep='1'), {},
            SimpleNamespace(provider='example'), 'sub', lambda *a: False,
        ) == 'DETAILS'
        assert completed.is_set()
    finally:
        release.set()
        for job in jobs:
            job.thread.join(1)
    if outcome == 'cancel':
        assert row['validated_at'] == 1
        assert entry['streams'] == [row]
    elif outcome == 'dead':
        assert entry['streams'] == []
    else:
        assert row['validated_at'] > 1
