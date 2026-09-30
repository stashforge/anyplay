"""
State-machine handler subpackage for allmanga-cli.

Each module in this package contains handler functions for a logical group
of application states.  Every handler receives the three shared state objects
(``CliFlags``, ``UiState``, ``MachineState``), the current configuration
dict, the parsed CLI args, and the translation type, and returns the name of
the next state as a string.

Modules
-------
search    — handle_search_state, handle_history_state
anilist   — handle_anilist_menu_state, handle_anilist_browse_state,
            handle_anilist_search_state
details   — handle_details_state, handle_update_progress_state,
            handle_update_status_state, handle_update_score_state
playback  — handle_episode_state, handle_play_state,
            handle_mirrors_state, handle_action_menu_state,
            handle_browser_play_state, handle_episode_state,
            handle_play_state, handle_mirrors_state, handle_action_menu_state,
            handle_browser_play_state
"""

from .anilist import (
    handle_anilist_airing_state,
    handle_anilist_browse_state,
    handle_anilist_menu_state,
    handle_anilist_search_state,
)
from .details import (
    handle_details_state,
)
from .details_modals import (
    handle_update_progress_state,
    handle_update_score_state,
    handle_update_status_state,
)
from .playback import (
    handle_action_menu_state,
    handle_episode_state,
    handle_mirrors_state,
    handle_play_state,
    handle_browser_play_state,
)
from .search import handle_history_state, handle_search_state

__all__ = [
    "handle_search_state",
    "handle_history_state",
    "handle_anilist_menu_state",
    "handle_anilist_airing_state",
    "handle_anilist_browse_state",
    "handle_anilist_search_state",
    "handle_details_state",
    "handle_update_progress_state",
    "handle_update_status_state",
    "handle_update_score_state",
    "handle_episode_state",
    "handle_play_state",
    "handle_mirrors_state",
    "handle_action_menu_state",
    "handle_browser_play_state",
]
