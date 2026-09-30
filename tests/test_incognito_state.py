import os
import tempfile
from unittest.mock import MagicMock

import pytest

from media_cli.context import FLAGS
from media_cli.core import storage
from media_cli.state import paths
from media_cli.domain.metadata import prepare_show_display_state


@pytest.fixture(autouse=True)
def setup_incognito_env(monkeypatch):
    """Ensure every test runs with incognito mode enabled and clean isolated state."""
    monkeypatch.setattr(FLAGS, "incognito_mode", True)
    storage.reset_caches()
    with tempfile.TemporaryDirectory() as tmpdir:
        fake_hist = os.path.join(tmpdir, "history.json")
        fake_prefs = os.path.join(tmpdir, "playback.json")
        monkeypatch.setattr(paths, "HISTORY_PATH", fake_hist)
        monkeypatch.setattr(paths, "PLAYBACK_PATH", fake_prefs)
        monkeypatch.setattr(paths, "STATE_DIR", tmpdir)
        yield tmpdir
        storage.reset_caches()


def test_incognito_initial_state_empty_and_no_disk_files():
    """Incognito session starts with empty in-memory history and no files on disk."""
    assert storage.is_incognito() is True
    assert storage.load_history() == []
    assert storage.load_prefs() == {}
    assert os.path.exists(paths.HISTORY_PATH) is False
    assert os.path.exists(paths.PLAYBACK_PATH) is False


def test_incognito_save_history_in_memory_with_zero_disk_write():
    """Watching an episode in incognito updates memory state and never writes to disk."""
    show = {
        "_id": "incog-show-1",
        "name": "Incognito Anime",
        "status": "RELEASING",
        "_episode_ids": ["ep-1", "ep-2", "ep-3"],
        "_episode_labels": {"ep-1": "1", "ep-2": "2", "ep-3": "3"},
        "availableEpisodes": {"sub": 3},
    }

    # Before watch
    assert storage.get_local_progress(show, "sub") is None
    assert storage.get_local_episode_label(show, "sub") is None

    # Watch episode 1
    storage.save_history(show, "ep-1", "sub")

    # In-memory progress is recorded!
    assert storage.get_local_progress(show, "sub") == "1"
    assert storage.get_local_episode_label(show, "sub") == "1"
    assert len(storage.load_history()) == 1
    assert storage.load_history()[0]["episode"] == "ep-1"

    # Disk files were NEVER created!
    assert os.path.exists(paths.HISTORY_PATH) is False


def test_incognito_prepare_show_display_state_preserves_session_progress():
    """prepare_show_display_state populates in-memory progress and keeps sync disabled."""
    show = {
        "_id": "incog-show-2",
        "name": "Incognito Anime 2",
        "status": "RELEASING",
        "_episode_ids": ["ep-1", "ep-2"],
        "_episode_labels": {"ep-1": "1", "ep-2": "2"},
        "availableEpisodes": {"sub": 2},
    }

    # Initial state
    prepare_show_display_state(show, "sub")
    assert show.get("_local_progress") is None
    assert show.get("_sync_enabled") is False

    # Watch episode 1 in this incognito session
    storage.save_history(show, "ep-1", "sub")

    # Refresh display state (e.g. returning from playback to Details screen)
    prepare_show_display_state(show, "sub")
    assert show.get("_local_progress") == "1"
    assert show.get("_local_episode_label") == "1"
    assert show.get("_sync_enabled") is False
    assert show.get("_progress_authority") == "LOCAL"


def test_incognito_resume_time_in_memory_only():
    """save_resume_time and get_resume_time work in memory without writing to playback.json."""
    assert storage.get_resume_time("show-x", 1) == 0

    storage.save_resume_time("show-x", 1, 450)
    assert storage.get_resume_time("show-x", 1) == 450

    # Disk file was never touched!
    assert os.path.exists(paths.PLAYBACK_PATH) is False


def test_incognito_menu_advances_from_play_to_play_next():
    """In details/action menu, progress allows advancing to Play Next (EP 2)."""
    from media_cli.app import playback_menu
    from media_cli.context import UiState, MachineState, CliFlags

    flags = CliFlags(incognito_mode=True)
    ui = UiState()
    ms = MachineState()
    ms.show_id = "show-adv-1"
    ms.show_title = "Advance Anime"

    action_show = {
        "_id": "show-adv-1",
        "name": "Advance Anime",
        "status": "RELEASING",
        "episodeCount": 12,
        "_episode_ids": ["ep-1", "ep-2", "ep-3"],
        "_episode_labels": {"ep-1": "1", "ep-2": "2", "ep-3": "3"},
        "availableEpisodes": {"sub": 3},
    }
    ui.ui_show_ctx = action_show

    # Watch episode 1 in incognito session
    storage.save_history(action_show, "ep-1", "sub")
    prepare_show_display_state(action_show, "sub")

    # Verify action_show local progress is 1
    assert action_show.get("_local_progress") == "1"
    assert storage.get_local_progress(action_show, "sub") == "1"


def test_incognito_ignores_existing_history_by_default():
    """Default incognito mode starts fresh and ignores existing history on disk."""
    import json

    storage.reset_caches()
    original_disk_data = [
        {
            "show": {"_id": "disk-show-1", "name": "Existing Show"},
            "episode": "5",
            "translation_type": "sub",
            "timestamp": 123456789,
        }
    ]
    with open(paths.HISTORY_PATH, "w") as f:
        json.dump(original_disk_data, f)

    # 1. By default, incognito starts with empty history despite disk contents
    hist = storage.load_history()
    assert hist == []
    show = dict(original_disk_data[0]["show"])
    assert storage.get_local_progress(show, "sub") is None

    # 2. Watch episode 1 in this session
    storage.save_history(show, "1", "sub")
    assert str(storage.get_local_progress(show, "sub")) == "1"

    # 3. Disk file remains completely untouched!
    with open(paths.HISTORY_PATH) as f:
        disk_content = json.load(f)
    assert disk_content == [
        {
            "show": {"_id": "disk-show-1", "name": "Existing Show"},
            "episode": "5",
            "translation_type": "sub",
            "timestamp": 123456789,
        }
    ]


def test_incognito_with_history_allowed_loads_snapshot_read_only():
    """When opened with history allowed (e.g. anyplay history --incognito), incognito reads snapshot from disk."""
    import json

    storage.reset_caches()
    storage.set_incognito_history_allowed(True)
    original_disk_data = [
        {
            "show": {"_id": "disk-show-1", "name": "Existing Show"},
            "episode": "5",
            "translation_type": "sub",
            "timestamp": 123456789,
        }
    ]
    with open(paths.HISTORY_PATH, "w") as f:
        json.dump(original_disk_data, f)

    # 1. Reads existing history snapshot from disk
    hist = storage.load_history()
    assert len(hist) == 1
    assert hist[0]["show"]["name"] == "Existing Show"
    assert hist[0]["episode"] == "5"

    # 2. Watch episode 6 in incognito
    show = hist[0]["show"]
    storage.save_history(show, "6", "sub")

    # In-memory history is updated to episode 6
    assert str(storage.get_local_progress(show, "sub")) == "6"

    # 3. Disk file is 100% UNTOUCHED byte-for-byte!
    with open(paths.HISTORY_PATH) as f:
        disk_content = json.load(f)
    assert disk_content[0]["episode"] == "5"
    assert disk_content == original_disk_data

