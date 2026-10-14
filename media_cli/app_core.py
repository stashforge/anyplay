#!/usr/bin/env python3
"""Anyplay terminal app orchestration."""

import atexit

from media_cli.core import anilist, processes, reporting, storage, streams
from media_cli.core.api import ProviderDependencyError
from media_cli.core.processes import check_deps, is_termux, kill_active_subprocesses
from media_cli.core.storage import find_offline_file_for_episode
from media_cli.services.catalog import load_episode_ids_for_selection
from media_cli.domain.history import playback_ep_from_history_entry
from media_cli.context import (
    set_ui_context,
    get_ui_show,
    get_ui_ttype,
    set_navigation_context,
    get_search_prev,
    get_ep_prev,
    get_action_prev,
)
from media_cli.domain.metadata import (
    prepare_show_display_state,
    batch_prepare_shows_display_state,
    format_video_time,
)
from media_cli.core.enrichment import enrich_show_if_missing
from media_cli.domain.titles import get_show_display_title
from media_cli.ui.info_panel import (
    make_info_fn,
    make_single_show_info_fn,
    make_shows_info_fn,
    build_info_panel,
)
from media_cli.context import (
    set_ui_context,
    get_ui_show,
    get_ui_ttype,
    set_navigation_context,
    get_search_prev,
    get_ep_prev,
    get_action_prev,
)
from media_cli.media import local_proxy
from media_cli.media import resolver as stream_resolver
from media_cli.playback import android as android_playback
from media_cli.playback import engine as playback_engine
from media_cli.playback.engine import _ipc_player, play_desktop, _exit_player_screen
from media_cli.ui import display
from media_cli.providers import (
    _DEFAULT_PROVIDER_ID,
    PROVIDER_REGISTRY,
    PROVIDERS,
    ALLANIME,
    available_providers,
    get_provider_registry,
    is_provider_active,
    provider_key,
    get_provider,
    provider_display_name,
    provider_translation_capability,
)
from media_cli.ui.panels import render_search_header
from media_cli.ui.info_panel import (
    make_info_fn,
    make_single_show_info_fn,
    make_shows_info_fn,
    build_info_panel,
)
from media_cli.context import (
    set_ui_context,
    get_ui_show,
    get_ui_ttype,
    set_navigation_context,
    get_search_prev,
    get_ep_prev,
    get_action_prev,
)
from media_cli.core.anilist import load_anilist_browse
from media_cli.core.storage import find_offline_file_for_episode
from media_cli.domain.history import playback_ep_from_history_entry
from media_cli.domain.metadata import (
    prepare_show_display_state,
    batch_prepare_shows_display_state,
    format_video_time,
)
from media_cli.ui.info_panel import (
    make_info_fn,
    make_single_show_info_fn,
    make_shows_info_fn,
    build_info_panel,
)
from media_cli.context import (
    set_ui_context,
    get_ui_show,
    get_ui_ttype,
    set_navigation_context,
    get_search_prev,
    get_ep_prev,
    get_action_prev,
)
from media_cli.core.anilist import load_anilist_browse
from media_cli.core.storage import find_offline_file_for_episode
from media_cli.domain.history import playback_ep_from_history_entry
from media_cli.domain.metadata import (
    prepare_show_display_state,
    batch_prepare_shows_display_state,
    format_video_time,
)
from media_cli.ui.info_panel import (
    make_info_fn,
    make_single_show_info_fn,
    make_shows_info_fn,
    build_info_panel,
)
play_desktop = playback_engine.play_desktop

atexit.register(storage.cleanup_incognito_cache)


def app_main():
    from media_cli.app.orchestrator import main

    return main()

# ── ANSI ──────────────────────────────────────────────────────────────────────
GREEN = "\033[1;32m"
YELLOW = "\033[1;33m"
RED = "\033[1;31m"
CYAN = "\033[1;36m"
BOLD = "\033[1m"
RESET = "\033[0m"

from media_cli.ui.player_screen import add_status_line as _add_status

# Reporting lives in core.reporting; the player-screen status buffer is wired
# in as its sink so reporters route there while the player UI is active.
reporting.set_status_sink(_add_status)
info = reporting.info
ok = reporting.ok
warn = reporting.warn
err = reporting.err
debug_warn = reporting.debug_warn

stream_resolver.configure_reporters(info, ok, warn)
resolve_source = stream_resolver.resolve_source
local_proxy.configure_debug_reporter(debug_warn)
start_local_proxy = local_proxy.start_local_proxy
start_local_content_server = local_proxy.start_local_content_server
stop_local_proxy = local_proxy.stop_local_proxy
replace_active_local_proxy = local_proxy.replace_active_local_proxy
cleanup_active_local_proxy = local_proxy.cleanup_active_local_proxy
android_playback.configure_reporters(info, ok, warn, err)
PLAYERS = android_playback.PLAYERS
pkg_installed = android_playback.package_installed
play_android = android_playback.play_android


# ── TUI picker ────────────────────────────────────────────────────────────────
# Colors (Catppuccin-inspired, matching old fzf theme)
_C_NORMAL = "\033[38;5;252m"  # #cccccc  normal item text
_C_SEL = "\033[1;97m"  # bright white bold  selected item
_C_PTR = "\033[38;2;243;139;168m"  # #f38ba8  pink pointer ❯
_C_PROMPT = "\033[38;2;203;166;247m"  # #cba6f7  mauve prompt
_C_MATCH = "\033[38;2;137;180;250m"  # #89b4fa  blue match highlight
_C_QUERY = "\033[38;2;137;220;235m"  # #89dceb  teal query text
_C_HINT = "\033[38;5;244m"  # #888888  dim hints / footer
_C_COUNT = "\033[38;5;244m"  # same dim for count
_C_SCROLLI = "\033[38;2;243;139;168m"  # pink scroll indicators
_C_SECTION = "\033[1;38;5;183m"  # lavender section labels
_RST = "\033[0m"


should_update_anilist_progress = anilist.should_update_anilist_progress
sync_progress_and_checkpoint = anilist.sync_progress_and_checkpoint
sync_watched_to_anilist = anilist.sync_watched_to_anilist
save_and_sync_watched = anilist.save_and_sync_watched

from media_cli.state.preferences import (
    set_action_feedback,
    get_active_feedback,
)

# ── Display (posters / spinner / alt-screen / loading) → ui.display ──────────────
# All terminal-presentation logic lives in ui.display; these aliases keep the
# many app_core.<name> callers (app/, ui/, cli/, tests) working unchanged.
# hovered-show id is app_core/orchestrator state the app layer writes; display
# reads it through the injected hook below.
_hovered_show_id = (
    None  # provider id of the show hovered in the picker (app layer writes it)
)
display.configure(hovered_show_id_fn=lambda: globals().get("_hovered_show_id"))

_request_poster_redraw = display._request_poster_redraw
_configured_loading_frame = display._configured_loading_frame
_configure_spinner_from_config = display._configure_spinner_from_config
_poster_manager = display._poster_manager
_clear_poster_downloads = display._clear_poster_downloads
clear_terminal_images = display.clear_terminal_images
_poster_footer_line = display._poster_footer_line
_poster_needs_tick = display._poster_needs_tick
_get_poster = display._get_poster
enter_alt_screen = display.enter_alt_screen
exit_alt_screen = display.exit_alt_screen
restore_terminal = display.restore_terminal
fatal_terminal_exit = display.fatal_terminal_exit
with_loading = display.with_loading
render_anilist_menu_loading = display.render_anilist_menu_loading
with_anilist_menu_loading = display.with_anilist_menu_loading
with_footer_loading = display.with_footer_loading


# ── Persistence (core.storage) ────────────────────────────────────────────────
# Definitions live in core/storage.py; aliases keep existing callers working.
_atomic_write_json = storage._atomic_write_json
write_private_log = storage.write_private_log
write_exception_log = storage.write_exception_log
load_prefs = storage.load_prefs
save_prefs = storage.save_prefs
get_preferred_mirror = storage.get_preferred_mirror
toggle_preferred_mirror = storage.toggle_preferred_mirror
get_episode_order = storage.get_episode_order
toggle_episode_order = storage.toggle_episode_order
get_title_sync_preference = storage.get_title_sync_preference
get_title_sync = storage.get_title_sync
set_title_sync = storage.set_title_sync
get_custom_skips = storage.get_custom_skips
set_custom_skips = storage.set_custom_skips
clear_custom_skips = storage.clear_custom_skips
get_local_progress = storage.get_local_progress
get_local_episode_label = storage.get_local_episode_label
get_history_entry = storage.get_history_entry
get_last_synced_progress = storage.get_last_synced_progress
episode_id_for_progress = storage.episode_id_for_progress
ANILIST_HISTORY_STRIP_KEYS = storage.ANILIST_HISTORY_STRIP_KEYS
HISTORY_SHOW_STRIP_KEYS = storage.HISTORY_SHOW_STRIP_KEYS
sanitize_show_for_history = storage.sanitize_show_for_history
sanitize_history_list = storage.sanitize_history_list
write_history_progress = storage.write_history_progress
set_last_synced_progress = storage.set_last_synced_progress


get_resume_time = storage.get_resume_time
load_resume_time = storage.get_resume_time
save_resume_time = storage.save_resume_time

get_pending_completion = storage.get_pending_completion
save_pending_completion = storage.save_pending_completion
clear_pending_completion = storage.clear_pending_completion
get_al_match = storage.get_al_match
save_al_match = storage.save_al_match
get_source_anilist_match = storage.get_source_anilist_match
save_source_anilist_match = storage.save_source_anilist_match

from media_cli.domain.history import (
    refresh_history_entry_provider_catalog,
)

_preserve_invalid_state_file = storage._preserve_invalid_state_file
load_history = storage.load_history
save_history = storage.save_history
touch_history = storage.touch_history
delete_history_entry = storage.delete_history_entry

fetch_anilist_media = anilist.fetch_anilist_media
get_show_anilist_id = anilist.get_show_anilist_id
get_show_mal_id = anilist.get_show_mal_id
get_anilist_media_id = anilist.get_anilist_media_id
update_anime_from_anilist_media = anilist.update_anime_from_anilist_media

refresh_history_entry_from_anilist = anilist.refresh_history_entry_from_anilist

from media_cli.domain.metadata import apply_provider_metadata_to_history_show

apply_allanime_metadata_to_history_show = apply_provider_metadata_to_history_show


refresh_history_anilist_airing_batch = anilist.refresh_history_anilist_airing_batch

save_refreshed_history = storage.save_refreshed_history
patch_history_entry_show = storage.patch_history_entry_show
patch_history_entry_state = storage.patch_history_entry_state
record_download_watch_progress = storage.record_download_watch_progress
record_download_unwatch_progress = storage.record_download_unwatch_progress
load_search_history = storage.load_search_history
save_search_history = storage.save_search_history
delete_search_history_entry = storage.delete_search_history_entry
clear_search_history = storage.clear_search_history
format_history_entry = storage.format_history_entry
format_history_updated_time = storage.format_history_updated_time
history_entry_progress = storage.history_entry_progress
history_entry_category = storage.history_entry_category
filter_history_entries = storage.filter_history_entries

load_config = storage.load_config
save_config = storage.save_config
set_incognito_history_allowed = storage.set_incognito_history_allowed
is_incognito_history_allowed = storage.is_incognito_history_allowed

from media_cli.media.urls import (
    redact_sensitive_text,
)

sanitize_token = storage.sanitize_token

save_anilist_token = anilist.save_anilist_token
clear_anilist_token = anilist.clear_anilist_token
anilist_token_storage_status = anilist.anilist_token_storage_status
mask_token = anilist.mask_token
anilist_auth_status_lines = anilist.anilist_auth_status_lines
stored_anilist_token = anilist.stored_anilist_token
anilist_auth_login_existing_lines = anilist.anilist_auth_login_existing_lines
anilist_auth_token_lines = anilist.anilist_auth_token_lines
prompt_anilist_token = anilist.prompt_anilist_token


# Wire storage's injected upper-layer hooks: private-log redaction, provider
# episode catalogs, and display-state priming (defined later in this module).
storage.configure_redactor(redact_sensitive_text)
storage.configure(
    episode_ids_fn=lambda show, ttype: ensure_episode_ids(show, ttype),
    prepare_display_state_fn=lambda show, ttype="sub", sync_enabled=None: (
        prepare_show_display_state(show, ttype, sync_enabled)
    ),
)
anilist.configure(
    action_feedback_fn=lambda show, msg: set_action_feedback(show, msg),
    provider_catalog_refresh_fn=lambda entry: refresh_history_entry_provider_catalog(
        entry
    ),
)
streams.configure(
    episode_data_fn=lambda show_id, ep, ttype="sub", provider_id=None: (
        get_episode_data(show_id, ep, ttype, provider_id=provider_id)
    ),
)

# ── AniList Tracking ──────────────────────────────────────────────────────────
scrobble_anilist = anilist.scrobble_anilist
_fuzzy_date_has_value = anilist._fuzzy_date_has_value
_today_fuzzy_date = anilist._today_fuzzy_date
_anilist_date_updates = anilist._anilist_date_updates
update_anilist_entry = anilist.update_anilist_entry

# Durable AniList write-queue + reconciliation live in core.anilist.
_load_anilist_queue = anilist._load_anilist_queue
_save_anilist_queue = anilist._save_anilist_queue
_anilist_mutation_key = anilist._anilist_mutation_key
_enqueue_anilist_progress = anilist._enqueue_anilist_progress
_update_queued_mutation = anilist._update_queued_mutation
_remove_queued_mutation = anilist._remove_queued_mutation
_checkpoint_queued_progress = anilist._checkpoint_queued_progress
_finish_queued_pending_completion = anilist._finish_queued_pending_completion
_run_queued_anilist_progress = anilist._run_queued_anilist_progress
_start_queued_anilist_progress = anilist._start_queued_anilist_progress
queue_anilist_progress = anilist.queue_anilist_progress
retry_queued_anilist_writes = anilist.retry_queued_anilist_writes
flush_anilist_writes = anilist.flush_anilist_writes
_reconcile_status = anilist._reconcile_status
_push_local_progress = anilist._push_local_progress

fetch_anilist_list = anilist.fetch_anilist_list
search_anilist = anilist.search_anilist
fetch_anilist_by_ids = anilist.fetch_anilist_by_ids
load_anilist_browse = anilist.load_anilist_browse
# Shared read caches now live in core.anilist; keep aliases for callers/tests.
_anilist_list_cache = anilist._anilist_list_cache
_anilist_search_cache = anilist._anilist_search_cache


# ── API / Catalog ─────────────────────────────────────────────────────────────
from media_cli.services.catalog import (
    ensure_episode_ids,
    get_episode_data,
    search_anime,
    check_translation_switch_capability,
    episode_catalog_error,
)
from media_cli.core.processes import (
    register_subprocess,
    unregister_subprocess,
    kill_active_subprocesses,
)
from media_cli.media.download import download_episode
from media_cli.media.sources import ping_stream_liveness
from media_cli.domain.matching import (
    _run_anilist_match_search,
    _run_manual_anilist_match,
    _run_manual_match_search,
    match_anilist_show_to_provider,
    match_provider_show_to_anilist,
)
from media_cli.domain.titles import title_provider_key
get_provider_id = title_provider_key

# ── Background resolver ───────────────────────────────────────────────────────
# Stream pool + background resolver live in core.streams; aliases below keep
# existing callers (app/playback.py, app/search.py, tests) working unchanged.
all_streams = streams.all_streams
_streams_lock = streams._streams_lock
_bg_lock = streams._bg_lock
_clear_streams = streams._clear_streams
_extend_streams = streams._extend_streams
_stream_snapshot = streams._stream_snapshot
_stream_count = streams._stream_count
_prune_dead_stream = streams._prune_dead_stream
_get_cached_ep_data = streams._get_cached_ep_data
_set_cached_ep_data = streams._set_cached_ep_data
_publish_stream = streams._publish_stream
_generation_is_current = streams._generation_is_current
_update_bg_stats = streams._update_bg_stats
start_bg_resolve = streams.start_bg_resolve
get_cache_stats = streams.get_cache_stats
debug_dump_cache = streams.debug_dump_cache
clear_cache = streams.clear_cache


# ── Main ──────────────────────────────────────────────────────────────────────


# ... existing code ...

fetch_episode_stream = streams.fetch_episode_stream


# Search coordinator functions

# Also expose the query/results attribute names for the search coordinator
_last_anikoto_query_str = ""
_last_anikoto_shows = []
_last_anilist_query_str = ""
_last_anilist_shows = []
_last_miruro_query_str = ""
_last_miruro_shows = []
_last_animexin_query_str = ""
_last_animexin_shows = []
_last_animekhor_query_str = ""
_last_animekhor_shows = []
_last_lucifer_query_str = ""
_last_lucifer_shows = []
_last_animexin_query_str = ""
_last_animexin_shows = []

# Provider search coordinator functions
from media_cli.app import search_coordinator as _search_coord

_make_provider_oneshot_search = _search_coord.make_provider_oneshot_search
_make_allanime_oneshot_search = _search_coord.make_allanime_oneshot_search
_make_anilist_oneshot_search = _search_coord.make_anilist_oneshot_search

# Also export the actual functions
make_provider_oneshot_search = _search_coord.make_provider_oneshot_search
make_allanime_oneshot_search = _search_coord.make_allanime_oneshot_search
make_anilist_oneshot_search = _search_coord.make_anilist_oneshot_search
_cached_search_results = _search_coord._cached_search_results
_remember_search_results = _search_coord._remember_search_results
_provider_search_cache = _search_coord._provider_search_cache


# Provider registry exports
from media_cli.providers import provider_display_name as _provider_display_name
from media_cli.providers import provider_key as _provider_key

provider_key = _provider_key
provider_display_name = _provider_display_name

from media_cli.core.network import is_online, force_check_online, is_confirmed_offline
delete_offline_episode = storage.delete_offline_episode
