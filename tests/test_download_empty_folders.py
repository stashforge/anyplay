"""Episode cleanup removes only genuinely empty managed show directories."""
from pathlib import Path

import pytest

from media_cli.core import storage


@pytest.mark.parametrize("leftover", [None, "notes.txt", "Episode 2.mkv", ".hidden"])
def test_last_episode_removes_folder_only_when_completely_empty(monkeypatch, tmp_path, leftover):
    root = tmp_path / "downloads"
    folder = root / "Show"
    folder.mkdir(parents=True)
    episode = folder / "Show - Episode 1.mkv"
    episode.write_bytes(b"video")
    if leftover:
        (folder / leftover).write_text("keep")
    db = {"shows": {"Show": {"episodes": ["1"], "episode_files": {"1": [str(episode)]}}}}
    monkeypatch.setattr(storage, "load_downloads_db", lambda: db)
    monkeypatch.setattr(storage, "save_downloads_db", lambda *a: None)
    assert storage.delete_offline_episode("Show", "1", {"download_dir": str(root)}, require_recorded=True)
    assert not episode.exists()
    assert folder.exists() is bool(leftover)
    assert root.is_dir()
    if leftover:
        assert (folder / leftover).read_text() == "keep"


@pytest.mark.parametrize("location", ["root", "outside"])
def test_cleanup_never_removes_download_root_or_unrelated_parent(monkeypatch, tmp_path, location):
    root = tmp_path / "downloads"
    root.mkdir()
    parent = root if location == "root" else tmp_path / "other"
    parent.mkdir(exist_ok=True)
    episode = parent / "Episode 1.mkv"
    episode.write_bytes(b"video")
    db = {"shows": {"Show": {"episodes": ["1"], "episode_files": {"1": [str(episode)]}}}}
    monkeypatch.setattr(storage, "load_downloads_db", lambda: db)
    monkeypatch.setattr(storage, "save_downloads_db", lambda *a: None)
    assert storage.delete_offline_episode("Show", "1", {"download_dir": str(root)}, require_recorded=True)
    assert parent.is_dir() and root.is_dir()
