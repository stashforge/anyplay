"""Filesystem locations used by persistent application state."""

import os
import shutil

from ..constants import APP_NAME, LEGACY_APP_NAMES

STATE_DIR = os.path.expanduser(f"~/.local/state/{APP_NAME}")
CONFIG_DIR = os.path.expanduser(f"~/.config/{APP_NAME}")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.conf")
LEGACY_CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
HISTORY_PATH = os.path.join(STATE_DIR, "history.json")
SEARCH_HISTORY_PATH = os.path.join(STATE_DIR, "search_history.json")
PLAYBACK_PATH = os.path.join(STATE_DIR, "playback.json")
ANILIST_QUEUE_PATH = os.path.join(STATE_DIR, "anilist_queue.json")
LOG_DIR = os.path.join(STATE_DIR, "logs")
HISTORY_MAX = 50
DOWNLOADS_DB_PATH = os.path.join(STATE_DIR, "downloads_db.json")


def ensure_migrated_directories() -> None:
    """Migrate legacy configuration and state directories if target directories do not exist."""
    for legacy in LEGACY_APP_NAMES:
        legacy_cfg = os.path.expanduser(f"~/.config/{legacy}")
        if os.path.isdir(legacy_cfg) and not os.path.exists(CONFIG_DIR):
            try:
                os.makedirs(os.path.dirname(CONFIG_DIR), exist_ok=True)
                shutil.copytree(legacy_cfg, CONFIG_DIR)
            except Exception:
                pass

        legacy_state = os.path.expanduser(f"~/.local/state/{legacy}")
        if os.path.isdir(legacy_state) and not os.path.exists(STATE_DIR):
            try:
                os.makedirs(os.path.dirname(STATE_DIR), exist_ok=True)
                shutil.copytree(legacy_state, STATE_DIR)
            except Exception:
                pass


ensure_migrated_directories()
