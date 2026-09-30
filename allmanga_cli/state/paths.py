"""Filesystem locations used by persistent application state."""

import os

STATE_DIR = os.path.expanduser("~/.local/state/allmanga-cli")
CONFIG_DIR = os.path.expanduser("~/.config/allmanga-cli")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.conf")
LEGACY_CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
HISTORY_PATH = os.path.join(STATE_DIR, "history.json")
SEARCH_HISTORY_PATH = os.path.join(STATE_DIR, "search_history.json")
PLAYBACK_PATH = os.path.join(STATE_DIR, "playback.json")
ANILIST_QUEUE_PATH = os.path.join(STATE_DIR, "anilist_queue.json")
LOG_DIR = os.path.join(STATE_DIR, "logs")
HISTORY_MAX = 50
DOWNLOADS_DB_PATH = os.path.join(STATE_DIR, "downloads_db.json")
