"""Cleanup uses local completion, not only a transient explicit watched list."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from media_cli.app import playback_player


@pytest.fixture
def cleanup(monkeypatch):
    ids = [f"https://example.test/season-10-episode-{ep}/" for ep in (31, 32)]
    show = dict(name="Example", _folder_name="Example", status="AIRING",
                _episode_ids=ids, _episode_labels=dict(zip(ids, ("31 [463]", "32 [464]"), strict=True)),
                watched_episodes=[], _local_progress=None)
    ms = SimpleNamespace(show_title="Example", auto_delete_watched=True, auto_delete_buffer=1,
                         _download_files={eid: f"/fake/episode-{ep}.mkv" for eid, ep in zip(ids, (31, 32), strict=True)})
    progress = Mock(return_value=None)
    delete = Mock(return_value=True)
    monkeypatch.setattr(playback_player.app_core, "get_local_progress", progress)
    monkeypatch.setattr(playback_player.app_core, "find_offline_file_for_episode", lambda *a, **kw: None)
    monkeypatch.setattr(playback_player.app_core.storage, "load_downloads_db", lambda: {"shows": {}})
    monkeypatch.setattr(playback_player.app_core, "delete_offline_episode", delete)
    monkeypatch.setattr(playback_player.app_core, "set_action_feedback", Mock())
    return show, ms, progress, delete


def test_long_catalogue_cleanup_reuses_one_registry_snapshot(cleanup, monkeypatch):
    show, ms, progress, delete = cleanup
    ids = [str(ep) for ep in range(1, 165)]
    progress.return_value = 164
    ms._download_files = {}
    snapshot = {"shows": {}}
    load = Mock(return_value=snapshot)
    def lookup(*args, **kwargs):
        assert kwargs == {"registered_only": True, "downloads_db": snapshot}
        return None
    monkeypatch.setattr(playback_player.app_core.storage, "load_downloads_db", load)
    monkeypatch.setattr(playback_player.app_core, "find_offline_file_for_episode", lookup)
    assert playback_player._auto_delete_watched_downloads(show, ids, "sub", {}, ms) == []
    load.assert_called_once_with()
    delete.assert_not_called()


@pytest.mark.parametrize("explicit", [[], ["32"], ["32 [464]"]])
@pytest.mark.parametrize("from_history", [False, True])
def test_local_watched_through_keeps_only_the_buffer(cleanup, explicit, from_history):
    show, ms, progress, delete = cleanup
    show["watched_episodes"] = explicit
    if from_history:
        progress.return_value = 32
    else:
        show["_local_progress"] = 32
    assert playback_player._auto_delete_watched_downloads(show, show["_episode_ids"], "sub", {}, ms) == ["31 [463]"]
    delete.assert_called_once_with("Example", "31 [463]", {}, require_recorded=True)
    progress.assert_called_once_with(show, "sub")


def test_remote_progress_is_not_permission_to_delete(cleanup):
    show, ms, _, delete = cleanup
    show["_anilist_progress"] = 32
    assert playback_player._auto_delete_watched_downloads(show, show["_episode_ids"], "sub", {}, ms) == []
    delete.assert_not_called()


def test_session_off_overrides_saved_on(cleanup):
    show, ms, _, delete = cleanup
    show["_local_progress"] = 32
    show["watched_episodes"] = list(show["_episode_ids"])
    ms.auto_delete_watched = False
    cfg = {"auto_delete_watched": True}
    assert playback_player._auto_delete_watched_downloads(show, show["_episode_ids"], "sub", cfg, ms) == []
    delete.assert_not_called()


def test_newer_local_history_overrides_stale_cached_progress(cleanup):
    show, ms, progress, delete = cleanup
    show["_local_progress"] = 32
    progress.return_value = 0
    assert playback_player._auto_delete_watched_downloads(show, show["_episode_ids"], "sub", {}, ms) == []
    delete.assert_not_called()


def test_finished_title_releases_final_buffer_only_after_local_completion(cleanup):
    show, ms, progress, delete = cleanup
    show["status"] = "FINISHED"
    progress.return_value = 32
    assert playback_player._auto_delete_watched_downloads(show, show["_episode_ids"], "sub", {}, ms) == ["31 [463]", "32 [464]"]
    assert delete.call_count == 2


def test_explicit_dual_labels_resolve_to_provider_ids(cleanup):
    show, ms, _, delete = cleanup
    show["watched_episodes"] = ["31", "32 [464]"]
    assert playback_player._auto_delete_watched_downloads(show, show["_episode_ids"], "sub", {}, ms) == ["31 [463]"]
    assert delete.call_count == 1


@pytest.mark.parametrize("value", ["bad", "NaN", "Infinity", -1])
def test_invalid_progress_cannot_authorize_deletion(cleanup, value):
    show, ms, progress, delete = cleanup
    progress.return_value = value
    assert playback_player._auto_delete_watched_downloads(show, show["_episode_ids"], "sub", {}, ms) == []
    delete.assert_not_called()


@pytest.mark.parametrize("progress_value,buffer,expected", [(31, 1, []), (31, 0, ["31 [463]"]), (32, 2, [])])
def test_buffer_keeps_watched_files_and_never_deletes_unwatched(cleanup, progress_value, buffer, expected):
    show, ms, progress, _ = cleanup
    progress.return_value = progress_value
    ms.auto_delete_buffer = buffer
    assert playback_player._auto_delete_watched_downloads(show, show["_episode_ids"], "sub", {}, ms) == expected


def test_actual_recorded_cleanup_preserves_buffer_and_unregistered_files(cleanup, monkeypatch, tmp_path):
    from media_cli.core import storage

    show, ms, progress, _ = cleanup
    progress.return_value = 32
    paths = []
    for number, absolute in ((31, 463), (32, 464)):
        path = tmp_path / f"Example Season 10 - Episode {number} [{absolute}].mkv"
        path.write_bytes(b"video")
        paths.append(path)
    user_copy = tmp_path / "Example Season 10 - Episode 31 [463] copy.mkv"
    user_copy.write_bytes(b"user file")
    ms._download_files = dict(zip(show["_episode_ids"], map(str, paths), strict=True))
    db = {"shows": {"Example": {"episodes": ["31", "32"], "metadata": {},
                               "episode_files": {eid: [str(path)] for eid, path in zip(show["_episode_ids"], paths, strict=True)}}}}
    monkeypatch.setattr(storage, "load_downloads_db", lambda: db)
    monkeypatch.setattr(storage, "save_downloads_db", Mock())
    monkeypatch.setattr(playback_player.app_core, "delete_offline_episode", storage.delete_offline_episode)
    assert playback_player._auto_delete_watched_downloads(show, show["_episode_ids"], "sub", {}, ms) == ["31 [463]"]
    assert not paths[0].exists()
    assert paths[1].exists() and user_copy.exists()
    assert db["shows"]["Example"]["episodes"] == ["32"]
