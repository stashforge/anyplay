"""
Shared state dataclasses for allmanga-cli.

Replaces the scattered ``globals()`` dict pattern in ``app.py`` with three
focused, typed objects, each with a clear responsibility boundary:

* :class:`CliFlags`    — CLI flags read from ``argparse``; treated as read-only
                         after startup.
* :class:`UiState`     — Per-interaction state that changes as the user hovers
                         over items, types queries, and navigates menus.
* :class:`MachineState`— Mutable locals shared across every state transition in
                         the ``main()`` loop (current episode, selected stream, …).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# CliFlags
# ---------------------------------------------------------------------------


@dataclass
class CliFlags:
    """Startup flags derived from CLI args.

    Set once at program entry and intended to be read-only after that.
    All downstream code should read these instead of calling
    ``globals().get("DEBUG_MODE")`` etc.
    """

    debug_mode: bool = False
    """Enable verbose crash logging (``--debug``)."""

    incognito_mode: bool = False
    """Suppress all disk writes and AniList updates (``--incognito``)."""

    sync_force_on: bool = False
    """``--sync`` flag: force AniList tracking on for this session."""

    sync_force_off: bool = False
    """``--no-sync`` flag: force AniList tracking off for this session."""

    show_image: bool = False
    """``--cover`` flag: render cover images inside the picker."""

    plain_mode: bool = False
    """``--plain`` / ``--no-tui`` flag: disable full-screen TUI and use line-by-line numbered prompts."""

    spinner_style: object = "braille"
    """Resolved configured spinner style name or custom frame list."""


# Module-level singleton: the one shared CliFlags instance for this process.
#
# ``main()`` fills these fields in from argparse right after parsing.  Bottom-
# layer helpers (``is_incognito``, ``debug_warn``, crash handling) read from
# here instead of reaching into ``app_core``'s ``globals()``.  Seeded from
# ``sys.argv`` so calls that happen before argparse still see the right flags.
FLAGS = CliFlags(
    debug_mode="--debug" in sys.argv[1:],
    incognito_mode="--incognito" in sys.argv[1:],
    plain_mode=any(arg in sys.argv[1:] for arg in ("--plain", "--no-tui")),
)


# ---------------------------------------------------------------------------
# UiState
# ---------------------------------------------------------------------------


@dataclass
class UiState:
    """Per-interaction UI state.

    Changes as the user hovers, types, and navigates between screens.
    Passed to picker and rendering helpers so they no longer need to read
    ``globals()``.
    """

    # Hover / poster
    hovered_show_id: str | None = None
    """Provider ID of the show currently highlighted in the picker."""

    hovered_show_obj: dict | None = None
    """Full show dict of the currently highlighted show (for poster ticks)."""

    # Active search query text inside the picker
    active_picker_query: str = ""
    """The live filter query string typed inside the current picker."""

    # Error message shown in the search-input header
    search_error: str = ""
    """Last search error to display in the input-screen header."""

    # Back-navigation stack (one level per screen pair)
    search_prev_state: str = "SEARCH"
    """State to return to when pressing Esc/Left from DETAILS or EPISODE."""

    ep_prev_state: str = "SEARCH"
    """State to return to when pressing Esc/Left from the episode list."""

    action_prev_state: str = "SEARCH"
    """State to return to when pressing Esc/Left from ACTION_MENU."""

    # Current show / translation type context shown in the picker header
    ui_show_ctx: dict = field(default_factory=dict)
    """The show dict currently displayed in info panels."""

    ui_ttype_ctx: str = "sub"
    """Translation type (``"sub"`` or ``"dub"``) for the current show."""

    ui_provider_ctx: str = "miruro"
    """Active streaming provider ID."""

    # Persistent filter / sort prefs (survive state transitions)
    history_filter: str = "Active"
    """Current history filter mode: ``"Active"``, ``"Completed"``, or ``"All"``."""

    anilist_browse_status: str | None = None
    """AniList list status being browsed (e.g. ``"CURRENT"``, ``"PLANNING"``)."""

    anilist_airing_tab: str = "today"
    """Current AniList airing tab: ``"today"``, ``"tomorrow"``, or ``"week"``."""

    # Mirror picker toast
    pref_toast: str = ""
    """Temporary toast message shown in the Mirrors picker."""

    pref_toast_time: float = 0.0
    """``time.time()`` when *pref_toast* was set (for 3-second expiry)."""

    # --sources flag: only prompt once per playback session
    initial_sources_prompted: bool = False
    """Whether the ``--sources`` mirror picker has been shown for this episode."""

    aniskip_android_hint_shown: bool = False
    """Whether the Android aniskip instructions hint has been shown."""


# ---------------------------------------------------------------------------
# MachineState
# ---------------------------------------------------------------------------


@dataclass
class MachineState:
    """Mutable locals shared across every state transition.

    These were previously bare locals inside ``main()`` that mutated in-place
    across the ``while state != "QUIT"`` loop.  Collecting them here makes the
    data flow explicit and lets handler functions receive a single object
    instead of a sprawling argument list.
    """

    # Search
    query_str: str = ""
    """The current search query string."""

    shows: list = field(default_factory=list)
    """Search results from the last AllAnime or AniList query."""

    # Selected title
    show_id: str | None = None
    """Provider ID of the selected show."""

    show_title: str = ""
    """Display title of the selected show."""

    total_eps: int = 0
    """Total episode count for the selected show."""

    # Episode cursor
    current_ep: object = 1
    """Current episode ID (string or int depending on provider)."""

    current_ep_index: int = 0
    """Zero-based index of *current_ep* inside the episode catalog."""

    # Stream
    selected_stream: dict | None = None
    """The stream dict chosen for playback (from ``resolver.resolve_source``)."""

    # Playback UX
    pending_osd_msg: str = ""
    """OSD message to display at the start of the next episode."""
    pending_resume_time: float | None = None
    """Override resume time (in seconds) for mirror switching or resume override."""

    # AniList search parent (for back-navigation)
    anilist_search_parent: str = "QUIT"
    """State to return to when Esc-ing from ANILIST_SEARCH."""

    # Search UX helpers
    just_searched: bool = False
    """True when the user just submitted a search (enables auto-select-single)."""

    just_picked_anime: bool = False
    """True immediately after a title was selected from search results."""

    # Episode data cache (avoid re-fetching for the same episode)
    ep_cache_key: object = None
    """Cache key ``(show_id, episode, ttype)`` for the last fetched episode."""

    ep_cache_data: object = None
    """Cached episode data dict from the last ``get_episode_data()`` call."""

    # Batch download
    download_batch_end: int | None = None
    """Last episode number in a batch download range (``-e 1-5``)."""

    # Search result caches (avoid re-fetching when navigating back)
    last_aa_query_str: str = ""
    """Query string of the last cached AllAnime search."""

    last_aa_shows: list = field(default_factory=list)
    """Cached results of the last AllAnime search."""

    last_al_query_str: str = ""
    """Query string of the last cached AniList search."""

    last_al_shows: list = field(default_factory=list)
    """Cached results of the last AniList search."""

    # Smart Downloads options
    auto_download_next: bool = False
    """Auto-download next episode in background/binge mode if missing locally."""
    auto_delete_watched: bool = False
    """Auto-delete watched episodes once beyond the rolling safety buffer."""
    auto_delete_buffer: int = 1
    """Number of watched episodes to retain before deleting older ones."""

    _is_downloads: bool = False
    """Whether the current session is in Downloads mode."""
    _download_files: dict = field(default_factory=dict)
    """Map of episode identifiers to local downloaded file paths."""
    _binge_auto_downloading: bool = False
    """Flag indicating whether background binge auto-download is currently active."""
    _failed_mirrors: set = field(default_factory=set)
    """Set of stream indices/mirrors that failed during playback."""

    _android_pending_show_id: str | None = None
    _android_pending_watched_ep: str | None = None
    _android_pending_watched_idx: int | None = None


def set_ui_context(ui: Any, show: dict | None, ttype: str) -> None:
    ui.ui_show_ctx = show
    ui.ui_ttype_ctx = ttype


def get_ui_show(ui: Any, default: dict | None = None) -> dict:
    if default is None:
        default = {}
    return ui.ui_show_ctx if getattr(ui, "ui_show_ctx", None) is not None else default


def get_ui_ttype(ui: Any, default: str = "sub") -> str:
    return (
        getattr(ui, "ui_ttype_ctx", None)
        if getattr(ui, "ui_ttype_ctx", None) is not None
        else default
    )


def set_navigation_context(
    ui: Any,
    search_prev: str | None = None,
    ep_prev: str | None = None,
    action_prev: str | None = None,
) -> None:
    if search_prev is not None:
        ui.search_prev_state = search_prev
    if ep_prev is not None:
        ui.ep_prev_state = ep_prev
    if action_prev is not None:
        ui.action_prev_state = action_prev


def get_search_prev(ui: Any, default: str = "SEARCH") -> str:
    return ui.search_prev_state if getattr(ui, "search_prev_state", None) else default


def get_ep_prev(ui: Any, default: str = "SEARCH") -> str:
    return ui.ep_prev_state if getattr(ui, "ep_prev_state", None) else default


def get_action_prev(ui: Any, default: str = "SEARCH") -> str:
    return ui.action_prev_state if getattr(ui, "action_prev_state", None) else default
