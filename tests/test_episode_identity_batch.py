"""Bounded parallel reads retain the existing per-file identity rules."""

import threading
from pathlib import Path

from media_cli.media import episode_identity


def test_batch_checks_four_at_a_time_without_consuming_an_unbounded_iterator(monkeypatch):
    barrier = threading.Barrier(4, timeout=2)
    lock = threading.Lock()
    active = peak = issued = completed = 0

    def paths():
        nonlocal issued
        for i in range(12):
            with lock:
                issued += 1
                assert issued - completed <= 4
            yield Path(f'episode-{i}.mkv')

    def read(path):
        nonlocal active, peak, completed
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait()
        with lock:
            active -= 1
            completed += 1
        return episode_identity.EmbeddedEpisodeIdentity('Example', path, 'title', path)

    monkeypatch.setattr(episode_identity, 'read_episode_identity', read)
    result = episode_identity.read_episode_identities(paths())
    assert peak == 4 and completed == 12
    assert list(result) == [f'episode-{i}.mkv' for i in range(12)]
    assert all(identity.episode == path for path, identity in result.items())


def test_batch_deduplicates_paths_and_keeps_missing_identity(monkeypatch):
    calls = []
    monkeypatch.setattr(episode_identity, 'read_episode_identity', lambda path: calls.append(path))
    assert episode_identity.read_episode_identities(['one.mkv', Path('one.mkv'), 'two.mkv']) == {
        'one.mkv': None, 'two.mkv': None,
    }
    assert sorted(calls) == ['one.mkv', 'two.mkv']


def test_failed_probe_is_retried_in_next_batch(monkeypatch, tmp_path):
    path = tmp_path / 'episode.mkv'
    path.write_bytes(b'video')
    calls = []
    expected = episode_identity.EmbeddedEpisodeIdentity('Example', '2', 'title', 'episode')

    def probe(*args):
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError('Temporary probe failure')
        return expected

    monkeypatch.setattr(episode_identity.shutil, 'which', lambda name: '/test/ffprobe')
    monkeypatch.setattr(episode_identity, '_probe_identity', probe)
    assert episode_identity.read_episode_identities([path]) == {str(path): None}
    assert episode_identity.read_episode_identities([path]) == {str(path): expected}
    assert len(calls) == 2


def test_batch_rejects_identity_when_file_changes_during_probe(monkeypatch, tmp_path):
    path = tmp_path / 'episode.mkv'
    path.write_bytes(b'old')

    def probe(*args):
        path.write_bytes(b'replaced video')
        return episode_identity.EmbeddedEpisodeIdentity('Old', '1', 'title', 'episode')

    monkeypatch.setattr(episode_identity.shutil, 'which', lambda name: '/test/ffprobe')
    monkeypatch.setattr(episode_identity, '_probe_identity', probe)
    assert episode_identity.read_episode_identities([path]) == {str(path): None}
