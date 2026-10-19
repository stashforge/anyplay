"""
Search and history state handlers for Anyplay.
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import as_completed
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
from ..ui.help import picker_help, search_input_help
from ..ui.actions import ActionBindings
from ..ui.background_loading import BackgroundLoadingStatus
from ..ui.footer import Footer
from ..ui.loading_model import LoadingInteraction, LoadingOwner, LoadingPlacement, LoadingTaskSpec
from ..core.terminal import sanitize_terminal_text
from ..ui.spinner import spinner_from_config
from . import playback as playback_mod
from .footer import context_footer, context_prefix

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

    history_loading = BackgroundLoadingStatus(
        LoadingTaskSpec(
            "refresh", LoadingOwner("history", 0), "Updating",
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

    history_modes = ["Active", "Up to date", "Completed", "All"]
    history_mode = ui.history_filter
    if history_mode not in history_modes:
        history_mode = "Active"

    filtered_hist = app_core.filter_history_entries(hist, history_mode)
    hopts = [app_core.format_history_entry(h) for h in filtered_hist]

    def _rebuild_history_view():
        nonlocal filtered_hist, hopts, _hover_index
        filtered_hist = app_core.filter_history_entries(hist, history_mode)
        fshows = [h.get("show") for h in filtered_hist if isinstance(h.get("show"), dict)]
        if fshows:
            app_core.batch_prepare_shows_display_state(fshows, ttype)
        new_hopts = [app_core.format_history_entry(entry) for entry in filtered_hist]
        hopts[:] = new_hopts
        if _hover_index >= len(filtered_hist):
            _hover_index = max(0, len(filtered_hist) - 1) if filtered_hist else -1

    def _persist_history_entry_state(entry):
        try:
            history_domain.history_entry_category(entry)
            app_core.patch_history_entry_state(entry)
        except Exception:
            pass

    _history_open_time = time.time()
    _hover_index = -1
    _hover_start_time = time.time()
    _refreshed_history_ids = set()
    _in_flight_hover_refresh = set()

    def _refresh_history_entries(entries, *, force=False, status_key="BATCH", message="Checking stale titles..."):
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

        history_refresh_status[status_key] = message
        _picker_mod._needs_redraw = True

        def _worker():
            try:
                provider_entries = []
                for entry in list(entries):
                    s_obj = entry.get("show", {})
                    s_id = str(s_obj.get("_id") or "")
                    if not s_id:
                        continue
                    if app_core.get_show_anilist_id(s_obj):
                        continue
                    if not force and history_domain.history_can_skip_finished_provider_refresh(s_obj):
                        _refreshed_history_ids.add(s_id)
                        continue
                    provider_entries.append(entry)

                provider_entries.sort(
                    key=lambda item: 0 if history_domain.history_entry_category(item) == "Up to date" else 1
                )

                batch_changed = False

                def _refresh_provider_entry(entry):
                    try:
                        changed = app_core.refresh_history_entry_provider_catalog(entry)
                        s_obj = entry.get("show", {})
                        s_obj["_episode_catalog_state"] = "loaded"
                        return entry, bool(changed)
                    except Exception:
                        return entry, False

                with DaemonExecutor(max_workers=4, max_pending=max(4, len(provider_entries) + 1)) as executor:
                    anilist_future = executor.submit(
                        app_core.refresh_history_anilist_airing_batch,
                        entries,
                        force=force,
                    )
                    provider_futures = [
                        executor.submit(_refresh_provider_entry, entry)
                        for entry in provider_entries
                    ]

                    if anilist_future.result():
                        batch_changed = True
                        _rebuild_history_view()
                        _picker_mod._needs_redraw = True

                    for entry in entries:
                        s_obj = entry.get("show", {})
                        s_id = str(s_obj.get("_id") or "")
                        if s_id and (
                            app_core.get_show_anilist_id(s_obj)
                            or s_obj.get("_anilist_airing_checked_at", 0) >= _history_open_time
                        ):
                            _refreshed_history_ids.add(s_id)

                    for future in as_completed(provider_futures):
                        entry, changed = future.result()
                        s_obj = entry.get("show", {})
                        s_id = str(s_obj.get("_id") or "")
                        if s_id:
                            _refreshed_history_ids.add(s_id)
                        if changed and s_id:
                            app_core.patch_history_entry_show(s_id, entry.get("translation_type", "sub"), s_obj)
                            batch_changed = True
                        _persist_history_entry_state(entry)

                if batch_changed:
                    _rebuild_history_view()
                    _picker_mod._needs_redraw = True
            except Exception:
                pass
            finally:
                history_refresh_status.pop(status_key, None)
                _rebuild_history_view()
                _picker_mod._needs_redraw = True

        threading.Thread(target=_worker, daemon=True).start()
        return True

    if not getattr(flags, "plain_mode", False) and not flags.incognito_mode:
        _refresh_history_entries(hist)

    def _trigger_hover_refresh_if_needed():
        if not (0 <= _hover_index < len(filtered_hist)):
            return
        entry = filtered_hist[_hover_index]
        show = entry.get("show", {})
        show_id = str(show.get("_id") or "")
        if not show_id or show_id in _refreshed_history_ids or show_id in _in_flight_hover_refresh:
            return

        # AniList-backed titles are already handled by the AniList batch check
        if app_core.get_show_anilist_id(show) and show.get("description") and show.get("genres"):
            _refreshed_history_ids.add(show_id)
            return

        # Finished titles with known episode counts don't have new episodes to check
        status = str(show.get("status") or "").upper()
        if not history_domain.history_entry_should_auto_refresh(entry, now=time.time()):
            _refreshed_history_ids.add(show_id)
            return

        _in_flight_hover_refresh.add(show_id)
        history_refresh_status["SINGLE"] = "Checking stale title..."
        _picker_mod._needs_redraw = True

        def _worker():
            try:
                changed = app_core.refresh_history_entry_provider_catalog(entry)
                _refreshed_history_ids.add(show_id)
                show["_episode_catalog_state"] = "loaded"
                if changed:
                    app_core.patch_history_entry_show(show_id, entry.get("translation_type", "sub"), show)
                    _rebuild_history_view()
                    _picker_mod._needs_redraw = True
                _persist_history_entry_state(entry)
            except Exception:
                pass
            finally:
                _in_flight_hover_refresh.discard(show_id)
                history_refresh_status.pop("SINGLE", None)
                _picker_mod._needs_redraw = True

        threading.Thread(target=_worker, daemon=True).start()

    def _history_footer(entry, width, statuses=()):
        updated = app_core.format_history_updated_time(entry)
        incog_badge = "INCOGNITO (READ-ONLY)" if flags.incognito_mode else ""
        show = entry.get("show", {}) if isinstance(entry, dict) else {}
        context = (context_prefix(show, ui, ms, mode="History"), incog_badge, f"Updated {updated}" if updated else "")
        default = Footer(statuses=tuple(statuses),
                         context=tuple(sanitize_terminal_text(part) for part in context if part),
                         actions=("Enter open", "Tab/Ctrl+N next", "Ctrl+R refresh title",
                                  "F5/Alt+R refresh view", "Del/Ctrl+D delete", "Esc quit"),
                         status_separator=" • ")
        return app_core._poster_footer_line(show, default, width)

    def _hist_top_hdr(si):
        if filtered_hist:
            idx = si if 0 <= si < len(filtered_hist) else max(0, min(si, len(filtered_hist) - 1))
            show = filtered_hist[idx].get("show", {})
            ui.hovered_show_id = show.get("_id")
            ui.hovered_show_obj = show
            app_core._hovered_show_id = ui.hovered_show_id
            poster = app_core._get_poster(show)
            if poster:
                return poster
        return ""

    def _hist_hdr(si):
        nonlocal _hover_index, _hover_start_time
        if si != _hover_index:
            _hover_index = si
            _hover_start_time = time.time()

        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80
        parts = []
        selected_show = {}
        if not filtered_hist:
            if history_mode == "Active":
                msg = "No active history entries."
            elif history_mode == "Up to date":
                msg = "No up-to-date titles."
            elif history_mode == "Completed":
                msg = "No completed titles."
            else:
                msg = "Watch history is empty."
            parts.extend(["", f"\033[38;5;244m{msg}\033[0m", ""])
            selected_entry = {}
        else:
            idx = si if 0 <= si < len(filtered_hist) else max(0, min(si, len(filtered_hist) - 1))
            h = filtered_hist[idx]
            tt = h.get("translation_type", "sub")
            selected_show = h.get("show", {})
            app_core.build_info_panel(selected_show, tt, w, parts, local_only=True, main_title=selected_show.get('name'))
            selected_entry = h

        status_msg = history_refresh_status.get("BATCH") or history_refresh_status.get("SINGLE")

        status = history_loading.read(bool(status_msg))
        parts.append(_history_footer(selected_entry, w, (status,) if status is not None else ()))

        return "\n".join(parts)


    def _hist_delete(si):
        nonlocal hist, filtered_hist, hopts
        if 0 <= si < len(filtered_hist):
            h = filtered_hist[si]
            show = h.get("show", {})
            if app_core.delete_history_entry(show.get("_id"), h.get("translation_type", "sub")):
                hist = app_core.load_history()
                filtered_hist = app_core.filter_history_entries(hist, history_mode)
                fshows = [x.get("show") for x in filtered_hist if isinstance(x.get("show"), dict)]
                if fshows:
                    app_core.batch_prepare_shows_display_state(fshows, ttype)
                new_hopts = [app_core.format_history_entry(x) for x in filtered_hist]
                hopts[:] = new_hopts
        return hopts, _hist_hdr(0)

    def _set_history_mode(next_index):
        nonlocal history_mode, filtered_hist, hopts
        history_mode = history_modes[next_index % len(history_modes)]
        ui.history_filter = history_mode
        filtered_hist = app_core.filter_history_entries(hist, history_mode)
        fshows = [entry.get("show") for entry in filtered_hist if isinstance(entry.get("show"), dict)]
        if fshows:
            app_core.batch_prepare_shows_display_state(fshows, ttype)
        new_hopts = [app_core.format_history_entry(entry) for entry in filtered_hist]
        hopts[:] = new_hopts
        return hopts, _hist_hdr(0)

    def _hist_tab(_selected=None, direction=1):
        mode_index = history_modes.index(history_mode)
        return _set_history_mode(mode_index + direction)

    def _hist_refresh(selected=None):
        if selected is None or not (0 <= selected < len(filtered_hist)):
            return hopts, _hist_hdr(0)
        entry = filtered_hist[selected]
        show_id = str((entry.get("show") or {}).get("_id") or "")
        if show_id:
            _refreshed_history_ids.discard(show_id)
        _refresh_history_entries(
            [entry],
            force=True,
            status_key="SINGLE",
            message="Refreshing selected title...",
        )
        _rebuild_history_view()
        return hopts, _hist_hdr(0)

    def _hist_refresh_visible(_selected=None):
        _refreshed_history_ids.clear()
        _refresh_history_entries(
            filtered_hist,
            force=True,
            status_key="BATCH",
            message="Refreshing visible titles...",
        )
        _rebuild_history_view()
        return hopts, _hist_hdr(0)

    def _hist_tick():
        now = time.time()
        if _hover_index >= 0 and (now - _hover_start_time) >= 1.2:
            _trigger_hover_refresh_if_needed()
        return (
            history_refresh_status.get("BATCH") is not None
            or history_refresh_status.get("SINGLE") is not None
            or bool(_in_flight_hover_refresh)
        )

    def _hist_item_prefix(oi):
        if 0 <= oi < len(filtered_hist):
            if filtered_hist[oi].get("has_new_release"):
                return "\033[38;2;166;227;161m◆\033[0m "
        return ""

    selected_history_episode_ids = {}

    def _hist_select(selected):
        if selected is None or not (0 <= selected < len(filtered_hist)):
            return True
        entry = filtered_hist[selected]
        selected_ttype = entry.get("translation_type", "sub")
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
        selected_history_episode_ids[selected] = ids
        _persist_history_entry_state(entry)
        return True

    def _hist_prompt():
        base = f"Watch History · {history_mode}"
        if flags.incognito_mode:
            return f"\033[38;2;155;125;185mINCOGNITO\033[0m · {base}"
        return base

    hist_help = picker_help(
        "Open details",
        "Search",
        "Quit",
        "Next filter",
        "Previous filter",
        "Remove from history",
        reverse_label="Refresh selected title",
    )
    hist_help["Alt+R"] = "Refresh visible titles"
    hist_help["F5"] = "Refresh visible titles"

    try:
        hidx = tui_pick(
            flags, ui,
            _hist_prompt, hopts,
            header_fn=_hist_hdr,
            top_header_fn=_hist_top_hdr,
            tab_fn=_hist_tab,
            reverse_fn=_hist_refresh,
            alt_reverse_fn=_hist_refresh_visible,
            delete_fn=_hist_delete,
            live_fn=lambda _query: (list(hopts), "", False),
            info_fn=app_core.make_info_fn(lambda: [e.get("show", {}) for e in filtered_hist], ui),
            help_dict=hist_help,
            keep_cursor_hidden_on_select=True,
            count_total=lambda: len(hist),
            tick_fn=_hist_tick,
            item_prefix_fn=_hist_item_prefix,
            select_fn=_hist_select,
        )
    finally:
        history_loading.read(False)
    if hidx == -2:
        return "QUIT"
    if hidx == -3:
        return "SEARCH"
    if 0 <= hidx < len(filtered_hist):
        playback_mod._clear_episode_source_state(ms)
        h = filtered_hist[hidx]
        show = h.get("show", {})
        ttype_hist = h.get("translation_type", "sub")
        app_core.prepare_show_display_state(show, ttype_hist)
        ms.show_id = show.get("_id")
        ms.show_title = get_show_display_title(show)
        ms.total_eps = show.get("availableEpisodes", {}).get(ttype_hist, 1)
        episode_ids = selected_history_episode_ids.get(hidx)
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
    provider_id = app_core.provider_key(getattr(args, "provider", "allanime"))
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

    def _search_cover_header(get_results_fn):
        def _hdr(si):
            shows_local = get_results_fn()
            if shows_local and 0 <= si < len(shows_local):
                ui.hovered_show_id = shows_local[si].get("_id") or shows_local[si].get("id")
                ui.hovered_show_obj = shows_local[si]
                app_core._hovered_show_id = ui.hovered_show_id
                poster = app_core._get_poster(shows_local[si])
                if poster:
                    return poster
            return ""
        return _hdr

    def _search_result_header(provider_name, base_query, ttype_local, get_results_fn, get_loading_fn, esc_action="quit", get_error_fn=None):
        def _hdr(si):
            return app_core.render_search_header(
                provider_name,
                base_query,
                ttype_local,
                get_results_fn,
                get_loading_fn,
                selected_idx=si,
                esc_action=esc_action,
                get_error_fn=get_error_fn,
                filter_query=ui.active_picker_query or "",
                badges=_session_badges(flags, args, search_context=True),
            )
        return _hdr

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
    initial_opts = [f"{app_core.get_show_display_title(s)}" for s in shows_list]
    hd2 = picker_help("Select anime", "New search", "New search")

    # Pre-compute headers for all shows (0-delay cursor navigation)
    _precomputed_headers: dict[int, str] = {}
    _get_error_fn = lambda: (get_error() or ui.search_error)
    if not get_loading():
        for i, show in enumerate(shows_list):
            _precomputed_headers[i] = app_core.render_search_header(
                provider_name,
                ms.query_str,
                ttype,
                get_results,
                get_loading,
                selected_idx=i,
                esc_action="search",
                get_error_fn=_get_error_fn,
                filter_query="",
                badges=_session_badges(flags, args, search_context=True),
            )

    _last_loading_msg = ""

    def _cached_header_fn(si):
        nonlocal _last_loading_msg
        if ui.active_picker_query:
            return app_core.render_search_header(
                provider_name,
                ms.query_str,
                ttype,
                get_results,
                get_loading,
                selected_idx=si,
                esc_action="search",
                get_error_fn=_get_error_fn,
                filter_query=ui.active_picker_query or "",
                badges=_session_badges(flags, args, search_context=True),
            )
        current_results = get_results()
        if not (0 <= si < len(current_results)):
            return app_core.render_search_header(
                provider_name,
                ms.query_str,
                ttype,
                get_results,
                get_loading,
                selected_idx=si,
                esc_action="search",
                get_error_fn=_get_error_fn,
                filter_query=ui.active_picker_query or "",
                badges=_session_badges(flags, args, search_context=True),
            )

        current_loading = get_loading()
        if current_loading != _last_loading_msg:
            _last_loading_msg = current_loading
            _precomputed_headers.clear()

        show = current_results[si]
        cached = _precomputed_headers.get(si)

        if (
            cached is None
            or bool(current_loading)
            or show.get("_poster_status") == "loading"
            or "Loading cover" in cached
            or "Enriching metadata" in cached
            or "Searching…" in cached
        ):
            hdr = app_core.render_search_header(
                provider_name,
                ms.query_str,
                ttype,
                get_results,
                get_loading,
                selected_idx=si,
                esc_action="search",
                get_error_fn=_get_error_fn,
                filter_query="",
                badges=_session_badges(flags, args, search_context=True),
            )
            if (
                not current_loading
                and show.get("_poster_status") != "loading"
                and "Loading cover" not in hdr
                and "Enriching metadata" not in hdr
                and "Searching…" not in hdr
            ):
                _precomputed_headers[si] = hdr
            return hdr

        return cached

    loaded_episodes_map: dict[str, list[str]] = {}

    def _on_select(idx):
        shows = get_results()
        if not shows or not (0 <= idx < len(shows)):
            return False
        s = shows[idx]
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
            app_core.set_action_feedback(s, err_msg)
            ui.search_error = err_msg
            return False
        commit_details_transition(s, prepared)
        ui.ui_show_ctx = s
        ui.ui_ttype_ctx = ttype
        ui.hovered_show_obj = s
        app_core._hovered_show_id = s.get("_id") or s.get("id")
        loaded_episodes_map[str(s.get("_id") or idx)] = ep_ids
        return True

    idx = tui_pick(
            flags, ui,
            search_title, initial_opts,
            header_fn=_cached_header_fn,
            top_header_fn=_search_cover_header(get_results),
            live_fn=live_fn,
            initial_query="",
            is_search=False,
            help_dict=hd2,
            auto_select_single_when_done=ms.just_searched,
            info_fn=app_core.make_info_fn(get_results, ui),
            select_fn=_on_select,
        )

    shows = get_results()
    # Replace global mutations by mutating MachineState
    ms.shows = shows
    ms.last_aa_query_str = ms.query_str
    ms.last_aa_shows = shows

    if idx in (-2, -3):
        ms.query_str = ""
        return "SEARCH"
    elif idx == -4:
        ui.search_error = (
            get_error() or f'No results found for "{ms.query_str}"'
        )
        ms.query_str = ""
        return "SEARCH"
    elif isinstance(idx, str):
        if idx.strip():
            ms.query_str = idx.strip()
        return "SEARCH"
    else:
        s = shows[idx]
        ms.show_id = s["_id"]
        ms.total_eps = s.get("availableEpisodes", {}).get(ttype, 0)
        episode_ids = loaded_episodes_map.get(str(s.get("_id") or idx)) or app_core.load_episode_ids_for_selection(s, ttype)
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
            err_msg = app_core.episode_catalog_error(s)
            ui.search_error = err_msg
            app_core.set_action_feedback(s, err_msg)
            return "SEARCH"

        if requested_episode_missing:
            return "EPISODE"

        if getattr(args, "download", False):
            from .playback_menu import handle_download_menu_state
            return handle_download_menu_state(flags, ui, ms, cfg, args, ttype)

        return "DETAILS"
