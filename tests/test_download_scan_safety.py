"""An uncertain filesystem scan must never prune established download records."""
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from media_cli.app import downloads


@pytest.fixture
def scan(monkeypatch, tmp_path):
    folder = tmp_path / 'Known'
    folder.mkdir()
    video = folder / 'Episode 1.mkv'
    video.write_bytes(b'\x00' * 4096)
    data = {'current_download_dir': str(tmp_path), 'shows': {'Known': {
        'metadata': {'name': 'Known', '_id': 'title', 'downloadedCount': 1},
        'episodes': ['1'], 'watched_episodes': ['1'], 'episode_files': {'1': [str(video)]}}}}
    monkeypatch.setattr(downloads.app_core, 'get_history_entry', lambda *a: None)
    monkeypatch.setattr(downloads, 'read_episode_identities', lambda paths: {})
    monkeypatch.setattr(downloads, 'read_episode_identity', lambda *a: None)
    save = Mock()
    monkeypatch.setattr(downloads, 'save_downloads_db', save)
    return tmp_path, folder, video, data, save


@pytest.mark.parametrize('persist', [True, False])
def test_unreadable_title_directory_preserves_record_and_visible_title(scan, monkeypatch, persist):
    root, folder, video, data, save = scan
    before = deepcopy(data)
    original = downloads.os.listdir
    def listdir(path):
        if Path(path) == folder:
            raise PermissionError('Unreadable folder')
        return original(path)
    monkeypatch.setattr(downloads.os, 'listdir', listdir)
    _, titles, dirty = downloads._scan_downloads(data, str(root), 'sub', persist=persist)
    assert data == before
    assert dict(titles)['Known']['episodes'] == ['1']
    assert dirty is False and save.call_count == 0
    assert video.exists()


def test_unreadable_root_preserves_cached_records(scan, monkeypatch):
    root, folder, video, data, save = scan
    before = deepcopy(data)
    original = downloads.os.listdir
    monkeypatch.setattr(downloads.os, 'listdir', lambda path: (_ for _ in ()).throw(PermissionError())
                        if Path(path) == root else original(path))
    _, titles, dirty = downloads._scan_downloads(data, str(root), 'sub')
    assert data == before and dict(titles)['Known']['episodes'] == ['1']
    assert dirty is False
    save.assert_not_called()


def test_unreadable_file_does_not_look_like_missing_video(scan, monkeypatch):
    import builtins
    root, folder, video, data, save = scan
    before = deepcopy(data)
    original = builtins.open
    def open_file(path, *args, **kwargs):
        if Path(path) == video:
            raise PermissionError('Temporarily inaccessible')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(builtins, 'open', open_file)
    _, titles, dirty = downloads._scan_downloads(data, str(root), 'sub')
    assert data == before and dict(titles)['Known']['episodes'] == ['1']
    assert dirty is False
    save.assert_not_called()


def test_uncertain_directory_stat_does_not_prune_entry(scan, monkeypatch):
    root, folder, video, data, save = scan
    before = deepcopy(data)
    original = downloads.os.stat
    def stat(path, *args, **kwargs):
        if Path(path) == folder:
            raise PermissionError('Cannot inspect folder')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(downloads.os, 'stat', stat)
    _, titles, dirty = downloads._scan_downloads(data, str(root), 'sub')
    assert data == before and dict(titles)['Known']['episodes'] == ['1']
    assert dirty is False
    save.assert_not_called()


def test_confirmed_empty_directory_can_still_be_pruned(scan):
    root, folder, video, data, save = scan
    video.unlink()
    _, titles, dirty = downloads._scan_downloads(data, str(root), 'sub')
    assert data['shows'] == {} and titles == [] and dirty
    save.assert_called()


def test_valid_scan_recovers_after_temporary_read_failure(scan, monkeypatch):
    root, folder, video, data, save = scan
    original = downloads.os.listdir
    blocked = [True]
    def listdir(path):
        if Path(path) == folder and blocked[0]:
            raise PermissionError()
        return original(path)
    monkeypatch.setattr(downloads.os, 'listdir', listdir)
    downloads._scan_downloads(data, str(root), 'sub')
    blocked[0] = False
    _, titles, _ = downloads._scan_downloads(data, str(root), 'sub')
    assert dict(titles)['Known']['episodes'] == ['1']


def test_unreadable_title_survives_when_another_empty_title_is_cleaned(scan, monkeypatch):
    root, folder, video, data, save = scan
    before = deepcopy(data['shows']['Known'])
    (root / 'Empty').mkdir()
    data['shows']['Empty'] = {'metadata': {'name': 'Empty'}, 'episodes': ['1']}
    original = downloads.os.listdir
    def listdir(path):
        if Path(path) == folder:
            raise PermissionError()
        return original(path)
    monkeypatch.setattr(downloads.os, 'listdir', listdir)
    _, titles, dirty = downloads._scan_downloads(data, str(root), 'sub')
    assert data['shows'] == {'Known': before}
    assert dict(titles)['Known'] == before and dirty
    save.assert_called()
    assert save.call_args.args[0]['shows']['Known'] == before


def test_file_becoming_uninspectable_after_probe_keeps_old_record(scan, monkeypatch):
    root, folder, video, data, save = scan
    before = deepcopy(data)
    blocked = [False]
    original = downloads.os.stat
    def stat(path, *args, **kwargs):
        if Path(path) == video and blocked[0]:
            raise PermissionError()
        return original(path, *args, **kwargs)
    def identities(paths):
        blocked[0] = True
        return {}
    monkeypatch.setattr(downloads.os, 'stat', stat)
    monkeypatch.setattr(downloads, 'read_episode_identities', identities)
    _, titles, dirty = downloads._scan_downloads(data, str(root), 'sub')
    assert data == before and dict(titles)['Known']['episodes'] == ['1']
    assert dirty is False
    save.assert_not_called()
