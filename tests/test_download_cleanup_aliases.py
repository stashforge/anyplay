"""Cleanup resolves provider and dual-number aliases to one saved episode."""
import pytest
from media_cli.core import storage
from media_cli.media import episode_identity


@pytest.mark.parametrize('key,requested', [('31', 'opaque-id'), ('31', '463'), ('opaque-id', '31'), ('opaque-id', '463')])
def test_cleanup_alias_removes_only_target_episode(monkeypatch, tmp_path, key, requested):
    folder = tmp_path / 'Example'
    folder.mkdir()
    target = folder / 'renamed.mkv'
    other = folder / 'other.mkv'
    target.write_bytes(b'video')
    other.write_bytes(b'other')
    data = {'episodes': [key, '32'], 'watched_episodes': [key, '32'],
            'episode_files': {key: [str(target)], '32': [str(other)]},
            'metadata': {'_episode_labels': {'opaque-id': '31 [463]'}}}
    db = {'shows': {'Example': data}}
    monkeypatch.setattr(storage, 'load_downloads_db', lambda: db)
    monkeypatch.setattr(storage, 'save_downloads_db', lambda data: None)
    monkeypatch.setattr(episode_identity, 'read_episode_identity', lambda path:
        episode_identity.EmbeddedEpisodeIdentity('Example', '31 [463]', 'title', 'opaque-id'))
    assert storage.delete_offline_episode('Example', requested, {'download_dir': str(tmp_path)}, require_recorded=True)
    assert not target.exists()
    assert other.exists()
    assert data['episodes'] == ['32']
    assert data['watched_episodes'] == ['32']
    assert list(data['episode_files']) == ['32']
    assert data['metadata']['downloadedCount'] == 1


def test_ambiguous_absolute_number_does_not_delete_anything(monkeypatch, tmp_path):
    videos = [tmp_path / 'a.mkv', tmp_path / 'b.mkv']
    for file in videos:
        file.write_bytes(b'video')
    db = {'shows': {'Example': {'episode_files': {'one': [str(videos[0])], 'two': [str(videos[1])]},
          'metadata': {'_episode_labels': {'one': '31 [463]', 'two': '32 [463]'}}}}}
    monkeypatch.setattr(storage, 'load_downloads_db', lambda: db)
    monkeypatch.setattr(storage, 'save_downloads_db', lambda data: pytest.fail('Ambiguous request changed registry'))
    assert not storage.delete_offline_episode('Example', '463', {'download_dir': str(tmp_path)}, require_recorded=True)
    assert all(file.exists() for file in videos)


def test_ambiguous_catalog_alias_is_rejected_even_if_only_one_is_downloaded(monkeypatch, tmp_path):
    video = tmp_path / 'a.mkv'
    video.write_bytes(b'video')
    db = {'shows': {'Example': {'episode_files': {'one': [str(video)]},
          'metadata': {'_episode_labels': {'one': '31 [463]', 'two': '32 [463]'}}}}}
    monkeypatch.setattr(storage, 'load_downloads_db', lambda: db)
    monkeypatch.setattr(storage, 'save_downloads_db', lambda data: pytest.fail('Ambiguous request changed registry'))
    monkeypatch.setattr(episode_identity, 'read_episode_identity', lambda path:
        episode_identity.EmbeddedEpisodeIdentity('Example', '31 [463]', 'title', 'one'))
    assert not storage.delete_offline_episode('Example', '463', {'download_dir': str(tmp_path)}, require_recorded=True)
    assert video.exists()
