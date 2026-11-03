"""
Details and update state handlers for Anyplay.
"""

from __future__ import annotations

import decimal
import os
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..domain.titles import title_provider_key

from media_cli import app_core
from media_cli.ui.picker import tui_pick

if TYPE_CHECKING:
    from ..context import CliFlags, MachineState, UiState

from ..domain.episodes import (
    detect_next_episode_gap,
    episode_id_at,
    episode_index_for_id,
    episode_progress_number,
    highest_episode_number,
)
from ..domain.titles import get_show_display_title
from ..playback.rules import should_clear_query_on_child_left
from ..ui.help import picker_help
from ..ui.transition import PreparationCancelled
from .details_preparation import PreparedDetails, commit_details_transition
from .footer import context_footer

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
_C_HINT = "\033[38;5;244m"
_RST    = "\033[0m"


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
    has_anilist_link = bool(app_core.get_show_anilist_id(s))
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



    total = s.get("episodeCount")
    try:

        total = int(total) if total is not None else 0
    except ValueError:

        total = 0

    released = s.get("availableEpisodes", {}).get(ttype_local, 0)
    try:

        released = int(released) if released is not None else 0
    except ValueError:

        released = 0
    if episode_ids:
        _highest = highest_episode_number(episode_ids)
        try:
            _highest_num = int(decimal.Decimal(str(_highest)))
        except (decimal.InvalidOperation, ValueError, TypeError):
            _highest_num = len(episode_ids)
        released = max(released, _highest_num)

    nep = s.get("_next_airing_ep")
    if nep is not None:
        try:
            inferred = int(nep) - 1
            if inferred > released:

                released = inferred
        except ValueError:

            pass

    str(s.get("status", "")).upper()
    user_status = str(s.get("_anilist_list", "")).upper()
    playback_status = user_status if use_anilist else ""
    player = args.player or cfg.get("player", "mpv")
    isDesktop = player in ["mpv", "rex", "mpvrx", "mpvrex"] and not app_core.is_termux()

    opts = []
    detail_play_ep = episode_id_at(episode_ids, 0) if episode_ids else None

    # Find next playable using _episode_ids position, not prog arithmetic
    if from_anilist_context or use_anilist:
        al_progress = s.get("_anilist_progress") or 0
        current_ep_label = app_core.episode_id_for_progress(s, ttype_local, al_progress) if al_progress > 0 else "0"
    else:
        current_ep_label = s.get("_local_episode_label") or "0"
    _current_idx = (
        episode_index_for_id(
            episode_ids, current_ep_label, labels=ui.ui_show_ctx.get("_episode_labels")
        )
        if episode_ids
        else None
    )
    if _current_idx is not None and _current_idx + 1 < len(episode_ids):
        detail_next_ep = episode_ids[_current_idx + 1]
    else:
        detail_next_ep = None

    has_gaps, gap_str = detect_next_episode_gap(current_ep_label, detail_next_ep)

    # For display totals, use episode labels only — never mix catalog index

    # next_manual_progress: the episode label to mark when "Mark Next Watched"
    if detail_next_ep:
        episode_progress_number(detail_next_ep, 0)
    else:
        episode_progress_number(current_ep_label, 0) + 1


    try:
        current_num = decimal.Decimal(str(current_ep_label))
    except decimal.InvalidOperation:
        current_num = decimal.Decimal(0)

    from ..domain.episodes import parse_episode_label
    parse_episode_label(current_ep_label)
    al_prog = s.get("_anilist_progress", 0) if use_anilist else 0
    prog = local_p or 0  # Use local progress as primary progress indicator

    show_anilist_actions = has_token and has_anilist_link and (from_anilist_context or use_anilist)


    if episode_ids and (playback_status == "COMPLETED" or (total and current_num >= total)):
        opts.append("Start Rewatch")
        if len(episode_ids) > 1 and isDesktop:
            opts.append("Binge from Start")
    elif episode_ids and prog == 0:
        opts.append("Play")
        if len(episode_ids) > 1 and isDesktop:
            opts.append("Binge")
    elif episode_ids and detail_next_ep is not None:
        opts.append("Play Next")
        if isDesktop:
            opts.append("Binge")
            if len(episode_ids) > 1:
                opts.append("Binge from Start")
    elif episode_ids and detail_next_ep is None and (current_ep_label == str(episode_ids[-1]) or (prog > 0 and prog >= len(episode_ids))):
        opts.append("Replay Latest")
        if len(episode_ids) > 1 and isDesktop:
            opts.append("Binge from Start")

    if episode_ids:
        opts.append("Episodes")
    elif has_provider_link and not s.get("_action_feedback"):
        app_core.set_action_feedback(s, app_core.episode_catalog_error(s))

    opts.append("OP/ED Songs")

    if not getattr(ms, "_is_downloads", False):
        if from_anilist_context:
            if has_provider_link:
                opts.append("Change Match")
            else:
                opts.append("Link Provider")
        elif has_token and sync_enabled and s.get("_id"):
            if has_anilist_link:
                opts.append("Change Match")
            else:
                opts.append("Link AniList")

    opts.append("Progress")
    if show_anilist_actions:
        opts.extend(["Status", "Rate"])

    opts.extend(["Back", "Quit"])

    al_status = str(s.get("_anilist_list") or "").title() or "None"
    al_score = s.get("_anilist_score")
    score_str = f"★ {al_score}" if al_score and float(al_score) > 0 else "not rated"

    local_p = app_core.get_local_progress(s, ttype_local)
    local_p_str = f"local EP {local_p}" if local_p is not None else "local EP 0"
    if show_anilist_actions:
        prog_hint = f"{local_p_str} · AL EP {al_prog}"
    else:
        prog_hint = local_p_str

    def clean_ep_label(ep_id, fallback_idx=None):
        if not ep_id:

            return ""
        labels = s.get("_episode_labels", {})
        from media_cli.domain.episodes import clean_episode_identifier
        if str(ep_id) in labels:
            return clean_episode_identifier(labels[str(ep_id)]) or clean_episode_identifier(str(ep_id)) or str(ep_id)
        cleaned = clean_episode_identifier(str(ep_id))
        if cleaned:
            return cleaned
        if fallback_idx is not None:
            return str(fallback_idx + 1)
        return str(ep_id)

    play_label = clean_ep_label(detail_play_ep, 0)
    next_label = clean_ep_label(detail_next_ep, (_current_idx + 1) if _current_idx is not None else None)
    replay_label = clean_ep_label(current_ep_label, len(episode_ids) - 1) if current_ep_label else (clean_ep_label(episode_ids[-1]) if episode_ids else "1")
    binge_label = next_label if 'Play Next' in opts else play_label

    hints = {
        "Play": f"EP {play_label}",
        "Play Next": f"EP {next_label} \u2022 {gap_str}" if gap_str else f"EP {next_label}",
        "Replay Latest": f"EP {replay_label}",
        "Binge": f"from EP {binge_label}",
        "Binge from Start": f"from EP {play_label}",
        "Start Rewatch": f"replay from EP {play_label}",
        "Episodes": "browse all",
        "OP/ED Songs": "openings & endings for this title",
        "Change Match": ("link a different streaming title / provider" if from_anilist_context else "link a different tracking title"),
        "Link Provider": "link a streaming provider to watch",
        "Link AniList": "link a tracking title",
        "Progress": prog_hint,
        "Status": al_status,
        "Rate": score_str,
        "Back": "return to previous screen",
        "Quit": "exit program"
    }

    _hdr_cache = {}

    def _details_hdr(si):
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80

        feedback = app_core.get_active_feedback(s)
        cache_key = (w, feedback, ttype_local)
        if cache_key in _hdr_cache:
            return _hdr_cache[cache_key]

        parts = []
        app_core.build_info_panel(s, ttype_local, w, parts, local_only=getattr(ms, "_is_downloads", False))
        direct_single = (
            ui.search_prev_state in ("SEARCH", "ANILIST_SEARCH")
            and len(ms.shows) <= 1
        )
        nav_text = "Esc=search" if direct_single else "Esc=back"
        if feedback:
            from ..ui.footer import Footer, FooterStatus, StatusKind
            from ..core.terminal import sanitize_terminal_text
            full_nav_text = Footer(statuses=(FooterStatus(sanitize_terminal_text(feedback), StatusKind.WARNING),),
                                   include_cover_status=False)
        else:
            full_nav_text = context_footer(
                s,
                ui,
                ms,
                f"Tab=Sub/Dub • Enter=select • ?=Help • {nav_text}",
            )
            if has_gaps:
                full_nav_text = f"{len(episode_ids)} listed • {full_nav_text}"
        parts.append(app_core._poster_footer_line(s, full_nav_text, w))
        res = "\n".join(parts)
        _hdr_cache[cache_key] = res
        return res

    def _details_tab_fn(opt=None, direction=1):
        nonlocal ttype_local, episode_ids
        _hdr_cache.clear()
        target_ttype = "dub" if ttype_local == "sub" else "sub"
        allowed, reason = app_core.check_translation_switch_capability(s, ttype_local, target_ttype)
        if not allowed:
            if reason:
                app_core.set_action_feedback(s, reason)
            return (opts, _details_hdr(0))

        new_ids = app_core.with_loading(
            f"Switching to {target_ttype.upper()}…",
            app_core.ensure_episode_ids,
            s,
            target_ttype,
        )
        if new_ids:
            ttype_local = target_ttype
            ui.ui_ttype_ctx = ttype_local
            episode_ids = new_ids
            ms.total_eps = len(episode_ids)
            app_core.set_action_feedback(s, f"Switched to {ttype_local.title()}.")
        else:
            p_name = (s.get("_provider_name") or (s.get("_provider") or "").title() or "this provider") if s else "this provider"
            app_core.set_action_feedback(s, f"{target_ttype.upper()} unavailable on {p_name}")
        return (opts, _details_hdr(0))

    direct_single = (
        ui.search_prev_state in ("SEARCH", "ANILIST_SEARCH")
        and len(ms.shows) <= 1
    )

    hd5 = picker_help(
        "Select option",
        "New search" if direct_single else "Go back",
        "New search" if direct_single else "Go back",
        tab_label="Toggle Sub/Dub",
    )

    if s.get("_action_feedback") and _just_entered:
        ms.just_picked_anime = False
        s["_action_feedback_time"] = time.time()

    idx = tui_pick(
        flags, ui,
        "Anime Details", opts,
        header_fn=_details_hdr,
        tab_fn=_details_tab_fn,
        hints=hints,
        info_fn=app_core.make_single_show_info_fn(s, ui),
        help_dict=hd5
    )

    if idx in (-2, -3):
        previous = ui.search_prev_state
        if direct_single:
            ms.query_str = ""
            return "ANILIST_SEARCH" if previous == "ANILIST_SEARCH" else "SEARCH"
        if should_clear_query_on_child_left(previous, direct_single):
            ms.query_str = ""
        return previous

    if idx >= 0:
        opt = opts[idx]
        if opt in ("Play", "Play Next", "Start Rewatch", "Replay Latest", "Binge", "Binge from Start"):
            if opt in ("Binge", "Binge from Start"):
                args.binge = True
            else:
                args.binge = False
            ui.action_prev_state = "DETAILS"

            if opt == "Play Next":
                if _current_idx is not None and _current_idx + 1 < len(episode_ids):
                    ms.current_ep_index = _current_idx + 1
                else:
                    ms.current_ep_index = min(max(int(prog), 0), max(0, ms.total_eps - 1))
                ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
            elif opt == "Replay Latest":
                ms.current_ep_index = max(0, len(episode_ids) - 1)
                ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
            elif opt == "Start Rewatch":
                if use_anilist:
                    reset_result = app_core.with_loading(
                        "Resetting progress…",
                        app_core._push_local_progress,
                        s, ttype_local, cfg.get("anilist_token"), 0
                    )
                    if reset_result.get("action") == "failed":
                        app_core.err("Could not start AniList rewatch.")
                        return "DETAILS"
                else:
                    app_core.write_history_progress(s, 0, ttype_local, touch=False)
                ms.current_ep_index = 0
                ms.current_ep = episode_id_at(episode_ids, 0)
            elif opt in ("Play", "Binge from Start"):
                ms.current_ep_index = 0
                ms.current_ep = episode_id_at(episode_ids, 0)
            elif opt == "Binge":
                if detail_next_ep is not None and _current_idx is not None and _current_idx + 1 < len(episode_ids):
                    ms.current_ep_index = _current_idx + 1
                else:
                    ms.current_ep_index = 0
                ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
            else:
                ms.current_ep_index = 0
                ms.current_ep = episode_id_at(episode_ids, 0)

            ms.selected_stream = None
            app_core._clear_streams()
            return "PLAY"


        elif opt == "Episodes":
            ui.ep_prev_state = "DETAILS"
            return "EPISODE"

        elif opt == "OP/ED Songs":
            from .animethemes import open_anime_songs
            return open_anime_songs(flags, ui, ms, cfg, args, s, ttype_local)

        elif opt in ("Change Match", "Change AllAnime Match", "Link Provider"):
            if not from_anilist_context:
                matched = app_core._run_anilist_match_search(flags, ui, s, cfg["anilist_token"])
                if matched:
                    app_core.set_title_sync(s, True)
                    app_core.prepare_show_display_state(s, ttype_local, True)
                    ms.show_title = get_show_display_title(s, sync_enabled=True)
                return "DETAILS"
            al_id = app_core.get_show_anilist_id(s) or str(s.get("_id") or s.get("id") or "")
            al_show = {
                "_id": al_id,
                "name": s.get("_display_name") or s.get("name", ""),
                "englishName": s.get("_display_english_name") or s.get("englishName", ""),
                "_anilist_list": s.get("_anilist_list"),
                "_anilist_progress": s.get("_anilist_progress"),
                "_next_airing_ep": s.get("_next_airing_ep"),
                "_next_airing_time": s.get("_next_airing_time"),
                "_next_airing_at": s.get("_next_airing_at"),
                "thumbnail": s.get("thumbnail")
            }
            target_pid = s.get("_provider") or getattr(args, "provider", None) or (cfg or {}).get("provider")
            new_match = app_core._run_manual_match_search(flags, ui, al_show, ttype_local, provider_id=target_pid, allow_provider_change=True)
            if new_match:
                new_match["_has_provider_link"] = True
                new_match["_anilist_media_synced"] = True
                new_match["_anilist_context"] = from_anilist_context
                app_core.set_title_sync(new_match, use_anilist)
                app_core.prepare_show_display_state(new_match, ttype_local, use_anilist)
                ui.ui_show_ctx = new_match
                ui.ui_ttype_ctx = ttype_local
                ms.shows = [new_match]

                ms.show_id = new_match.get("_id")
                ms.show_title = get_show_display_title(new_match)
                ms.total_eps = new_match.get("availableEpisodes", {}).get(ttype_local, 0)

                episode_ids = app_core.with_loading("Linking title…", app_core.ensure_episode_ids, new_match, ttype_local)
                ms.total_eps = len(episode_ids) or ms.total_eps
                ms.current_ep_index = 0
                ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
            return "DETAILS"

        elif opt == "Link AniList":
            matched = app_core._run_anilist_match_search(flags, ui, s, cfg["anilist_token"])
            if matched:
                app_core.set_title_sync(s, True)
                app_core.prepare_show_display_state(s, ttype_local, True)
                ms.show_title = get_show_display_title(s, sync_enabled=True)
            return "DETAILS"

        elif opt == "Progress":
            return "UPDATE_PROGRESS"

        elif opt == "Status":
            return "UPDATE_STATUS"

        elif opt == "Rate":
            return "UPDATE_SCORE"

        elif opt == "Back":
            previous = ui.search_prev_state
            if direct_single:
                ms.query_str = ""
            return previous

        elif opt == "Quit":
            return "QUIT"

    return "DETAILS"
