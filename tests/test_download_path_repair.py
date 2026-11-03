"""A renamed managed file stays managed; copied tagged media does not."""
import pytest

from media_cli.core import storage
from media_cli.media import episode_identity


def setup_download(monkeypatch, tmp_path):
    root = tmp_path / 'downloads'
    folder = root / 'Example'
    folder.mkdir(parents=True)
    file = folder / 'Example - Episode 1.mkv'
    file.write_bytes(b'video')
    db = {'shows': {}}
    monkeypatch.setattr(storage, 'load_downloads_db', lambda: db)
    monkeypatch.setattr(storage, 'save_downloads_db', lambda data: None)
    identity = episode_identity.EmbeddedEpisodeIdentity('Example', '1', 'title', 'ep1')
    monkeypatch.setattr(episode_identity, 'read_episode_identity', lambda path: identity)
    assert storage.register_downloaded_episode_files('Example', '1', [str(file)], {'_id': 'title', 'name': 'Example'})
    return root, folder, file, db


def test_registered_rename_repairs_path_and_allows_existing_cleanup(monkeypatch, tmp_path):
    root, folder, file, db = setup_download(monkeypatch, tmp_path)
    renamed_folder = folder.rename(root / 'Renamed folder')
    renamed = (renamed_folder / file.name).rename(renamed_folder / 'renamed.mkv')
    cfg = {'download_dir': str(root)}
    assert storage.find_offline_file_for_episode('Example', '1', cfg) == str(renamed)
    assert db['shows']['Example']['episode_files']['1'] == [str(renamed)]
    assert storage.delete_offline_episode('Example', '1', cfg, require_recorded=True)
    assert not renamed.exists()


def test_copy_is_playable_but_does_not_inherit_auto_delete_permission(monkeypatch, tmp_path):
    import shutil
    root, folder, file, db = setup_download(monkeypatch, tmp_path)
    copy = folder / 'copy.mkv'
    shutil.copy2(file, copy)
    file.unlink()
    assert storage.find_offline_file_for_episode('Example', '1', {'download_dir': str(root)}) == str(copy)
    assert db['shows']['Example']['episode_files']['1'] == [str(file)]
    storage.delete_offline_episode('Example', '1', {'download_dir': str(root)}, require_recorded=True)
    assert copy.exists()


def test_legacy_registration_without_file_identity_is_not_reassigned(monkeypatch, tmp_path):
    root, folder, file, db = setup_download(monkeypatch, tmp_path)
    db['shows']['Example'].pop('episode_file_identities', None)
    renamed = file.rename(folder / 'renamed.mkv')
    assert storage.find_offline_file_for_episode('Example', '1', {'download_dir': str(root)}) == str(renamed)
    assert db['shows']['Example']['episode_files']['1'] == [str(file)]


def test_edited_file_does_not_inherit_original_cleanup_registration(monkeypatch, tmp_path):
    root, folder, file, db = setup_download(monkeypatch, tmp_path)
    renamed = file.rename(folder / 'edited.mkv')
    renamed.write_bytes(b'edited video with the same title tags')
    cfg = {'download_dir': str(root)}
    assert storage.find_offline_file_for_episode('Example', '1', cfg) == str(renamed)
    assert db['shows']['Example']['episode_files']['1'] == [str(file)]
    storage.delete_offline_episode('Example', '1', cfg, require_recorded=True)
    assert renamed.exists()


@pytest.mark.parametrize('registered_key,requested', [
    ('31', 'opaque-id'), ('31', '463'), ('opaque-id', '31'), ('opaque-id', '463'),
])
def test_renamed_registration_repaired_across_episode_aliases(monkeypatch, tmp_path, registered_key, requested):
    root, folder, file, db = setup_download(monkeypatch, tmp_path)
    db['shows'].clear()
    identity = episode_identity.EmbeddedEpisodeIdentity('Example', '31 [463]', 'title', 'opaque-id')
    monkeypatch.setattr(episode_identity, 'read_episode_identity', lambda path: identity)
    metadata = {'_id': 'title', 'name': 'Example', '_episode_labels': {'opaque-id': '31 [463]'}}
    assert storage.register_downloaded_episode_files('Example', registered_key, [str(file)], metadata)
    renamed = file.rename(folder / 'renamed.mkv')
    cfg = {'download_dir': str(root)}
    assert storage.find_offline_file_for_episode('Example', requested, cfg) == str(renamed)
    assert db['shows']['Example']['episode_files'][registered_key] == [str(renamed)]
    assert storage.delete_offline_episode('Example', registered_key, cfg, require_recorded=True)
    assert not renamed.exists()


def test_ambiguous_original_registration_is_not_reassigned(monkeypatch, tmp_path):
    root, folder, file, db = setup_download(monkeypatch, tmp_path)
    data = db['shows']['Example']
    data['episode_files']['duplicate-id'] = [str(file)]
    renamed = file.rename(folder / 'renamed.mkv')
    assert storage.find_offline_file_for_episode('Example', '1', {'download_dir': str(root)}) == str(renamed)
    assert data['episode_files']['1'] == [str(file)]
    assert data['episode_files']['duplicate-id'] == [str(file)]
