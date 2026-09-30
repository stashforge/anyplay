"""Playback state handlers for allmanga-cli."""

from __future__ import annotations

from .playback_episodes import (
    _clear_episode_source_state,
    _display_episode_label,
    _episode_labels_for,
    _fmt_ep,
    format_mirror_label,
    handle_episode_state,
)
from .playback_menu import (
    handle_action_menu_state,
    handle_mirrors_state,
    handle_browser_play_state,
)
from .playback_player import (
    handle_play_state,
)

__all__ = [
    "_episode_labels_for",
    "_display_episode_label",
    "_fmt_ep",
    "_clear_episode_source_state",
    "format_mirror_label",
    "handle_episode_state",
    "handle_play_state",
    "handle_action_menu_state",
    "handle_mirrors_state",
    "handle_browser_play_state",
]
