"""Download scanning prefers embedded identity, then safe saved associations."""

from types import SimpleNamespace
from unittest.mock import Mock
import shutil
import subprocess

import pytest

from media_cli.app import downloads
from media_cli.context import CliFlags, MachineState, UiState
from media_cli.media import episode_identity
from media_cli.media.episode_identity import EmbeddedEpisodeIdentity


@pytest.fixture
def identity_reader(monkeypatch):
    reader = Mock(return_value=None)
    monkeypatch.setattr(downloads, "read_episode_identity", reader, raising=False)
    monkeypatch.setattr(episode_identity, "read_episode_identity", reader)
    return reader


def test_embedded_label_wins_over_misleading_renamed_filename(identity_reader, tmp_path):
    path = tmp_path / "Episode 99.mkv"
    identity_reader.return_value = EmbeddedEpisodeIdentity("Example", "31 [463]", "show-id", "ep-id")
    assert downloads.extract_offline_episode_info(str(path)) == (31.0, 463.0, "31", "31 [463]")


def test_saved_path_association_precedes_filename(identity_reader, tmp_path):
    path = tmp_path / "unrelated.mkv"
    path.write_bytes(b"video")
    data = {"metadata": {"_episode_labels": {"ep-id": "31 [463]"}}, "episode_files": {"ep-id": [str(path)]}}
    assert downloads.extract_offline_episode_info(str(path), show_data=data) == (31.0, 463.0, "31", "31 [463]")


def test_saved_contradictory_alias_is_not_trusted(identity_reader, tmp_path):
    path = tmp_path / "Example Season 10 - Episode 31 [463].mkv"
    data = {"episode_files": {"10": [str(path)]}}
    assert downloads.extract_offline_episode_info(str(path), show_data=data)[2:] == ("31", "31 [463]")


def test_untagged_unrecorded_files_keep_filename_fallback(identity_reader):
    assert downloads.extract_offline_episode_info("Example - Episode 2.mkv")[2:] == ("2", "2")


def test_tags_override_filename_guard_without_authorizing_other_episode(identity_reader):
    identity_reader.return_value = EmbeddedEpisodeIdentity("Example", "31 [463]", "show-id", "ep-id")
    assert not episode_identity.download_identity_conflicts("31 [463]", "Episode 99.mkv")
    assert not episode_identity.download_identity_conflicts("ep-id", "Episode 99.mkv")
    assert episode_identity.download_identity_conflicts("99", "Episode 99.mkv")


def test_download_scan_and_open_use_renamed_file_identity(identity_reader, monkeypatch, tmp_path):
    folder = tmp_path / "Example"
    folder.mkdir()
    path = folder / "Episode 99.mkv"
    path.write_bytes(b"\0" * 2048)
    identity_reader.return_value = EmbeddedEpisodeIdentity("Example", "31 [463]", "show-id", "ep-id")
    db = {"current_download_dir": str(tmp_path), "shows": {"Example": {
        "episodes": ["99"], "metadata": {"_id": "show-id", "name": "Example", "_provider": "example",
        "_episode_ids": ["ep-id"], "_episode_labels": {"ep-id": "31 [463]"}},
    }}}
    monkeypatch.setattr(downloads, "load_downloads_db", lambda: db)
    monkeypatch.setattr(downloads, "save_downloads_db", Mock())
    monkeypatch.setattr(downloads.app_core, "get_history_entry", lambda *a: None)
    monkeypatch.setattr(downloads.app_core, "is_online", lambda: False)
    monkeypatch.setattr(downloads.app_core, "get_local_progress", lambda *a: None)
    monkeypatch.setattr(downloads.app_core, "get_local_episode_label", lambda *a: None)
    monkeypatch.setattr(downloads, "set_ui_context", Mock())
    monkeypatch.setattr(downloads, "tui_pick", lambda *a, **kw: 0)
    ms = MachineState()
    assert downloads.handle_downloads_state(CliFlags(), UiState(), ms, {"download_dir": str(tmp_path)},
                                           SimpleNamespace(download_dir=None), "sub", None) == "DETAILS"
    assert db["shows"]["Example"]["episodes"] == ["31"]
    assert ms._download_files["31"] == str(path)
    assert ms._download_files["ep-id"] == str(path)
    assert "99" not in ms._download_files
    assert path.exists()


def test_real_renamed_tagged_video_uses_embedded_episode(tmp_path):
    from media_cli.media.download import anyplay_metadata_args

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe required")
    path = tmp_path / "original.mkv"
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=size=16x16:rate=1",
                    "-frames:v", "1", "-c:v", "ffv1",
                    *anyplay_metadata_args("Example", "31 [463]", "title-id", "episode-id"), str(path)],
                   check=True, capture_output=True, timeout=10)
    renamed = path.rename(tmp_path / "Episode 99.mkv")
    assert downloads.extract_offline_episode_info(str(renamed)) == (31.0, 463.0, "31", "31 [463]")
    assert not episode_identity.download_identity_conflicts("31", str(renamed))


def test_ambiguous_saved_associations_fall_back_to_filename(identity_reader, tmp_path):
    path = tmp_path / "Episode 31.mkv"
    data = {"metadata": {"_episode_labels": {"opaque-a": "Movie", "opaque-b": "Special"}},
            "episode_files": {"opaque-a": [str(path)], "opaque-b": [str(path)]}}
    assert downloads.extract_offline_episode_info(str(path), show_data=data)[2] == "31"
