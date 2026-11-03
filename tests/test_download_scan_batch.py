"""Scan uses bounded probing without persisting failures or stale file tags."""

import threading
from collections import Counter

from media_cli.app import downloads
from media_cli.media import episode_identity


def setup_scan(monkeypatch, tmp_path):
    known, discovered = tmp_path / 'Known', tmp_path / 'Discovered'
    known.mkdir()
    discovered.mkdir()
    renamed = known / 'Episode 99.mkv'
    failed = known / 'Episode 4.mkv'
    other = discovered / 'Episode 20.mkv'
    for path in (renamed, failed, other):
        path.write_bytes(b'\x00' * 4096)
    calls = Counter()
    threads = []

    def probe(path, executable, stamp):
        calls[path] += 1
        threads.append(threading.get_ident())
        if path == str(failed):
            raise RuntimeError('Temporary probe failure')
        episode = '5' if stamp[2] > 4096 else ('2' if path == str(renamed) else '3')
        return episode_identity.EmbeddedEpisodeIdentity('Example', episode, 'title', episode)

    monkeypatch.setattr(episode_identity, '_probe_identity', probe)
    monkeypatch.setattr(episode_identity.shutil, 'which', lambda name: '/test/ffprobe')
    monkeypatch.setattr(downloads.app_core, 'get_history_entry', lambda *a: None)
    monkeypatch.setattr(downloads, 'save_downloads_db', lambda *a: (_ for _ in ()).throw(AssertionError('Detached scan wrote DB')))
    db = {'current_download_dir': str(tmp_path), 'shows': {'Known': {
        'metadata': {'name': 'Known', '_id': 'title'}, 'episodes': ['99'],
        'episode_files': {'2': [str(failed)]},
    }}}
    return db, renamed, failed, other, calls, threads


def test_scan_batches_known_and_discovered_files_and_retries_failure_next_scan(monkeypatch, tmp_path):
    db, renamed, failed, other, calls, threads = setup_scan(monkeypatch, tmp_path)
    owner = threading.get_ident()
    _, titles, _ = downloads._scan_downloads(db, str(tmp_path), 'sub', persist=False)
    assert all(thread != owner for thread in threads), 'Scan probed files serially on its caller'
    assert calls == {str(renamed): 1, str(failed): 1, str(other): 1}
    assert {name: data['episodes'] for name, data in titles} == {'Known': ['2', '4'], 'Discovered': ['3']}
    downloads._scan_downloads(db, str(tmp_path), 'sub', persist=False)
    assert calls[str(failed)] == 2, 'Probe failures must be retryable next scan'


def test_scan_rechecks_file_replaced_after_parallel_probe(monkeypatch, tmp_path):
    db, renamed, failed, other, calls, threads = setup_scan(monkeypatch, tmp_path)
    read_batch = episode_identity.read_episode_identities

    def batch(paths):
        result = read_batch(paths)
        renamed.write_bytes(b'\x00' * 8192)
        return result

    monkeypatch.setattr(downloads, 'read_episode_identities', batch, raising=False)
    _, titles, _ = downloads._scan_downloads(db, str(tmp_path), 'sub', persist=False)
    assert dict(titles)['Known']['episodes'] == ['4', '5']
    assert calls[str(renamed)] >= 2


def test_scan_does_not_count_file_removed_during_parallel_probe(monkeypatch, tmp_path):
    db, renamed, failed, other, calls, threads = setup_scan(monkeypatch, tmp_path)
    read_batch = episode_identity.read_episode_identities

    def batch(paths):
        result = read_batch(paths)
        renamed.unlink()
        return result

    monkeypatch.setattr(downloads, 'read_episode_identities', batch)
    _, titles, _ = downloads._scan_downloads(db, str(tmp_path), 'sub', persist=False)
    assert dict(titles)['Known']['episodes'] == ['4']
