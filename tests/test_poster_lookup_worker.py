"""Cover discovery must not perform tool or filesystem checks on the UI thread."""

import hashlib
import threading

import pytest

from media_cli.ui import poster as module
from media_cli.ui.poster import PosterManager


def make_manager(tmp_path):
    return PosterManager(enabled=lambda: True, cache_dir=lambda: str(tmp_path),
                         hovered_show_id=lambda: None, request_redraw=lambda: None,
                         loading_frame=lambda: '*')


@pytest.mark.parametrize('source', ['show', 'memory'])
def test_ready_cover_never_checks_tools_or_cache_paths(monkeypatch, tmp_path, source):
    manager = make_manager(tmp_path)
    show = {'_id': 'one', 'thumbnail': 'https://example.test/cover.jpg'}
    if source == 'show':
        show['_poster_raw'] = 'pixels'
    else:
        manager._raw_cache[hashlib.sha256(show['thumbnail'].encode()).hexdigest()[:32]] = 'pixels'
    def forbidden(*args, **kwargs):
        pytest.fail('In-memory cover performed tool/disk/worker work')
    monkeypatch.setattr(module.shutil, 'which', forbidden)
    monkeypatch.setattr(module.threading, 'Thread', forbidden)
    manager.read_cache_dirs = manager.cache_dir = forbidden
    assert manager.get(show) == 'pixels'


@pytest.mark.parametrize('blocked_stage', ['tool', 'read_cache', 'write_cache'])
def test_blocked_lookup_is_background_and_repeated_reads_share_worker(monkeypatch, tmp_path, blocked_stage):
    manager = make_manager(tmp_path)
    owner = threading.current_thread()
    entered, release, complete = threading.Event(), threading.Event(), threading.Event()
    threads = []
    original_thread = threading.Thread
    def tracked_thread(*args, **kwargs):
        thread = original_thread(*args, **kwargs)
        threads.append(thread)
        return thread
    def inspect(stage, result):
        assert threading.current_thread() is not owner, 'Cover lookup ran on the UI thread'
        if stage == blocked_stage:
            entered.set()
            assert release.wait(2)
        return result
    monkeypatch.setattr(module.threading, 'Thread', tracked_thread)
    monkeypatch.setattr(module.shutil, 'which', lambda *a: inspect('tool', '/bin/chafa'))
    manager.read_cache_dirs = lambda: inspect('read_cache', [])
    manager.cache_dir = lambda: inspect('write_cache', str(tmp_path))
    manager._download = lambda *a, **kw: complete.set()
    show = {'_id': 'one', 'thumbnail': 'https://example.test/cover.jpg'}
    try:
        assert manager.get(show) == ''
        assert entered.wait(1)
        for _ in range(10):
            assert manager.get(show) == ''
        assert len(threads) == 1
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=2)
    assert complete.is_set()
    assert not manager.active_downloads


@pytest.mark.parametrize('missing_tool', [False, True])
def test_worker_failure_and_missing_tool_release_pending_state(monkeypatch, tmp_path, missing_tool):
    manager = make_manager(tmp_path)
    show = {'_id': 'one', 'thumbnail': 'https://example.test/cover.jpg'}
    threads = []
    original_thread = threading.Thread
    def tracked_thread(*args, **kwargs):
        thread = original_thread(*args, **kwargs)
        threads.append(thread)
        return thread
    monkeypatch.setattr(module.threading, 'Thread', tracked_thread)
    monkeypatch.setattr(module.shutil, 'which', lambda *a: None if missing_tool else '/bin/chafa')
    def unreadable():
        raise OSError('private-cache-path')
    manager.read_cache_dirs = unreadable
    assert manager.get(show) == ''
    for thread in threads:
        thread.join(timeout=2)
        assert not thread.is_alive()
    assert show['_poster_status'] == ('no_chafa' if missing_tool else 'failed')
    assert not manager.active_downloads
