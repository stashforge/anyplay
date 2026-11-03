"""
Search and history state handlers for Anyplay.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from concurrent.futures import as_completed
from copy import deepcopy
from dataclasses import replace
from ..core.workers import DaemonExecutor
from typing import TYPE_CHECKING, Any

from media_cli import app_core
from media_cli.ui.picker import tui_pick

if TYPE_CHECKING:
    from ..context import CliFlags, MachineState, UiState

from ..domain import history as history_domain
from ..domain.episodes import episode_id_at, episode_index_for_id
from ..domain.titles import get_show_display_title
from ..ui import picker as _picker_mod
from ..ui.help import search_input_help
from ..ui.actions import ActionBindings
from ..ui.background_loading import BackgroundLoadingStatus
from ..ui.loading_model import LoadingInteraction, LoadingOwner, LoadingPlacement, LoadingTaskSpec
from ..ui.spinner import spinner_from_config
from ..ui.transition import call_on_ui_thread
from . import playback as playback_mod
from .search_results_controller import run_search_results
from .history_refresh_handoff import HistoryRefreshInbox, merge_history_refresh
from .history_diagnostics import HistoryDiagnostics
from .history_menu_view import HISTORY_MODES, HistoryEntryReference, build_history_view

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
_C_HINT = "\033[38;5;244m"
_RST    = "\033[0m"
YELLOW  = "\033[1;33m"


def _footer_parts(*parts):
    return " • ".join(str(part) for part in parts if part)


def _session_badges(flags, args, *, search_context=False):
    badges = []
    if getattr(flags, "incognito_mode", False):
        badges.append("\033[38;2;155;125;185mINCOGNITO\033[0m")
    if search_context and getattr(args, "sync", False) and not getattr(args, "no_sync", False):
        badges.append("Sync On")
    return badges


def prompt_search_query(flags, ui, *, provider_name, search_title="Search Anime"):
    """Use the existing query-entry screen for any provider's new search."""
    def header(_index):
        parts = [""]
        message = "Search & watch history is paused." if flags.incognito_mode else "Use Up/Down to browse previous searches."
        parts.append(f"{_C_HINT}{message}{_RST}")
        parts.append(f"\033[38;5;250mProvider: \033[1;97m{provider_name}{_RST}")
        nav = "Enter=search  ? = Help  Esc=quit"
        if flags.incognito_mode:
            nav = f"\033[38;2;155;125;185mINCOGNITO\033[0m | {nav}"
        parts.append(f"{_C_HINT}{ui.search_error + '  |  Esc=quit' if ui.search_error else nav}{_RST}")
        return "\n".join(parts)

    return tui_pick(
        flags, ui, search_title, [], header_fn=header,
        return_query_on_enter=True,
        query_history=[] if flags.incognito_mode else app_core.load_search_history(),
        delete_fn=None if flags.incognito_mode else app_core.delete_search_history_entry,
        is_search=True, help_dict=search_input_help("Quit"),
    )


# ---------------------------------------------------------------------------
# HISTORY
# ---------------------------------------------------------------------------
history_refresh_status = {}
_history_session_refreshed = False

def handle_history_state(
    flags: CliFlags,
    ui: UiState,
    ms: MachineState,
    cfg: dict,
    args: Any,
    ttype: str,
    resolve_tracking_fn,) -> str:
    hist = app_core.load_history()
    if not hist:
        app_core.warn("Watch history is empty.")
        return "QUIT"
    diagnostics = HistoryDiagnostics()

    history_loading = BackgroundLoadingStatus(
        LoadingTaskSpec(
            "refresh", LoadingOwner("history", 0), "Refreshing history...",
            LoadingInteraction.BACKGROUND, LoadingPlacement.FOOTER,
            ActionBindings(()), delay=0,
        ),
        spinner_from_config(cfg),
    )

    # Pre-warm all history shows display state in RAM immediately
    shows = [h.get("show") for h in hist if isinstance(h.get("show"), dict)]
    if shows:
        app_core.batch_prepare_shows_display_state(shows, ttype)
        if hasattr(app_core, "_poster_manager") and hasattr(app_core._poster_manager, "prewarm_shows"):
            app_core._poster_manager.prewarm_shows(shows)

    history_modes = HISTORY_MODES
    history_mode = ui.history_filter
    if history_mode not in history_modes:
        history_mode = "Active"

    filtered_hist = app_core.filter_history_entries(hist, history_mode)
    history_base_view = None

    def _rebuild_history_view():
        nonlocal filtered_hist, history_base_view
        history_base_view = None
        with diagnostics.phase('filter'):
            filtered_hist = app_core.filter_history_entries(hist, history_mode)
        fshows = [h.get("show") for h in filtered_hist if isinstance(h.get("show"), dict)]
        if fshows:
            with diagnostics.phase('display_prepare'):
                app_core.batch_prepare_shows_display_state(fshows, ttype)

    def _persist_history_entry_state(entry):
        try:
            history_domain.history_entry_category(entry)
            app_core.patch_history_entry_state(entry)
        except Exception:
            pass

    _history_open_time = time.time()
    _refreshed_history_references = set()
    _in_flight_hover_refresh = set()
    refresh_inbox = call_on_ui_thread(HistoryRefreshInbox)
    pending_refreshes = {}

    def _start_refresh(entries, work, *, status_key, message, hover_reference=None):
        def start():
            requests = refresh_inbox.begin(tuple(entries))
            pending_refreshes[object()] = (status_key, message, requests, hover_reference)
            history_refresh_status[status_key] = message
            _picker_mod._needs_redraw = True
            threading.Thread(target=refresh_inbox.prepare, args=(requests, work), daemon=True).start()

        call_on_ui_thread(start)

    def _accept_refresh(update, latest_by_reference):
        if update.entry is None:
            return
        target = next((entry for entry in hist if HistoryEntryReference.from_entry(entry) == update.reference), None)
        latest = latest_by_reference.get(update.reference)
        if target is None or latest is None:
            return
        merge_history_refresh(target, update, latest)
        _refreshed_history_references.add(update.reference)
        return target

    def _drain_refreshes():
        latest_by_reference = None
        refreshed_entries = []

        def accept(update):
            nonlocal latest_by_reference
            if update.entry is None:
                return
            if latest_by_reference is None:
                latest_by_reference = {
                    HistoryEntryReference.from_entry(entry): entry
                    for entry in app_core.load_history()
                }
            target = _accept_refresh(update, latest_by_reference)
            if target is not None:
                refreshed_entries.append(target)

        with diagnostics.phase('accept'):
            accepted = refresh_inbox.drain(accept)
        diagnostics.count('accepted', accepted)
        if refreshed_entries:
            try:
                with diagnostics.phase('persist'):
                    app_core.patch_history_entries_state(refreshed_entries)
            except Exception:
                logging.getLogger(__name__).debug("History refresh batch save failed", exc_info=True)
        completed_keys = set()
        for token, (key, message, requests, hover_reference) in tuple(pending_refreshes.items()):
            if any(refresh_inbox.is_pending(request) for request in requests):
                continue
            pending_refreshes.pop(token)
            completed_keys.add(key)
            if hover_reference and not any(pending[3] == hover_reference for pending in pending_refreshes.values()):
                _in_flight_hover_refresh.discard(hover_reference)
        for key in completed_keys:
            remaining = [message for status, message, _, _ in pending_refreshes.values() if status == key]
            if remaining:
                history_refresh_status[key] = remaining[-1]
            else:
                history_refresh_status.pop(key, None)
        if accepted or completed_keys:
            _rebuild_history_view()
            _picker_mod._needs_redraw = True

    def _refresh_history_entries(entries, *, force=False, status_key="BATCH", message=None):
        nonlocal _history_open_time
        _history_open_time = time.time()
        entries = list(entries or [])
        if not force:
            entries = [
                entry for entry in entries
                if history_domain.history_entry_should_auto_refresh(entry, now=_history_open_time)
            ]
        if not entries:
            return False
        if message is None:
            verb = "Refreshing" if force else "Checking"
            noun = "title" if len(entries) == 1 else "titles"
            message = f"{verb} {len(entries)} {noun}..."

        def _work(prepared):
            provider_entries = [entry for entry in prepared if (entry.get("show") or {}).get("_id")
                                and not app_core.get_show_anilist_id(entry["show"])
                                and (force or not history_domain.history_can_skip_finished_provider_refresh(entry["show"]))]
            provider_entries.sort(key=lambda entry: history_domain.history_entry_category(entry) != "Up to date")
            # Complete metadata enrichment before provider workers touch the same copies.
            changed = bool(app_core.refresh_history_anilist_airing_batch(prepared, force=force, persist=False))

            def refresh_provider(entry):
                before = deepcopy(entry)
                try:
                    return app_core.refresh_history_entry_provider_catalog(entry)
                except Exception:
                    logging.getLogger(__name__).debug("History provider refresh failed", exc_info=True)
                    entry.clear()
                    entry.update(before)
                    return False

            with DaemonExecutor(max_workers=4, max_pending=max(4, len(provider_entries) + 1)) as executor:
                futures = [executor.submit(refresh_provider, entry) for entry in provider_entries]
                for future in as_completed(futures):
                    changed = bool(future.result()) or changed
            return changed

        _start_refresh(entries, _work, status_key=status_key, message=message)
        return True

    if not getattr(flags, "plain_mode", False) and not flags.incognito_mode:
        _refresh_history_entries(hist)

    def _refresh_hover_reference(reference):
        entry = next((entry for entry in filtered_hist
                      if HistoryEntryReference.from_entry(entry) == reference), None)
        if entry is None:
            return
        show = entry.get("show", {})
        if reference in _refreshed_history_references or reference in _in_flight_hover_refresh:
            return

        # AniList-backed titles are already handled by the AniList batch check
        if app_core.get_show_anilist_id(show) and show.get("description") and show.get("genres"):
            _refreshed_history_references.add(reference)
            return

        # Respect the shared finished-title and refresh cooldown rules.
        if not history_domain.history_entry_should_auto_refresh(entry, now=time.time()):
            _refreshed_history_references.add(reference)
            return

        _in_flight_hover_refresh.add(reference)
        _start_refresh(
            (entry,), lambda prepared: bool(app_core.refresh_history_entry_provider_catalog(prepared[0])),
            status_key="SINGLE", message="Checking this title...", hover_reference=reference,
        )

    def _history_refresh_status():
        message = history_refresh_status.get("BATCH") or history_refresh_status.get("SINGLE")
        return history_loading.read(bool(message), message=message or None)

    def _delete_history_reference(reference):
        nonlocal hist, filtered_hist
        if flags.incognito_mode:
            return
        h = next((entry for entry in filtered_hist if HistoryEntryReference.from_entry(entry) == reference), None)
        if h is not None:
            if app_core.delete_history_entry(reference.show_id, reference.translation_type, provider_id=reference.provider_id):
                refresh_inbox.invalidate(reference)
                hist = app_core.load_history()
                _rebuild_history_view()

    def _set_history_mode(mode):
        nonlocal history_mode, filtered_hist
        if mode not in history_modes:
            raise ValueError(f"Unknown History mode: {mode!r}")
        history_mode = mode
        ui.history_filter = history_mode
        _rebuild_history_view()

    def _refresh_history_reference(reference):
        entry = next((entry for entry in filtered_hist if HistoryEntryReference.from_entry(entry) == reference), None)
        if entry is None:
            return
        _refreshed_history_references.discard(reference)
        _refresh_history_entries(
            [entry],
            force=True,
            status_key="SINGLE",
            message="Refreshing this title...",
        )
        _rebuild_history_view()

    def _refresh_visible_history():
        _refreshed_history_references.clear()
        _refresh_history_entries(
            filtered_hist,
            force=True,
            status_key="BATCH",
        )
        _rebuild_history_view()

    selected_history_episode_ids = {}

    def _prepare_history_reference(reference):
        entry = next((entry for entry in filtered_hist if HistoryEntryReference.from_entry(entry) == reference), None)
        if entry is None:
            return False
        refresh_inbox.invalidate(reference)
        selected_ttype = entry.get("translation_type", "sub")
        from .details_catalog import interactive_catalog, ready_episode_ids
        if interactive_catalog(flags, args):
            selected_history_episode_ids[reference] = list(ready_episode_ids(entry.get('show', {}), selected_ttype))
            return True
        from copy import deepcopy
        from ..ui.transition import PreparationCancelled

        detached = deepcopy(entry)
        def prepare():
            if history_domain.history_entry_refresh_is_stale(detached, now=time.time()):
                app_core.refresh_history_entry_provider_catalog(detached)
            ids = app_core.ensure_episode_ids(detached.get("show", {}), selected_ttype)
            app_core.prepare_show_display_state(detached.get("show", {}), selected_ttype)
            return ids

        try:
            ids = app_core.with_loading("Checking episode list…", prepare)
        except PreparationCancelled:
            return False
        entry.update(detached)
        selected_history_episode_ids[reference] = ids
        _persist_history_entry_state(entry)
        return True

    def _read_history_navigation():
        nonlocal history_base_view
        status = _history_refresh_status()
        if history_base_view is None:
            with diagnostics.phase('build_view'):
                history_base_view = build_history_view(
                    filtered_hist, mode=history_mode, total_count=len(hist),
                    incognito=flags.incognito_mode, now=_history_open_time,
                    show_cover=flags.show_image,
                )
        return (replace(history_base_view, footer_statuses=(status,))
                if status is not None else history_base_view)
    def _show_history_information(reference):
        nonlocal history_base_view
        entry = next((entry for entry in filtered_hist
                      if HistoryEntryReference.from_entry(entry) == reference), None)
        if entry is not None:
            try:
                app_core.make_info_fn(lambda: [entry.get("show", {})], ui)(0)
            finally:
                history_base_view = None

    try:
        if not flags.plain_mode and sys.stdin.isatty():
            from .history_menu_controller import run_history_menu

            outcome = call_on_ui_thread(lambda: run_history_menu(
                flags, ui, read_view=_read_history_navigation, switch_mode=_set_history_mode,
                select=_prepare_history_reference, refresh_title=_refresh_history_reference,
                refresh_view=_refresh_visible_history,
                delete=None if flags.incognito_mode else _delete_history_reference,
                information=_show_history_information, focus=_refresh_hover_reference, poll=_drain_refreshes,
                diagnostics=diagnostics,
            ))
        else:
            from .history_legacy_picker import run_legacy_history

            outcome = call_on_ui_thread(lambda: run_legacy_history(
                flags, ui, ms, picker=tui_pick, read_entries=lambda: filtered_hist,
                read_view=_read_history_navigation, select=_prepare_history_reference,
                switch_mode=_set_history_mode, refresh_title=_refresh_history_reference,
                refresh_view=_refresh_visible_history,
                delete=None if flags.incognito_mode else _delete_history_reference,
                information=_show_history_information, poll=_drain_refreshes, focus=_refresh_hover_reference,
                read_status=_history_refresh_status,
                is_refreshing=lambda: bool(history_refresh_status.get("BATCH")
                                            or history_refresh_status.get("SINGLE") or _in_flight_hover_refresh),
            ))
    finally:
        diagnostics.report(force=True)
        def close_refreshes():
            refresh_inbox.close()
            for key, _, _, _ in pending_refreshes.values():
                history_refresh_status.pop(key, None)
            history_loading.read(False)

        call_on_ui_thread(close_refreshes)
    if outcome is None:
        return "HISTORY"
    if outcome.kind == "quit":
        return "QUIT"
    if outcome.kind == "back":
        return "SEARCH"
    hidx = next((index for index, entry in enumerate(filtered_hist)
                 if HistoryEntryReference.from_entry(entry) == outcome.reference), -1)
    if 0 <= hidx < len(filtered_hist):
        playback_mod._clear_episode_source_state(ms)
        h = filtered_hist[hidx]
        show = h.get("show", {})
        ttype_hist = h.get("translation_type", "sub")
        app_core.prepare_show_display_state(show, ttype_hist)
        ms.show_id = show.get("_id")
        ms.show_title = get_show_display_title(show)
        ms.total_eps = show.get("availableEpisodes", {}).get(ttype_hist, 1)
        episode_ids = selected_history_episode_ids.get(outcome.reference)
        if episode_ids is None:
            episode_ids = app_core.ensure_episode_ids(show, ttype_hist)

        ms.current_ep = app_core.playback_ep_from_history_entry(
            h, ttype_hist, episode_ids=episode_ids, episode_labels=show.get("_episode_labels") or {},
        )

        ms.total_eps = len(episode_ids) or ms.total_eps
        ms.current_ep_index = episode_index_for_id(episode_ids, ms.current_ep, labels=show.get("_episode_labels"))
        if episode_ids and ms.current_ep_index is not None:
            ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
        elif episode_ids:
            ms.current_ep_index = 0
            ms.current_ep = episode_id_at(episode_ids, 0)

        ui.ui_show_ctx = show
        ui.ui_ttype_ctx = ttype_hist
        ms.just_picked_anime = True
        app_core.set_navigation_context(ui, search_prev="HISTORY", ep_prev="HISTORY", action_prev="HISTORY")
        return "DETAILS"

    return "HISTORY"


# ---------------------------------------------------------------------------
# SEARCH
# ---------------------------------------------------------------------------
def handle_search_state(
    flags: CliFlags,
    ui: UiState,
    ms: MachineState,
    cfg: dict,
    args: Any,
    ttype: str,
    resolve_tracking_fn,) -> str:
    ui.ui_show_ctx = {}
    ms.ep_cache_key = None
    ms.ep_cache_data = None
    app_core._clear_poster_downloads()
    provider_id = app_core.provider_key(
        getattr(args, "provider", None) or cfg.get("provider") or app_core._DEFAULT_PROVIDER_ID
    )
    provider_name = app_core.provider_display_name(provider_id)

    # Determine category: Anime, Donghua, Movies/Shows
    category = "Anime"
    try:
        from ..providers import get_provider_registry
        registry = get_provider_registry()
        meta = registry.get(provider_id, {})
        ptype = (meta.get("type") or "").lower()
        if ptype == "donghua":
            category = "Donghua"
        elif ptype in ("movie", "movies"):
            category = "Movies/Shows"
    except Exception:
        category = "Anime"

    search_title = f"Search {category}"


    # Step 1: Input Page
    if not ms.query_str:
        res = prompt_search_query(flags, ui, provider_name=provider_name, search_title=search_title)
        ui.search_error = ""
        if res == -2:
            return "QUIT"
        if not res:
            return "SEARCH"
        ms.query_str = str(res).strip()
        ms.just_searched = True
        if not flags.incognito_mode and ms.query_str:
            app_core.save_search_history(ms.query_str)
    else:
        if not flags.incognito_mode and ms.query_str:
            app_core.save_search_history(ms.query_str)

    if not ms.query_str:
        app_core.err("Search query cannot be empty.")
        return "QUIT"

    # Step 2: Fetch Results ONCE
    live_fn, get_results, get_loading, get_error = app_core._cached_search_results(
        ms.query_str,
        f"_last_{provider_id}_query_str",
        f"_last_{provider_id}_shows",
        lambda: app_core.make_provider_oneshot_search(
            ms.query_str,
            ttype,
            provider_id,
        )
    )

    # Step 3: Title Selection Page
    app_core.enter_alt_screen()

    shows_list = get_results()
    if shows_list:
        app_core.batch_prepare_shows_display_state(shows_list, ttype)
        if hasattr(app_core, "_poster_manager") and hasattr(app_core._poster_manager, "prewarm_shows"):
            app_core._poster_manager.prewarm_shows(shows_list)

    loaded_episodes_map: dict[str, list[str]] = {}

    def _on_select(s):
        from .details_catalog import interactive_catalog, ready_episode_ids
        if interactive_catalog(flags, args):
            loaded_episodes_map[str(s.get('_id') or s.get('id'))] = list(ready_episode_ids(s, ttype))
            ui.ui_show_ctx = s
            ui.ui_ttype_ctx = ttype
            ui.hovered_show_obj = s
            return True
        from .details_preparation import prepare_details_transition, commit_details_transition
        from ..ui.transition import PreparationCancelled
        try:
            prepared = app_core.with_loading(
                f"Loading {ttype.upper()} episodes…", prepare_details_transition, s, ttype
            )
        except PreparationCancelled:
            return False
        ep_ids = list(prepared.episode_ids)
        if not ep_ids:
            err_msg = app_core.episode_catalog_error(prepared.show)
            app_core.set_action_feedback(prepared.show, err_msg)
        commit_details_transition(s, prepared)
        ui.ui_show_ctx = s
        ui.ui_ttype_ctx = ttype
        ui.hovered_show_obj = s
        app_core._hovered_show_id = s.get("_id") or s.get("id")
        loaded_episodes_map[str(s.get("_id") or s.get("id"))] = ep_ids
        return True

    outcome = run_search_results(
        flags, ui, live_fn=live_fn, get_results=get_results, get_loading=get_loading,
        get_error=get_error, provider_id=provider_id, provider_name=provider_name,
        query=ms.query_str, ttype=ttype, prompt=search_title, auto_single=ms.just_searched,
        restore_position=not ms.just_searched,
        badges=tuple(_session_badges(flags, args, search_context=True)), select=_on_select,
        information=lambda show: app_core.make_info_fn(lambda: [show], ui)(0),
    )

    shows = get_results()
    # Replace global mutations by mutating MachineState
    ms.shows = shows
    ms.last_aa_query_str = ms.query_str
    ms.last_aa_shows = shows

    if outcome.kind == "quit":
        return "QUIT"
    if outcome.kind == "back":
        ms.query_str = ""
        return "SEARCH"
    elif outcome.kind == "empty":
        ui.search_error = (
            outcome.error or get_error() or f'No results found for "{ms.query_str}"'
        )
        ms.query_str = ""
        return "SEARCH"
    else:
        s = outcome.show
        ms.show_id = s.get("_id") if s.get("_id") is not None else s["id"]
        ms.total_eps = s.get("availableEpisodes", {}).get(ttype, 0)
        selection_key = str(s.get("_id") or s.get("id"))
        episode_ids = (
            loaded_episodes_map[selection_key] if selection_key in loaded_episodes_map
            else app_core.load_episode_ids_for_selection(s, ttype)
        )
        ms.total_eps = len(episode_ids) or ms.total_eps
        ms.show_title = get_show_display_title(s)

        ui.ui_show_ctx = s
        ui.ui_ttype_ctx = ttype
        ms.just_picked_anime = True
        ms.just_searched = False
        app_core.set_navigation_context(ui, search_prev="SEARCH", ep_prev="DETAILS", action_prev="DETAILS")


        requested_episode_missing = False
        if args.episode:
            args._cli_episode_passed = True
            requested_ep = str(args.episode)
            requested_idx = episode_index_for_id(
                episode_ids, requested_ep, labels=s.get("_episode_labels")
            )
            args.episode = None
            if requested_idx is not None:
                ms.current_ep_index = requested_idx
                ms.current_ep = episode_id_at(episode_ids, requested_idx)
                ms.selected_stream = None
                app_core._clear_streams()
                return "PLAY"

            if episode_ids:
                app_core.set_action_feedback(
                    s,
                    f"EP {requested_ep} not found • Select an episode",
                )
            else:
                app_core.set_action_feedback(s, app_core.episode_catalog_error(s))
            ms.current_ep_index = 0
            ms.current_ep = episode_id_at(episode_ids, 0) if episode_ids else requested_ep
            requested_episode_missing = True
        elif (h := app_core.get_history_entry(s, ttype)):
            ms.current_ep = app_core.playback_ep_from_history_entry(
                h, ttype, episode_ids=episode_ids, episode_labels=s.get("_episode_labels") or {},
            )
        else:
            ms.current_ep = episode_id_at(episode_ids, 0)

        ms.current_ep_index = episode_index_for_id(episode_ids, ms.current_ep, labels=s.get("_episode_labels"))

        if episode_ids and ms.current_ep_index is not None:
            ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
        elif episode_ids:
            ms.current_ep_index = 0
            ms.current_ep = episode_id_at(episode_ids, 0)
        else:
            from .details_catalog import interactive_catalog
            if not interactive_catalog(flags, args):
                err_msg = app_core.episode_catalog_error(s)
                app_core.set_action_feedback(s, err_msg)
            return "DETAILS"

        if requested_episode_missing:
            return "EPISODE"

        if getattr(args, "download", False):
            from .playback_menu import handle_download_menu_state
            return handle_download_menu_state(flags, ui, ms, cfg, args, ttype)

        return "DETAILS"
