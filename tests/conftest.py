import os
import tempfile
import pytest

from media_cli.state import paths
from media_cli.core import storage


@pytest.fixture
def installed_android_launcher(monkeypatch):
    """Android playback simulations already fake dispatch; also fake its tool."""
    import shutil

    original = shutil.which
    monkeypatch.setattr(shutil, "which", lambda program: "/test/am" if program == "am" else original(program))


@pytest.fixture(autouse=True)
def isolate_test_state_and_config(monkeypatch):
    """Isolate all tests from the user's real ~/.config and ~/.local/state filesystem."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_cfg_dir = os.path.join(tmpdir, "config")
        test_state_dir = os.path.join(tmpdir, "state")
        os.makedirs(test_cfg_dir, exist_ok=True)
        os.makedirs(test_state_dir, exist_ok=True)

        monkeypatch.setattr(paths, "CONFIG_DIR", test_cfg_dir)
        monkeypatch.setattr(paths, "CONFIG_PATH", os.path.join(test_cfg_dir, "config.conf"))
        monkeypatch.setattr(paths, "LEGACY_CONFIG_PATH", os.path.join(test_cfg_dir, "config.json"))
        monkeypatch.setattr(paths, "STATE_DIR", test_state_dir)
        monkeypatch.setattr(paths, "HISTORY_PATH", os.path.join(test_state_dir, "history.json"))
        monkeypatch.setattr(paths, "SEARCH_HISTORY_PATH", os.path.join(test_state_dir, "search_history.json"))
        monkeypatch.setattr(paths, "PLAYBACK_PATH", os.path.join(test_state_dir, "playback.json"))
        monkeypatch.setattr(paths, "ANILIST_QUEUE_PATH", os.path.join(test_state_dir, "anilist_queue.json"))
        monkeypatch.setattr(paths, "DOWNLOADS_DB_PATH", os.path.join(test_state_dir, "downloads_db.json"))
        monkeypatch.setattr(paths, "LOG_DIR", os.path.join(test_state_dir, "logs"))

        storage.reset_caches()
        yield tmpdir
        storage.reset_caches()
