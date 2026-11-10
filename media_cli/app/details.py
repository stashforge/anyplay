"""
Details and update state handlers for Anyplay.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..domain.titles import title_provider_key

from media_cli import app_core

if TYPE_CHECKING:
    from ..context import CliFlags, MachineState, UiState

from ..domain.episodes import (
    episode_id_at,
    episode_index_for_id,
)
from ..ui.transition import PreparationCancelled
from .details_preparation import PreparedDetails, commit_details_transition

@dataclass(frozen=True)
class _PreparedDetailsState:
    show: dict
    episode_ids: tuple[str, ...]
    ttype: str
    use_anilist: bool = False
    needs_manual_match: bool = False


def handle_details_state(
    flags: CliFlags,
    ui: UiState,
    ms: MachineState,
    cfg: dict,
    args: Any,
    ttype: str,
    resolve_tracking_fn,) -> str:
    s = ui.ui_show_ctx
    ttype_local = ui.ui_ttype_ctx

    has_provider_link = bool((s.get("_provider") or s.get("provider")) and s.get("_has_provider_link") is not False)
    _is_dl = getattr(ms, "_is_downloads", False)
    _just_entered = bool(ms.just_picked_anime)
    previous_state = ui.search_prev_state
    from_anilist_context = previous_state in (
        "ANILIST_BROWSE", "ANILIST_SEARCH", "ANILIST_AIRING"
    )
    has_token = bool(cfg.get("anilist_token")) and not flags.incognito_mode
    sync_enabled = bool((getattr(args, "sync", False) or cfg.get("sync") or cfg.get("auto_track")) and not getattr(args, "no_sync", False) and not flags.incognito_mode)

    from .details_catalog import accept_catalog_status, catalog_jobs, interactive_catalog, ready_episode_ids
    from ..ui.transition import call_on_ui_thread
    managed_catalog = bool(has_provider_link and not _is_dl and interactive_catalog(flags, args))
    known_ids = None
    if managed_catalog:
        import uuid
        if not getattr(ms, '_details_catalog_session', None):
            ms._details_catalog_session = uuid.uuid4().hex
        session = ms._details_catalog_session
        known_ids = ready_episode_ids(s, ttype_local)
        if not known_ids:
            status = call_on_ui_thread(lambda: catalog_jobs.read(session, s, ttype_local))
            if status.state == 'loading':
                target = (s.get('_provider'), s.get('_id') or s.get('id'), ttype_local)
                if getattr(ms, '_details_catalog_loading_target', None) != target:
                    ms._details_catalog_loading_target = target
                    app_core.set_action_feedback(s, '')
                ui.action_prev_state = ui.search_prev_state or 'SEARCH'
                from .playback_menu import handle_action_menu_state
                return handle_action_menu_state(
                    flags, ui, ms, cfg, args, ttype_local, resolve_tracking_fn,
                    ready_ids=(), catalog_loading=True,
                    catalog_ready=lambda: catalog_jobs.read(session, s, ttype_local).state != 'loading',
                )
            call_on_ui_thread(lambda: accept_catalog_status(s, ttype_local, status))
            known_ids = ready_episode_ids(s, ttype_local)

    # Workers only receive snapshots; abandoned jobs must not touch live contexts.
    original = deepcopy(s)
    worker_flags = deepcopy(flags)
    worker_ui = deepcopy(ui)
    worker_args = deepcopy(args)
    worker_cfg = deepcopy(cfg)

    def finish_preparation(show, ids, translation_type, status_cb=None):
        show = deepcopy(show)
        use_anilist = bool(
            resolve_tracking_fn(previous_state, worker_args, worker_cfg, show)
            and (app_core.get_show_anilist_id(show) or show.get("_id"))
        )
        has_link = bool(app_core.get_show_anilist_id(show))
        should_sync = has_token and has_link and (from_anilist_context or use_anilist)
        if should_sync and not show.get("_anilist_media_synced"):
            if status_cb:
                status_cb("Syncing AniList data…")
            media = app_core.fetch_anilist_media(
                worker_cfg.get("anilist_token"), app_core.get_show_anilist_id(show)
            )
            if media:
                app_core.update_anime_from_anilist_media(show, media)
            show["_anilist_media_synced"] = True

        if from_anilist_context:
            show.pop("_sync_conflict", None)
            show["_progress_authority"] = "AL"
        elif not use_anilist:
            show.pop("_sync_conflict", None)

        if status_cb:
            status_cb("Preparing title details…")
        app_core.prepare_show_display_state(
            show, translation_type, use_anilist, prefer_watched_episodes=_is_dl
        )
        return _PreparedDetailsState(show, tuple(str(ep) for ep in ids), translation_type, use_anilist)

    def prepare_details(status_cb=None):
        show = deepcopy(original)
        translation_type = ttype_local
        if _just_entered and has_provider_link and not managed_catalog:
            if show.get("_id") and previous_state in (
                "HISTORY", "SEARCH", "ANILIST_SEARCH", "ANILIST_BROWSE", "ANILIST_AIRING"
            ) and show.get("_episode_catalog_state") not in ("loaded", "unavailable", "error"):
                app_core.refresh_history_entry_provider_catalog(
                    {"show": show, "translation_type": translation_type}
                )
            if not show.get("description") or not show.get("genres"):
                if status_cb:
                    status_cb("Loading title info…")
                app_core.enrich_show_if_missing(show)

        ids = []
        if has_provider_link or _is_dl:
            if status_cb:
                status_cb("Loading episodes…")
            ids = list(known_ids) if known_ids is not None else app_core.ensure_episode_ids(show, translation_type)
            if not ids and _is_dl:
                ids = show.get("_episode_ids") or show.get("_downloaded_episodes") or []
            if not ids and translation_type in ("dub", "sub") and not _is_dl and not managed_catalog:
                alt_type = "sub" if translation_type == "dub" else "dub"
                if status_cb:
                    status_cb(f"Checking {alt_type.upper()} episodes…")
                alt_ids = app_core.ensure_episode_ids(show, alt_type)
                if alt_ids:
                    missing_mode = translation_type.upper()
                    translation_type, ids = alt_type, alt_ids
                    app_core.set_action_feedback(
                        show, f"{missing_mode} unavailable • Switched to {translation_type.upper()}"
                    )

        show["_anilist_context"] = from_anilist_context
        if has_token and sync_enabled and not from_anilist_context and not app_core.get_show_anilist_id(show):
            if status_cb:
                status_cb("Finding AniList match…")
            worker_ui.ui_show_ctx = show
            matched = app_core.match_provider_show_to_anilist(
                worker_flags, worker_ui, show, worker_cfg["anilist_token"], False
            )
            if not matched:
                return _PreparedDetailsState(
                    show, tuple(str(ep) for ep in ids), translation_type, needs_manual_match=True
                )
            show = matched
            show["_anilist_media_synced"] = True
        return finish_preparation(show, ids, translation_type, status_cb)

    try:
        prepared = app_core.with_loading("Loading episodes…", prepare_details)
        if prepared.needs_manual_match:
            # The interactive picker must keep ownership of the UI thread.
            matched = app_core._run_manual_anilist_match(
                flags, ui, prepared.show, cfg["anilist_token"]
            )
            show = matched or prepared.show
            if matched:
                show["_anilist_media_synced"] = True
            prepared = app_core.with_loading(
                "Preparing title details…", finish_preparation,
                show, prepared.episode_ids, prepared.ttype,
            )
    except PreparationCancelled:
        return previous_state or "SEARCH"

    from ..ui.transition import call_on_ui_thread
    def accept_details():
        commit_details_transition(s, PreparedDetails(prepared.show, prepared.episode_ids, original))
        ui.ui_ttype_ctx = prepared.ttype
        ms.total_eps = len(prepared.episode_ids) or (ms.total_eps if (has_provider_link or _is_dl) else 0)
        if _just_entered and s.get("_id") and has_provider_link:
            app_core.patch_history_entry_show(s.get("_id"), prepared.ttype, s)
    call_on_ui_thread(accept_details)
    ttype_local = prepared.ttype
    episode_ids = prepared.episode_ids
    use_anilist = prepared.use_anilist
    ms.current_ep_index = episode_index_for_id(
        episode_ids, ms.current_ep, labels=s.get("_episode_labels")
    )
    ms.just_picked_anime = False
    ui.action_prev_state = ui.search_prev_state or "SEARCH"

    # Resolve correct initial episode based on progress
    local_p = s.get("_local_progress")
    local_lbl = s.get("_local_episode_label") or local_p
    al_p = s.get("_anilist_progress")

    watched_idx = None
    if episode_ids:
        if local_p is not None:
            watched_idx = episode_index_for_id(episode_ids, str(local_p), labels=s.get("_episode_labels"))
            if watched_idx is None and local_lbl:
                watched_idx = episode_index_for_id(episode_ids, str(local_lbl), labels=s.get("_episode_labels"))
        if watched_idx is None and al_p:
            try:
                al_int = int(al_p)
                if 0 < al_int <= len(episode_ids):
                    watched_idx = al_int - 1
            except (ValueError, TypeError):
                pass

    status_str = str(s.get("status") or "").upper()
    is_finished = status_str in ("FINISHED", "COMPLETED", "ENDED")
    all_available_watched = bool(
        episode_ids and watched_idx is not None and watched_idx >= len(episode_ids) - 1
    )
    playback_status = str(s.get("_anilist_list", "")).upper() if use_anilist else ""
    is_completed = bool(
        all_available_watched
        and (
            playback_status == "COMPLETED"
            or is_finished
        )
    )

    if episode_ids:
        cur_watched = False
        if ms.current_ep is not None and watched_idx is not None:
            cur_idx = episode_index_for_id(episode_ids, ms.current_ep, labels=s.get("_episode_labels"))
            if cur_idx is not None and cur_idx <= watched_idx:
                cur_watched = True

        resume_show_id = (
            s.get("_id")
            or s.get("id")
            or getattr(ms, "show_id", None)
            or s.get("_folder_name")
            or getattr(ms, "show_title", "")
        )
        cur_has_resume = (
            ms.current_ep is not None
            and not cur_watched
            and resume_show_id
            and (app_core.get_resume_time(resume_show_id, ms.current_ep, title_provider_key(s)) or 0) > 0
        )
        if (
            getattr(ms, "_android_pending_watched_ep", None) is not None
            and getattr(ms, "_android_pending_show_id", None) == s.get("_id")
            and ms.current_ep_index is not None
            and 0 <= ms.current_ep_index < len(episode_ids)
        ):
            ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
        elif not _just_entered and ms.current_ep_index is not None:
            # Returning from an action preserves its episode context; progress
            # updates must not silently select the next unwatched episode.
            ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
        elif cur_has_resume:
            ms.current_ep_index = episode_index_for_id(episode_ids, ms.current_ep, labels=s.get("_episode_labels"))
            if ms.current_ep_index is None:
                ms.current_ep_index = 0
                ms.current_ep = episode_id_at(episode_ids, 0)
        elif is_completed:
            ms.current_ep_index = 0
            ms.current_ep = episode_id_at(episode_ids, 0)
        elif watched_idx is not None:
            target_idx = watched_idx + 1 if watched_idx + 1 < len(episode_ids) else watched_idx
            ms.current_ep_index = target_idx
            ms.current_ep = episode_id_at(episode_ids, target_idx)
        elif ms.current_ep_index is not None and 0 <= ms.current_ep_index < len(episode_ids):
            ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
        else:
            ms.current_ep_index = 0
            ms.current_ep = episode_id_at(episode_ids, 0)

        if (
            getattr(ms, "_is_downloads", False)
            and getattr(ms, "_download_files", None)
            and not is_completed
        ):
            def _is_downloaded_idx(idx):
                ep = episode_id_at(episode_ids, idx)
                if str(ep) in ms._download_files:
                    return True
                lbl = (s.get("_episode_labels") or {}).get(str(ep), str(ep))
                if str(lbl) in ms._download_files:
                    return True
                from ..domain.episodes import clean_episode_identifier, parse_episode_dual_numbers
                prim, sec = parse_episode_dual_numbers(str(lbl))
                clean = (prim or clean_episode_identifier(str(lbl)) or str(lbl)).lstrip("0") or "0"
                if clean in ms._download_files:
                    return True
                return False

            if not _is_downloaded_idx(ms.current_ep_index):
                start = (watched_idx + 1) if (watched_idx is not None and watched_idx + 1 < len(episode_ids)) else 0
                found_dl = None
                for idx_cand in range(start, len(episode_ids)):
                    if _is_downloaded_idx(idx_cand):
                        found_dl = idx_cand
                        break
                if found_dl is None:
                    for idx_cand in range(0, len(episode_ids)):
                        if _is_downloaded_idx(idx_cand):
                            found_dl = idx_cand
                            break
                if found_dl is not None:
                    ms.current_ep_index = found_dl
                    ms.current_ep = episode_id_at(episode_ids, found_dl)

    from .playback_menu import handle_action_menu_state
    if managed_catalog:
        return handle_action_menu_state(flags, ui, ms, cfg, args, ttype_local, resolve_tracking_fn,
                                        ready_ids=tuple(episode_ids))
    return handle_action_menu_state(flags, ui, ms, cfg, args, ttype_local, resolve_tracking_fn)
