"""Explicit episode identity wins over season/title numbers and stale aliases."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from media_cli.domain.episodes import clean_episode_identifier, parse_episode_dual_numbers


@pytest.mark.parametrize("raw", [
    "Tales of Demons and Gods Season 10 - Episode 31 [463].mkv",
    "/videos/Season 10/Series 86 - Episode 31 [463].mkv",
    "Series 86 - EP 31 [463].mp4",
    "https://example.test/season-10-episode-31/",
])
def test_explicit_episode_wins_over_title_and_season_numbers(raw):
    assert clean_episode_identifier(raw) == "31"
    assert parse_episode_dual_numbers(raw)[0] == "31"


def test_runtime_download_alias_cannot_point_to_a_different_episode(tmp_path):
    from media_cli.app.playback_episodes import _prune_missing_download_files

    path = tmp_path / "Show Season 10 - Episode 31 [463].mkv"
    path.write_bytes(b"video")
    state = SimpleNamespace(_download_files={"10": str(path), "31": str(path), "463": str(path),
                                            path.name: str(path), "opaque-id": str(path)})
    result = _prune_missing_download_files(state)
    assert "10" not in result
    assert set(result) == {"31", "463", path.name, "opaque-id"}
    assert path.exists()


def test_recorded_wrong_episode_alias_cannot_delete_the_real_episode(monkeypatch, tmp_path):
    from media_cli.core import storage

    path = tmp_path / "Show Season 10 - Episode 31 [463].mkv"
    path.write_bytes(b"video")
    db = {"shows": {"Show": {"episodes": ["31"], "episode_files": {"10": [str(path)]}}}}
    monkeypatch.setattr(storage, "load_downloads_db", lambda: db)
    monkeypatch.setattr(storage, "save_downloads_db", Mock())
    assert not storage.delete_offline_episode("Show", "10", {}, require_recorded=True)
    assert path.exists()


def test_registration_rejects_contradictory_file_identity(monkeypatch, tmp_path):
    from media_cli.core import storage

    path = tmp_path / "Show Season 10 - Episode 31 [463].mkv"
    path.write_bytes(b"video")
    write = Mock()
    monkeypatch.setattr(storage, "load_downloads_db", lambda: {"shows": {}})
    monkeypatch.setattr(storage, "save_downloads_db", write)
    assert not storage.register_downloaded_episode_files("Show", "10", [str(path)])
    write.assert_not_called()


@pytest.mark.parametrize("identifier,conflicts", [
    ("10", True), ("31", False), ("463", False), ("31 [463]", False), ("opaque-id", False),
])
def test_identity_guard_retains_valid_aliases_and_opaque_ids(identifier, conflicts):
    from media_cli.domain.episodes import episode_file_identity_conflicts

    assert episode_file_identity_conflicts(identifier, "Series 10 - Episode 31 [463].mkv") is conflicts


def test_local_playback_rejects_old_wrong_episode_alias(monkeypatch, tmp_path):
    from media_cli.app import playback_player

    path = tmp_path / "Show Season 10 - Episode 31 [463].mkv"
    path.write_bytes(b"\0" * 2048)
    ms = SimpleNamespace(_download_files={"10": str(path)})
    monkeypatch.setattr(playback_player.app_core, "find_offline_file_for_episode", lambda *a: None)
    assert playback_player._local_stream_for_episode({}, "Show", "10", "sub", {}, ms) is None


def test_registration_resolves_provider_id_using_supplied_label(monkeypatch, tmp_path):
    from media_cli.core import storage

    path = tmp_path / "Show - Episode 31 [463].mkv"
    path.write_bytes(b"video")
    monkeypatch.setattr(storage, "load_downloads_db", lambda: {"shows": {}})
    monkeypatch.setattr(storage, "save_downloads_db", Mock())
    assert storage.register_downloaded_episode_files(
        "Show", "9988", [str(path)], metadata={"_episode_labels": {"9988": "31 [463]"}},
    )
