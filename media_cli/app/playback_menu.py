"""Action menu, mirror picker, and browser opener handlers for playback."""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from typing import Any

from .. import app_core
from ..context import CliFlags, MachineState, UiState
from ..core import streams
from ..core.terminal import truncate_display as _truncate_display
from ..domain.episodes import (
    anilist_progress_target_for_episode,
    clean_episode_identifier,
    episode_id_at,
    episode_index_for_id,
    episode_progress_number,
    highest_episode_number,
    parse_episode_dual_numbers,
    resolve_dual_episode_label,
)
from ..domain.titles import get_show_display_title
from ..media.urls import open_external_url
from ..playback.rules import marked_watched_osd
from ..ui.help import picker_help
from ..ui.picker import tui_pick
from . import playback as playback_mod
from .footer import context_footer

_torrent_mirror_augmenting: set[tuple] = set()
_torrent_mirror_augment_lock = threading.RLock()


def _load_provider_episode_data_for_mirrors(show_id, episode_id, ttype, provider_id):
    return app_core.get_episode_data(
        show_id,
        episode_id,
        ttype,
        provider_id=provider_id,
    )


def _load_episode_data_for_mirrors(show_id, episode_id, ttype, provider_id, show_ctx):
    ep_data = _load_provider_episode_data_for_mirrors(show_id, episode_id, ttype, provider_id)
    if not app_core.is_termux():
        ep_data = playback_mod._augment_episode_sources_with_torrents(
            ep_data,
            show_ctx,
            episode_id,
            ttype,
        )
    return ep_data


def _start_torrent_mirror_augmentation(cur_key, ep_data, show_ctx, episode_id, ttype, target_pid, quality):
    if app_core.is_termux() or not isinstance(ep_data, dict) or ep_data.get("_torrent_fallback_checked"):
        return None
    with _torrent_mirror_augment_lock:
        if cur_key in _torrent_mirror_augmenting:
            return None
        _torrent_mirror_augmenting.add(cur_key)

    def worker():
        try:
            augmented = playback_mod._augment_episode_sources_with_torrents(
                ep_data,
                show_ctx,
                episode_id,
                ttype,
            )
            if augmented and augmented is not ep_data:
                app_core._set_cached_ep_data(augmented, cur_key)
                streams.start_bg_resolve(
                    augmented,
                    set(),
                    cur_key[0],
                    cur_key[1],
                    cur_key[2],
                    target_pid,
                    quality,
                )
        finally:
            with _torrent_mirror_augment_lock:
                _torrent_mirror_augmenting.discard(cur_key)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def handle_action_menu_state(
    flags: CliFlags,
    ui: UiState,
    ms: MachineState,
    cfg: dict,
    args: Any,
    ttype: str,
    resolve_tracking_fn,
) -> str:
    opts, acts = [], []
    action_show = ui.ui_show_ctx
    resolve_tracking_fn(ui.search_prev_state, args, cfg, action_show)

    episode_ids = app_core.ensure_episode_ids(action_show, ttype)

    if not episode_ids:
        err_msg = app_core.episode_catalog_error(action_show)
        app_core.set_action_feedback(action_show, err_msg)
        ui.search_error = err_msg
        return ui.action_prev_state or ui.search_prev_state or "SEARCH"

    ms.total_eps = len(episode_ids) or ms.total_eps
    ms.current_ep_index = episode_index_for_id(
        episode_ids, ms.current_ep, labels=ui.ui_show_ctx.get("_episode_labels")
    )

    if ms.current_ep_index is None:
        app_core.err(f"EP {ms.current_ep} is not present in the provider catalog.")
        ui.ep_prev_state = "DETAILS"
        return "EPISODE"

    next_ep = (
        episode_id_at(episode_ids, ms.current_ep_index + 1)
        if ms.current_ep_index + 1 < ms.total_eps
        else None
    )
    prev_ep = (
        episode_id_at(episode_ids, ms.current_ep_index - 1)
        if ms.current_ep_index > 0
        else None
    )
    current_ep_label = playback_mod._display_episode_label(
        action_show, ms.current_ep, ttype
    )
    next_ep_label = (
        playback_mod._display_episode_label(action_show, next_ep, ttype)
        if next_ep is not None
        else ""
    )
    prev_ep_label = (
        playback_mod._display_episode_label(action_show, prev_ep, ttype)
        if prev_ep is not None
        else ""
    )

    def _resume_show_id():
        return (
            ms.show_id
            or action_show.get("_id")
            or action_show.get("id")
            or action_show.get("_folder_name")
            or ms.show_title
        )

    def _current_resume_time():
        show_key = _resume_show_id()
        if not show_key:
            return 0
        return (
            app_core.get_resume_time(show_key, ms.current_ep)
            or app_core.get_resume_time(show_key, current_ep_label)
            or 0
        )

    al_id = app_core.get_show_anilist_id(action_show)
    has_token = bool(cfg.get("anilist_token")) and not flags.incognito_mode
    has_anilist_link = bool(al_id)
    from_anilist_context = ui.search_prev_state in (
        "ANILIST_BROWSE",
        "ANILIST_SEARCH",
        "ANILIST_AIRING",
    ) or bool(action_show.get("_anilist_context"))
    use_anilist = bool(
        resolve_tracking_fn(ui.search_prev_state, args, cfg, action_show)
        and (al_id or action_show.get("_id"))
    )
    show_anilist_actions = (
        has_token and has_anilist_link and (from_anilist_context or use_anilist)
    )
    sync_enabled = bool(
        (getattr(args, "sync", False) or cfg.get("sync") or cfg.get("auto_track"))
        and not getattr(args, "no_sync", False)
        and not flags.incognito_mode
    )

    al_prog = int(action_show.get("_anilist_progress") or 0)
    if getattr(ms, "_is_downloads", False):
        local_p = action_show.get("_local_progress")
        if local_p is None:
            local_p = app_core.get_local_progress(action_show, ttype)
    else:
        local_p = app_core.get_local_progress(action_show, ttype)
        if local_p is None:
            local_p = action_show.get("_local_progress")
    local_lbl = action_show.get("_local_episode_label") or local_p
    resolved_lbl = resolve_dual_episode_label(action_show, local_lbl or local_p)
    l_prim, l_sec = parse_episode_dual_numbers(resolved_lbl)

    try:
        local_prog_val = int(l_prim or clean_episode_identifier(str(local_p or 0)))
    except (ValueError, TypeError):
        local_prog_val = 0

    eff_prog = (
        al_prog
        if (from_anilist_context or use_anilist) and al_prog > 0
        else local_prog_val
    )

    total_count = action_show.get("episodeCount")
    try:
        total_count = int(total_count) if total_count is not None else 0
    except ValueError:
        total_count = 0

    highest_ep = highest_episode_number(
        episode_ids, labels=ui.ui_show_ctx.get("_episode_labels")
    )
    try:
        highest_ep_num = int(highest_ep)
    except (TypeError, ValueError):
        highest_ep_num = ms.total_eps

    status_str = str(action_show.get("status") or "").upper()
    is_finished = status_str in ("FINISHED", "COMPLETED", "ENDED")
    available_completion_threshold = max(
        value for value in (ms.total_eps, highest_ep_num) if value and value > 0
    ) if (ms.total_eps > 0 or highest_ep_num > 0) else 0
    all_available_watched = bool(
        available_completion_threshold > 0
        and eff_prog >= available_completion_threshold
    )

    playback_status = (
        str(action_show.get("_anilist_list", "")).upper() if use_anilist else ""
    )
    is_completed = bool(
        all_available_watched
        and (
            playback_status == "COMPLETED"
            or is_finished
            or (total_count > 0 and eff_prog >= total_count and is_finished)
        )
    )

    def _refresh_progress_state():
        nonlocal al_prog, local_p, local_lbl, resolved_lbl, l_prim, l_sec, local_prog_val, eff_prog, all_available_watched, is_completed
        al_prog = int(action_show.get("_anilist_progress") or 0)
        if getattr(ms, "_is_downloads", False):
            local_p = action_show.get("_local_progress")
            if local_p is None:
                local_p = app_core.get_local_progress(action_show, ttype)
        else:
            local_p = app_core.get_local_progress(action_show, ttype)
            if local_p is None:
                local_p = action_show.get("_local_progress")
        local_lbl = action_show.get("_local_episode_label") or local_p
        resolved_lbl = resolve_dual_episode_label(action_show, local_lbl or local_p)
        l_prim, l_sec = parse_episode_dual_numbers(resolved_lbl)

        try:
            local_prog_val = int(l_prim or clean_episode_identifier(str(local_p or 0)))
        except (ValueError, TypeError):
            local_prog_val = 0

        eff_prog = (
            al_prog
            if (from_anilist_context or use_anilist) and al_prog > 0
            else local_prog_val
        )

        all_available_watched = bool(
            available_completion_threshold > 0
            and eff_prog >= available_completion_threshold
        )

        pb_status = (
            str(action_show.get("_anilist_list", "")).upper() if use_anilist else ""
        )
        is_completed = bool(
            all_available_watched
            and (
                pb_status == "COMPLETED"
                or is_finished
                or (total_count > 0 and eff_prog >= total_count and is_finished)
            )
        )

    def _check_watched():
        _refresh_progress_state()
        if getattr(ms, "_is_downloads", False):
            watched_eps = action_show.get("watched_episodes", [])
            if str(ms.current_ep) in watched_eps:
                return True
            from ..domain.episodes import (
                clean_episode_identifier,
                parse_episode_dual_numbers,
            )

            prim, sec = parse_episode_dual_numbers(str(current_ep_label))
            clean = (
                prim
                or clean_episode_identifier(str(current_ep_label))
                or str(current_ep_label)
            ).lstrip("0") or "0"
            for w in watched_eps:
                wp, ws = parse_episode_dual_numbers(str(w))
                wc = (wp or clean_episode_identifier(str(w)) or str(w)).lstrip(
                    "0"
                ) or "0"
                if wc == clean or (sec and sec == ws):
                    return True

        import decimal

        try:
            cur_idx = ms.current_ep_index if ms.current_ep_index is not None else 0
            if l_sec:
                return cur_idx < eff_prog
            current_ep_num = decimal.Decimal(
                str(episode_progress_number(ms.current_ep, cur_idx + 1))
            )
            if (
                from_anilist_context
                or use_anilist
                or action_show.get("_progress_authority") == "AL"
            ):
                if al_prog <= 0:
                    return False
                return current_ep_num <= al_prog
            else:
                if local_p is None:
                    return False
                if local_prog_val == 0:
                    return str(local_p) == "0" and current_ep_num == 0
                return current_ep_num <= local_prog_val
        except (decimal.InvalidOperation, ValueError, TypeError):
            return False

    is_watched = _check_watched()

    anilist_progress_target_for_episode(ms.current_ep, fallback=None)

    def _build_menu():
        nonlocal opts, acts, action_hints, next_ep, prev_ep, current_ep_label, next_ep_label, prev_ep_label, is_watched
        opts = []
        acts = []
        action_hints = {}
        _refresh_progress_state()
        is_watched = _check_watched()

        if (
            getattr(ms, "_android_pending_show_id", None)
            and ms._android_pending_show_id != ms.show_id
        ):
            ms._android_pending_watched_ep = None
            ms._android_pending_watched_idx = None
            ms._android_pending_show_id = None

        pending_watched_ep = getattr(ms, "_android_pending_watched_ep", None)

        resume_time = _current_resume_time()

        # Primary Action (Continue vs Start Rewatch vs Play vs Play Next vs Replay)
        if is_completed:
            opts.append("Start Rewatch")
            acts.append("REWATCH")
            action_hints["Start Rewatch"] = "play EP 1 from start"
        elif resume_time > 0 and not is_watched:
            if resume_time > 35:
                effective_resume = max(0, resume_time - 30)
            elif resume_time > 10:
                effective_resume = max(0, resume_time - 10)
            else:
                effective_resume = 0
            opts.append("Continue")
            acts.append("CONTINUE")
            action_hints["Continue"] = (
                f"resume {playback_mod._fmt_ep(current_ep_label)} from {app_core.format_video_time(effective_resume)}"
            )
        elif pending_watched_ep is not None and str(pending_watched_ep) == str(
            ms.current_ep
        ):
            opts.append("Replay")
            acts.append("REPLAY")
            action_hints["Replay"] = (
                f"play {playback_mod._fmt_ep(current_ep_label)} from start"
            )
        elif is_watched:
            if next_ep is not None and not is_completed:
                opts.append("Play Next")
                acts.append("NEXT")
                action_hints["Play Next"] = f"play {playback_mod._fmt_ep(next_ep_label)}"
            else:
                opts.append("Replay")
                acts.append("REPLAY")
                action_hints["Replay"] = (
                    f"play {playback_mod._fmt_ep(current_ep_label)} from start"
                )
        elif eff_prog > 0 or pending_watched_ep is not None:
            opts.append("Play Next")
            acts.append("PLAY_CURRENT")
            action_hints["Play Next"] = f"play {playback_mod._fmt_ep(current_ep_label)}"
        else:
            opts.append("Play")
            acts.append("PLAY_CURRENT")
            action_hints["Play"] = f"play {playback_mod._fmt_ep(current_ep_label)}"

        # Replay (if not primary action and not completed)
        if "Replay" not in opts and not is_completed:
            if pending_watched_ep is not None and str(pending_watched_ep) != str(
                ms.current_ep
            ):
                opts.append("Replay")
                acts.append("REPLAY")
                pending_lbl = playback_mod._display_episode_label(
                    action_show, pending_watched_ep, ttype
                )
                action_hints["Replay"] = (
                    f"play {playback_mod._fmt_ep(pending_lbl)} from start"
                )
            elif resume_time > 0 or is_watched:
                opts.append("Replay")
                acts.append("REPLAY")
                action_hints["Replay"] = (
                    f"play {playback_mod._fmt_ep(current_ep_label)} from start"
                    if is_watched
                    else "from start"
                )

        # Previous (if prev_ep exists)
        if (
            prev_ep is not None
            and not is_completed
            and (pending_watched_ep is None or str(prev_ep) != str(pending_watched_ep))
        ):
            opts.append("Previous")
            acts.append("PREV")
            action_hints["Previous"] = f"play {playback_mod._fmt_ep(prev_ep_label)}"

        # Next (if next_ep exists and Play Next not already in opts)
        if (
            next_ep is not None
            and not is_completed
            and "Play Next" not in opts
            and "Next" not in opts
        ):
            opts.append("Next")
            acts.append("NEXT")
            action_hints["Next"] = f"play {playback_mod._fmt_ep(next_ep_label)}"

        # Binge
        if ms.total_eps > 1:
            opts.append("Binge")
            acts.append("BINGE")
            action_hints["Binge"] = "continuous play"

        # Mark Watched / Mark Unwatched
        if not is_watched and not is_completed:
            opts.append("Mark Watched")
            acts.append("TRACK_ONLY")
            action_hints["Mark Watched"] = "save progress"
        else:
            opts.append("Mark Unwatched")
            acts.append("UNTRACK")
            action_hints["Mark Unwatched"] = "clear progress"

        # Progress
        opts.append("Progress")
        acts.append("PROGRESS")
        local_ep_hint = (
            playback_mod._fmt_ep(resolved_lbl)
            if resolved_lbl
            else f"EP {local_prog_val}"
        )
        if show_anilist_actions:
            action_hints["Progress"] = f"local {local_ep_hint} · AL EP {al_prog}"
        else:
            action_hints["Progress"] = f"local {local_ep_hint}"

        # Status & Rate (AniList)
        if show_anilist_actions:
            al_status = str(action_show.get("_anilist_list") or "").title() or "None"
            opts.append("Status")
            acts.append("STATUS")
            action_hints["Status"] = al_status

            al_score = action_show.get("_anilist_score")
            score_str = (
                f"★ {al_score}" if al_score and float(al_score) > 0 else "not rated"
            )
            opts.append("Rate")
            acts.append("RATE")
            action_hints["Rate"] = score_str

        # Change Match / Link Provider / Change Provider / Link AniList
        if not getattr(ms, "_is_downloads", False):
            has_provider_link = bool(
                (action_show.get("_provider") or action_show.get("provider"))
                and action_show.get("_has_provider_link") is not False
            )
            if from_anilist_context:
                opts.append("Change Match" if has_provider_link else "Link Provider")
                acts.append("CHANGE_MATCH")
                action_hints[opts[-1]] = "link different streaming title"
            elif (
                getattr(ui, "action_prev_state", "") == "HISTORY"
                or getattr(ui, "search_prev_state", "") == "HISTORY"
            ) and has_provider_link:
                opts.append("Change Provider")
                acts.append("CHANGE_PROVIDER")
                action_hints["Change Provider"] = "switch streaming source"
                if (
                    has_token
                    and sync_enabled
                    and action_show.get("_id")
                    and not has_anilist_link
                ):
                    opts.append("Link AniList")
                    acts.append("LINK_ANILIST")
                    action_hints["Link AniList"] = "link tracking title"
            elif has_token and sync_enabled and action_show.get("_id"):
                opts.append("Change Match" if has_anilist_link else "Link AniList")
                acts.append("CHANGE_MATCH" if has_anilist_link else "LINK_ANILIST")
                action_hints[opts[-1]] = "link tracking title"

        # Episodes (if ms.total_eps > 1)
        if ms.total_eps > 1:
            opts.append("Episodes")
            acts.append("EPISODES")
            first_lbl = playback_mod._display_episode_label(
                action_show, episode_ids[0], ttype
            )
            last_lbl = playback_mod._display_episode_label(
                action_show, episode_ids[-1], ttype
            )
            action_hints["Episodes"] = (
                f"browse ({first_lbl}–{last_lbl})"
                if (first_lbl and last_lbl)
                else f"browse (1–{ms.total_eps})"
            )

        opts.append("Anime Songs")
        acts.append("ANIME_SONGS")
        action_hints["Anime Songs"] = "openings & endings from AnimeThemes"

        # Mirrors (if not downloads)
        if not getattr(ms, "_is_downloads", False):
            opts.append("Mirrors")
            acts.append("MIRRORS")
            action_hints["Mirrors"] = "source & quality"

            # Download
            opts.append("Download")
            acts.append("DOWNLOAD_MENU")
            action_hints["Download"] = "download options & batching"

        opts.append("Playback")
        acts.append("PLAYBACK_SETTINGS")
        action_hints["Playback"] = "session playback options"

    action_hints = {}
    _build_menu()

    _last_menu_resume = _current_resume_time()
    _last_menu_watched = is_watched
    _last_menu_al_prog = action_show.get("_anilist_progress")
    _last_menu_local_prog = action_show.get("_local_progress")

    _hdr_cache = {}

    def _action_hdr(si):
        C_K = "\033[38;5;244m"
        R = "\033[0m"
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80

        feedback = app_core.get_active_feedback(action_show)
        cache_key = (w, feedback, ttype)
        if cache_key in _hdr_cache:
            return _hdr_cache[cache_key]

        parts = []
        if action_show:
            app_core.build_info_panel(
                action_show,
                ttype,
                w,
                parts,
                local_only=getattr(ms, "_is_downloads", False),
            )

        def _t(s):
            return _truncate_display(s, max(1, w - 1))

        if feedback:
            parts.append(f"\033[38;5;222m{_t(feedback)}{R}")
        else:
            footer = context_footer(
                action_show,
                ui,
                ms,
                "Enter/Right=select • Tab=Sub/Dub • ?=Help • Left/Esc=back",
                offline=app_core.is_confirmed_offline(),
            )
            parts.append(f"{C_K}{_t(footer)}{R}")

        res = "\n".join(parts)
        _hdr_cache[cache_key] = res
        return res

    def _action_tab_fn(opt=None, direction=1):
        nonlocal ttype, episode_ids, next_ep, prev_ep, current_ep_label, next_ep_label, prev_ep_label
        _hdr_cache.clear()
        target_ttype = "dub" if ttype == "sub" else "sub"
        allowed, reason = app_core.check_translation_switch_capability(
            action_show, ttype, target_ttype
        )
        if not allowed:
            if reason:
                app_core.set_action_feedback(action_show, reason)
            return (opts, _action_hdr(0))

        new_ids = app_core.with_loading(
            f"Switching to {target_ttype.upper()}…",
            app_core.ensure_episode_ids,
            action_show,
            target_ttype,
            status_msg=f"Switching to {target_ttype.upper()}…",
        )
        if new_ids:
            ttype = target_ttype
            ui.ui_ttype_ctx = ttype
            episode_ids = new_ids
            ms.total_eps = len(episode_ids)
            ms.selected_stream = None
            app_core.prepare_show_display_state(action_show, ttype)
            ms.current_ep_index = episode_index_for_id(
                episode_ids, ms.current_ep, labels=ui.ui_show_ctx.get("_episode_labels")
            )
            next_ep = (
                episode_id_at(episode_ids, ms.current_ep_index + 1)
                if ms.current_ep_index is not None
                and ms.current_ep_index + 1 < ms.total_eps
                else None
            )
            prev_ep = (
                episode_id_at(episode_ids, ms.current_ep_index - 1)
                if ms.current_ep_index is not None and ms.current_ep_index > 0
                else None
            )
            current_ep_label = playback_mod._display_episode_label(
                action_show, ms.current_ep, ttype
            )
            next_ep_label = (
                playback_mod._display_episode_label(action_show, next_ep, ttype)
                if next_ep is not None
                else ""
            )
            prev_ep_label = (
                playback_mod._display_episode_label(action_show, prev_ep, ttype)
                if prev_ep is not None
                else ""
            )
            _check_watched()
            _build_menu()

        else:
            p_name = (
                (
                    action_show.get("_provider_name")
                    or (action_show.get("_provider") or "").title()
                    or "this provider"
                )
                if action_show
                else "this provider"
            )
            app_core.set_action_feedback(
                action_show, f"{target_ttype.upper()} unavailable on {p_name}"
            )
        return (opts, _action_hdr(0))

    def _action_hints_fn(opt_idx):
        if opt_idx < 0 or opt_idx >= len(opts):
            return ""
        opt_name = opts[opt_idx]
        if opt_name == "Continue":
            cur_rt = _current_resume_time()
            if cur_rt > 35:
                eff_r = max(0, cur_rt - 30)
            elif cur_rt > 10:
                eff_r = max(0, cur_rt - 10)
            else:
                eff_r = 0
            return f"resume {playback_mod._fmt_ep(current_ep_label)} from {app_core.format_video_time(eff_r)}"
        return action_hints.get(opt_name, "")

    def _action_live_fn(q):
        nonlocal _last_menu_resume, _last_menu_watched, _last_menu_al_prog, _last_menu_local_prog
        cur_rt = _current_resume_time()
        cur_al_p = action_show.get("_anilist_progress")
        cur_loc_p = action_show.get("_local_progress")
        _refresh_progress_state()
        cur_watched = _check_watched()

        resume_bucket_changed = (
            (_last_menu_resume is not None)
            and ((_last_menu_resume > 0 and cur_rt == 0) or (_last_menu_resume == 0 and cur_rt > 0))
        )
        state_changed = (
            resume_bucket_changed
            or (_last_menu_watched is not None and cur_watched != _last_menu_watched)
            or (_last_menu_al_prog is not None and cur_al_p != _last_menu_al_prog)
            or (_last_menu_local_prog is not None and cur_loc_p != _last_menu_local_prog)
        )
        if state_changed:
            _last_menu_resume = cur_rt
            _last_menu_watched = cur_watched
            _last_menu_al_prog = cur_al_p
            _last_menu_local_prog = cur_loc_p
            _hdr_cache.clear()
            _build_menu()

        return (opts, _action_hdr(0), True)

    def _playback_settings_menu():
        selected_idx = 0

        def _settings_options():
            from .playback_player import auto_download_mode
            dl_mode = auto_download_mode(cfg, getattr(ms, "auto_download_next", None))
            dl_status = "Adaptive" if dl_mode == "adaptive" else "Off"
            del_status = "ON" if getattr(ms, "auto_delete_watched", False) else "OFF"
            buf = getattr(ms, "auto_delete_buffer", cfg.get("auto_delete_buffer", 1))
            return (
                [
                    f"Auto-Download Next: {dl_status}",
                    f"Auto-Delete Watched: {del_status}",
                    f"Auto-Delete Buffer: {buf} ep" + ("" if buf == 1 else "s"),
                    "Skip Settings",
                ],
                [
                    "TOGGLE_AUTO_DOWNLOAD",
                    "TOGGLE_AUTO_DELETE",
                    "CYCLE_AUTO_DELETE_BUFFER",
                    "SKIP_SETTINGS",
                ],
                {
                    f"Auto-Download Next: {dl_status}": "session only; download next when playback is safe",
                    f"Auto-Delete Watched: {del_status}": "session only; delete older watched downloads",
                    f"Auto-Delete Buffer: {buf} ep" + ("" if buf == 1 else "s"): "session only; keep recent watched downloads",
                    "Skip Settings": "AniSkip and custom opening/ending skips",
                },
            )

        hd = picker_help("Playback", "Back", "Back")
        while True:
            setting_opts, setting_acts, setting_hints = _settings_options()
            sidx = tui_pick(
                flags,
                ui,
                "Playback",
                setting_opts,
                header_fn=_action_hdr,
                hints=setting_hints,
                help_dict=hd,
                info_fn=app_core.make_single_show_info_fn(action_show, ui),
                initial_selected=selected_idx,
            )
            if sidx in (-2, -3) or sidx < 0:
                return "DETAILS"

            selected_idx = sidx
            selected = setting_acts[sidx]
            if selected == "TOGGLE_AUTO_DOWNLOAD":
                from .playback_player import auto_download_mode
                ms.auto_download_next = "off" if auto_download_mode(cfg, getattr(ms, "auto_download_next", None)) == "adaptive" else "adaptive"
                status = "Adaptive" if ms.auto_download_next == "adaptive" else "Off"
                app_core.set_action_feedback(action_show, f"Auto-Download: {status}")
            elif selected == "TOGGLE_AUTO_DELETE":
                ms.auto_delete_watched = not getattr(ms, "auto_delete_watched", False)
                status = "ON" if ms.auto_delete_watched else "OFF"
                app_core.set_action_feedback(action_show, f"Auto-Delete: {status}")
            elif selected == "CYCLE_AUTO_DELETE_BUFFER":
                current = getattr(ms, "auto_delete_buffer", cfg.get("auto_delete_buffer", 1))
                try:
                    current = int(current)
                except (TypeError, ValueError):
                    current = 1
                choices = [0, 1, 2, 3, 5]
                try:
                    next_buf = choices[(choices.index(current) + 1) % len(choices)]
                except ValueError:
                    next_buf = 1
                ms.auto_delete_buffer = next_buf
                app_core.set_action_feedback(action_show, f"Auto-Delete Buffer: {next_buf}")
            elif selected == "SKIP_SETTINGS":
                _skip_settings_menu()

    def _title_keys_for_custom_skips():
        from ..media.custom_skips import stable_title_keys
        return stable_title_keys(action_show, ms.show_id)

    def _session_entries_for_title():
        store = getattr(ms, "custom_skip_sessions", None)
        if store is None:
            ms.custom_skip_sessions = {}
            store = ms.custom_skip_sessions
        for key in _title_keys_for_custom_skips():
            entries = store.get(str(key))
            if isinstance(entries, list):
                return entries
        return []

    def _saved_entries_for_title():
        return app_core.get_custom_skips(action_show, ms.show_id)

    def _custom_skips_disabled():
        disabled = getattr(ms, "custom_skip_disabled_titles", set())
        return any(str(key) in disabled for key in _title_keys_for_custom_skips())

    def _custom_skip_status():
        if _custom_skips_disabled():
            return "OFF"
        if _session_entries_for_title():
            return "Session"
        if _saved_entries_for_title():
            return "Title"
        return "OFF"

    def _skip_settings_menu():
        selected_idx = 0

        def _options():
            aniskip_enabled = getattr(args, "aniskip", None)
            if aniskip_enabled is None:
                aniskip_enabled = cfg.get("aniskip", cfg.get("aniskip_enabled", True))
            auto_skip = getattr(args, "auto_skip", None)
            if auto_skip is None:
                auto_skip = cfg.get("auto_skip", cfg.get("aniskip_auto", True))
            status = _custom_skip_status()
            opts2 = [
                f"AniSkip: {'ON' if aniskip_enabled else 'OFF'}",
                f"Auto-Skip: {'ON' if auto_skip else 'OFF'}",
                f"Custom Skips: {status}",
                "Edit Custom Skips",
                "Clear Session Skips",
                "Clear Saved Title Skips",
            ]
            acts2 = [
                "TOGGLE_ANISKIP",
                "TOGGLE_AUTO_SKIP",
                "TOGGLE_CUSTOM_SKIPS",
                "EDIT_CUSTOM_SKIPS",
                "CLEAR_SESSION_SKIPS",
                "CLEAR_SAVED_SKIPS",
            ]
            hints2 = {
                opts2[0]: "global/configured AniSkip lookup",
                opts2[1]: "auto-skip when skip intervals are available",
                opts2[2]: "custom skips override AniSkip when present",
                "Edit Custom Skips": "open editor for this title/provider skip form",
                "Clear Session Skips": "remove custom skips for this title until exit",
                "Clear Saved Title Skips": "remove persisted custom skips for this title",
            }
            return opts2, acts2, hints2

        hd = picker_help("Skip Settings", "Back", "Back")
        while True:
            opts2, acts2, hints2 = _options()
            sidx = tui_pick(
                flags,
                ui,
                "Skip Settings",
                opts2,
                header_fn=_action_hdr,
                hints=hints2,
                help_dict=hd,
                info_fn=app_core.make_single_show_info_fn(action_show, ui),
                initial_selected=selected_idx,
            )
            if sidx in (-2, -3) or sidx < 0:
                return
            selected_idx = sidx
            act = acts2[sidx]
            if act == "TOGGLE_ANISKIP":
                current = getattr(args, "aniskip", None)
                if current is None:
                    current = cfg.get("aniskip", cfg.get("aniskip_enabled", True))
                args.aniskip = not bool(current)
                app_core.set_action_feedback(action_show, f"AniSkip: {'ON' if args.aniskip else 'OFF'}")
                continue
            if act == "TOGGLE_AUTO_SKIP":
                current = getattr(args, "auto_skip", None)
                if current is None:
                    current = cfg.get("auto_skip", cfg.get("aniskip_auto", True))
                args.auto_skip = not bool(current)
                app_core.set_action_feedback(action_show, f"Auto-Skip: {'ON' if args.auto_skip else 'OFF'}")
                continue
            if act == "TOGGLE_CUSTOM_SKIPS":
                keys = [str(key) for key in _title_keys_for_custom_skips() if str(key)]
                if not keys:
                    keys = [str(ms.show_id or ms.show_title or "")]
                disabled = getattr(ms, "custom_skip_disabled_titles", set())
                has_custom = bool(_session_entries_for_title() or _saved_entries_for_title())
                if _custom_skips_disabled():
                    for key in keys:
                        disabled.discard(key)
                    app_core.set_action_feedback(action_show, "Custom skips enabled.")
                elif has_custom:
                    for key in keys:
                        disabled.add(key)
                    app_core.set_action_feedback(action_show, "Custom skips disabled for this session.")
                else:
                    app_core.set_action_feedback(action_show, "No custom skips yet. Use Edit Custom Skips.")
                ms.custom_skip_disabled_titles = disabled
                continue
            if act == "CLEAR_SESSION_SKIPS":
                for key in _title_keys_for_custom_skips():
                    ms.custom_skip_sessions.pop(str(key), None)
                    ms.custom_skip_disabled_titles.discard(str(key))
                app_core.set_action_feedback(action_show, "Custom session skips cleared.")
                continue
            if act == "CLEAR_SAVED_SKIPS":
                app_core.clear_custom_skips(action_show, ms.show_id)
                for key in _title_keys_for_custom_skips():
                    ms.custom_skip_disabled_titles.discard(str(key))
                app_core.set_action_feedback(action_show, "Saved custom skips cleared.")
                continue
            if act == "EDIT_CUSTOM_SKIPS":
                from ..media import custom_skips
                from ..providers import get_provider_registry

                provider_ids = list(get_provider_registry().keys())
                current_entry = None
                session_entries = _session_entries_for_title()
                if session_entries:
                    current_entry = dict(session_entries[-1], mode="session")
                else:
                    saved_entries = _saved_entries_for_title()
                    if saved_entries:
                        current_entry = dict(saved_entries[-1], mode="title")
                if current_entry is None:
                    current_entry = {
                        "mode": "session",
                        "providers": ["all"],
                        "exclude_providers": ["torrents-csv"],
                        "intervals": [],
                    }
                try:
                    app_core.restore_terminal()
                    spec = custom_skips.edit_skip_form(
                        title=get_show_display_title(action_show) if action_show else ms.show_title,
                        providers=provider_ids,
                        entry=current_entry,
                        config=cfg,
                    )
                except Exception as exc:
                    app_core.set_action_feedback(action_show, f"Custom skips not saved: {exc}")
                    continue
                entry = {
                    "providers": spec["providers"],
                    "exclude_providers": spec["exclude_providers"],
                    "intervals": custom_skips.compact_intervals(spec["intervals"]),
                }
                if not entry["intervals"]:
                    for key in _title_keys_for_custom_skips():
                        ms.custom_skip_sessions.pop(str(key), None)
                        ms.custom_skip_disabled_titles.discard(str(key))
                    app_core.clear_custom_skips(action_show, ms.show_id)
                    app_core.set_action_feedback(action_show, "Custom skips cleared.")
                elif spec["mode"] == "title":
                    for key in _title_keys_for_custom_skips():
                        ms.custom_skip_sessions.pop(str(key), None)
                        ms.custom_skip_disabled_titles.discard(str(key))
                    app_core.set_custom_skips(action_show, ms.show_id, entry)
                    app_core.set_action_feedback(action_show, "Custom skips saved for this title.")
                else:
                    keys = _title_keys_for_custom_skips()
                    if not keys:
                        keys = [str(ms.show_id or ms.show_title or "")]
                    for key in keys:
                        if key:
                            ms.custom_skip_sessions[str(key)] = [entry]
                            ms.custom_skip_disabled_titles.discard(str(key))
                    app_core.set_action_feedback(action_show, "Custom skips saved for this session.")

    hd7 = picker_help("Select action", "Go back", "Go back", tab_label="Toggle Sub/Dub")
    idx = tui_pick(
        flags,
        ui,
        "Select action",
        opts,
        header_fn=_action_hdr,
        live_fn=_action_live_fn,
        tab_fn=_action_tab_fn,
        info_fn=app_core.make_single_show_info_fn(action_show, ui),
        hints=_action_hints_fn,
        help_dict=hd7,
    )

    if idx in (-2, -3):
        return ui.action_prev_state or "DETAILS"

    def _mark_ep_watched(target_ep, target_idx=None):
        if target_idx is None:
            target_idx = episode_index_for_id(
                episode_ids, target_ep, labels=action_show.get("_episode_labels")
            )
        sync_active = bool(use_anilist and cfg.get("anilist_token"))
        if getattr(ms, "_is_downloads", False):
            folder_name = action_show.get("_folder_name", ms.show_title)
            app_core.record_download_watch_progress(
                action_show,
                folder_name,
                target_ep,
                ttype,
                sync_enabled=sync_active,
            )
            app_core.prepare_show_display_state(
                action_show, ttype, False, prefer_watched_episodes=True
            )
            from .playback_player import _auto_delete_watched_downloads
            _auto_delete_watched_downloads(action_show, episode_ids, ttype, cfg, ms)
            if not sync_active:
                target_lbl = playback_mod._display_episode_label(
                    action_show, target_ep, ttype
                )
                app_core.set_action_feedback(
                    action_show, f"✔ Marked {playback_mod._fmt_ep(target_lbl)} watched"
                )
                app_core.save_resume_time(ms.show_id, target_ep, 0)
                return False

        tkn = cfg.get("anilist_token")
        synced = False
        target_idx_val = target_idx if target_idx is not None else 0
        progress_ep = episode_progress_number(target_ep, target_idx_val + 1)
        if tkn and resolve_tracking_fn(ui.search_prev_state, args, cfg, action_show):
            al_id = app_core.get_show_anilist_id(action_show)
            result = app_core.with_loading(
                "Syncing to AniList…",
                app_core.sync_watched_to_anilist,
                tkn,
                ms.show_title,
                progress_ep,
                al_id,
                action_show,
                ttype,
            )
            if result:
                synced = True
                app_core.set_action_feedback(
                    action_show, f"✔ Synced EP {progress_ep} to AniList"
                )
            else:
                app_core.set_action_feedback(action_show, "Sync failed • Saved offline")
        else:
            app_core.with_loading(
                "Saving progress…", app_core.save_history, action_show, target_ep, ttype
            )
            target_lbl = playback_mod._display_episode_label(
                action_show, target_ep, ttype
            )
            app_core.set_action_feedback(
                action_show, f"✔ Marked {playback_mod._fmt_ep(target_lbl)} watched"
            )

        app_core.save_resume_time(ms.show_id, target_ep, 0)
        app_core.prepare_show_display_state(action_show, ttype)
        return synced

    def _execute_track_action():
        pending_ep = getattr(ms, "_android_pending_watched_ep", None)
        pending_idx = getattr(ms, "_android_pending_watched_idx", None)
        ms._android_pending_watched_ep = None
        ms._android_pending_watched_idx = None
        if pending_ep is not None and str(pending_ep) != str(ms.current_ep):
            _mark_ep_watched(pending_ep, pending_idx)
        return _mark_ep_watched(ms.current_ep, ms.current_ep_index)

    def _execute_untrack_action():
        sync_active = bool(use_anilist and cfg.get("anilist_token"))
        if getattr(ms, "_is_downloads", False):
            folder_name = action_show.get("_folder_name", ms.show_title)
            app_core.record_download_unwatch_progress(
                action_show,
                folder_name,
                ms.current_ep,
                ttype,
                sync_enabled=sync_active,
            )
            app_core.prepare_show_display_state(
                action_show, ttype, False, prefer_watched_episodes=True
            )
            if not sync_active:
                app_core.set_action_feedback(
                    action_show,
                    f"✔ Marked {playback_mod._fmt_ep(current_ep_label)} unwatched",
                )
                return False

        prev_num = episode_progress_number(prev_ep, 0) if prev_ep is not None else 0
        tkn = cfg.get("anilist_token")
        if tkn and resolve_tracking_fn(ui.search_prev_state, args, cfg, action_show):
            al_id = app_core.get_show_anilist_id(action_show)
            app_core.with_loading(
                "Syncing to AniList…",
                app_core.sync_watched_to_anilist,
                tkn,
                ms.show_title,
                prev_num,
                al_id,
                action_show,
                ttype,
            )
            app_core.set_action_feedback(
                action_show,
                f"✔ Marked {playback_mod._fmt_ep(current_ep_label)} unwatched",
            )
        else:
            app_core.with_loading(
                "Updating progress…",
                app_core.write_history_progress,
                action_show,
                prev_num,
                ttype,
                touch=False,
            )
            app_core.set_action_feedback(
                action_show,
                f"✔ Marked {playback_mod._fmt_ep(current_ep_label)} unwatched",
            )
        app_core.save_resume_time(ms.show_id, ms.current_ep, 0)
        app_core.prepare_show_display_state(action_show, ttype)
        return True

    a = acts[idx]
    pending_ep = getattr(ms, "_android_pending_watched_ep", None)
    pending_idx = getattr(ms, "_android_pending_watched_idx", None)

    if a in ("CONTINUE", "PLAY", "PLAY_FIRST", "PLAY_CURRENT", "BINGE", "NEXT"):
        if pending_ep is not None:
            ms._android_pending_watched_ep = None
            ms._android_pending_watched_idx = None
            if str(pending_ep) != str(ms.current_ep):
                _mark_ep_watched(pending_ep, pending_idx)

    if a == "CONTINUE":
        ms.selected_stream = None
        app_core._clear_streams()
        playback_mod._clear_episode_source_state(ms)
        return "PLAY"

    elif a in ("PLAY", "PLAY_FIRST", "PLAY_CURRENT"):
        ms.selected_stream = None
        app_core._clear_streams()
        playback_mod._clear_episode_source_state(ms)
        return "PLAY"

    elif a == "REWATCH":
        ms._android_pending_watched_ep = None
        ms._android_pending_watched_idx = None
        ms.current_ep_index = 0
        ms.current_ep = episode_id_at(episode_ids, 0)
        ms.selected_stream = None
        app_core._clear_streams()
        playback_mod._clear_episode_source_state(ms)
        return "PLAY"

    elif a == "TRACK_ONLY":
        _execute_track_action()
        ms.current_ep_index = None
        ms.current_ep = None
        return "DETAILS"

    elif a == "UNTRACK":
        ms._android_pending_watched_ep = None
        ms._android_pending_watched_idx = None
        _execute_untrack_action()
        ms.current_ep_index = None
        ms.current_ep = None
        return "DETAILS"

    elif a == "PROGRESS":
        return "UPDATE_PROGRESS"

    elif a == "TRACK_NEXT":
        synced = _execute_track_action()
        ms.pending_osd_msg = marked_watched_osd(ms.current_ep, synced)
        ms.current_ep_index += 1
        ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
        playback_mod._clear_episode_source_state(ms)
        return "PLAY"

    elif a == "BINGE":
        args.binge = True
        ms.selected_stream = None
        app_core._clear_streams()
        playback_mod._clear_episode_source_state(ms)
        return "PLAY"

    elif a == "NEXT":
        ms.current_ep_index += 1
        ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
        ms.selected_stream = None
        app_core._clear_streams()
        playback_mod._clear_episode_source_state(ms)
        return "PLAY"

    elif a == "PREV":
        ms._android_pending_watched_ep = None
        ms._android_pending_watched_idx = None
        ms.current_ep_index -= 1
        ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
        ms.selected_stream = None
        app_core._clear_streams()
        playback_mod._clear_episode_source_state(ms)
        return "PLAY"

    elif a == "EPISODES":
        ui.ep_prev_state = "DETAILS"
        return "EPISODE"

    elif a == "ANIME_SONGS":
        from .animethemes import open_anime_songs
        return open_anime_songs(flags, ui, ms, cfg, args, action_show, ttype)

    elif a == "REPLAY":
        if pending_ep is not None:
            ms._android_pending_watched_ep = None
            ms._android_pending_watched_idx = None
            if str(pending_ep) != str(ms.current_ep):
                ms.current_ep = pending_ep
                if pending_idx is not None:
                    ms.current_ep_index = pending_idx
                else:
                    ms.current_ep_index = episode_index_for_id(
                        episode_ids,
                        pending_ep,
                        labels=action_show.get("_episode_labels"),
                    )
        playback_mod._clear_episode_source_state(ms)
        app_core.save_resume_time(ms.show_id, ms.current_ep, 0)
        return "PLAY"

    elif a == "MIRRORS":
        return "MIRRORS"

    elif a == "DOWNLOAD_MENU":
        return handle_download_menu_state(flags, ui, ms, cfg, args, ttype)

    elif a == "PLAYBACK_SETTINGS":
        return _playback_settings_menu()

    elif a == "TOGGLE_AUTO_DOWNLOAD":
        from .playback_player import auto_download_mode
        ms.auto_download_next = "off" if auto_download_mode(cfg, getattr(ms, "auto_download_next", None)) == "adaptive" else "adaptive"
        status = "Adaptive" if ms.auto_download_next == "adaptive" else "Off"
        app_core.set_action_feedback(action_show, f"Auto-Download: {status}")
        return "DETAILS"

    elif a == "TOGGLE_AUTO_DELETE":
        ms.auto_delete_watched = not getattr(ms, "auto_delete_watched", False)
        status = "ON" if ms.auto_delete_watched else "OFF"
        app_core.set_action_feedback(action_show, f"Auto-Delete: {status}")
        return "DETAILS"

    elif a == "STATUS":
        return "UPDATE_STATUS"

    elif a == "RATE":
        return "UPDATE_SCORE"

    elif a == "CHANGE_PROVIDER":
        old_id = action_show.get("_id")
        old_progress = action_show.get("_local_progress")
        old_label = action_show.get("_local_episode_label")
        target_pid = (
            action_show.get("_provider")
            or action_show.get("provider")
            or getattr(args, "provider", None)
            or (cfg or {}).get("provider")
        )
        new_match = app_core._run_manual_match_search(
            flags,
            ui,
            action_show,
            ttype,
            provider_id=target_pid,
            allow_provider_change=True,
        )
        if new_match:
            new_match["_has_provider_link"] = True
            new_match["_local_progress"] = old_progress
            new_match["_local_episode_label"] = old_label
            app_core.prepare_show_display_state(new_match, ttype, use_anilist)
            ui.ui_show_ctx = new_match
            ui.ui_ttype_ctx = ttype
            ms.shows = [new_match]
            ms.show_id = new_match.get("_id")
            ms.show_title = get_show_display_title(new_match)
            episode_ids = app_core.with_loading(
                "Linking new provider…", app_core.ensure_episode_ids, new_match, ttype
            )
            ms.total_eps = len(episode_ids) or (
                new_match.get("availableEpisodes", {}).get(ttype, 0)
            )
            if episode_ids:
                target_idx = episode_index_for_id(
                    episode_ids, str(old_progress or old_label or "")
                )
                ms.current_ep_index = target_idx if target_idx is not None else 0
                ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
            if old_id and old_id != ms.show_id:
                app_core.delete_history_entry(old_id, ttype)
                app_core.save_history(
                    new_match, ms.current_ep or old_progress or 0, ttype
                )
                app_core.patch_history_entry_show(ms.show_id, ttype, new_match)
            app_core.set_action_feedback(
                new_match,
                f"✔ Switched provider to {app_core.provider_display_name(new_match.get('_provider'))}",
            )
        return "DETAILS"

    elif a in ("CHANGE_MATCH", "LINK_ANILIST"):
        if not from_anilist_context:
            matched = app_core._run_anilist_match_search(
                flags, ui, action_show, cfg["anilist_token"]
            )
            if matched:
                app_core.set_title_sync(action_show, True)
                app_core.prepare_show_display_state(action_show, ttype, True)
                ms.show_title = get_show_display_title(action_show, sync_enabled=True)
            return "DETAILS"
        al_id_val = app_core.get_show_anilist_id(action_show) or str(
            action_show.get("_id") or action_show.get("id") or ""
        )
        al_show = {
            "_id": al_id_val,
            "name": action_show.get("_display_name") or action_show.get("name", ""),
            "englishName": action_show.get("_display_english_name")
            or action_show.get("englishName", ""),
            "_anilist_list": action_show.get("_anilist_list"),
            "_anilist_progress": action_show.get("_anilist_progress"),
            "_next_airing_ep": action_show.get("_next_airing_ep"),
            "_next_airing_time": action_show.get("_next_airing_time"),
            "_next_airing_at": action_show.get("_next_airing_at"),
            "thumbnail": action_show.get("thumbnail"),
        }
        target_pid = (
            action_show.get("_provider")
            or getattr(args, "provider", None)
            or (cfg or {}).get("provider")
        )
        new_match = app_core._run_manual_match_search(
            flags,
            ui,
            al_show,
            ttype,
            provider_id=target_pid,
            allow_provider_change=True,
        )
        if new_match:
            new_match["_has_provider_link"] = True
            new_match["_anilist_media_synced"] = True
            new_match["_anilist_context"] = from_anilist_context
            app_core.set_title_sync(new_match, use_anilist)
            app_core.prepare_show_display_state(new_match, ttype, use_anilist)
            ui.ui_show_ctx = new_match
            ui.ui_ttype_ctx = ttype
            ms.shows = [new_match]

            ms.show_id = new_match.get("_id")
            ms.show_title = get_show_display_title(new_match)
            ms.total_eps = new_match.get("availableEpisodes", {}).get(ttype, 0)

            episode_ids = app_core.with_loading(
                "Linking title…", app_core.ensure_episode_ids, new_match, ttype
            )
            ms.total_eps = len(episode_ids) or ms.total_eps
            ms.current_ep_index = 0
            ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
        return "DETAILS"

    return "DETAILS"


def handle_browser_play_state(
    flags: CliFlags,
    ui: UiState,
    ms: MachineState,
    cfg: dict,
    args: Any,
    ttype: str,
    resolve_tracking_fn,
) -> str:
    """Open the current episode in an external browser."""
    action_show = ui.ui_show_ctx
    ep_data = app_core.get_episode_data(
        ms.show_id, ms.current_ep, ttype, provider_id=app_core.get_provider_id(action_show)
    )
    if not ep_data:
        app_core.err("No episode data available to open in browser.")
        return "DETAILS"

    ep_info = ep_data.get("episode", {})
    source_urls = ep_info.get("sourceUrls", [])
    if not source_urls:
        app_core.err("No streaming sources available for this episode.")
        return "DETAILS"

    # Use the first available source URL
    first_source = source_urls[0]
    url = first_source.get("link") or first_source.get("streamUrl")
    if not url:
        app_core.err("No playable URL found for this episode.")
        return "DETAILS"

    from ..media.urls import open_external_url
    if open_external_url(url):
        app_core.set_action_feedback(
            action_show, f"Opened {first_source.get('sourceName', 'browser')} in browser"
        )
    else:
        app_core.err(f"Failed to open {url} in browser.")
    return "DETAILS"


def handle_download_menu_state(
    flags: CliFlags,
    ui: UiState,
    ms: MachineState,
    cfg: dict,
    args: Any,
    ttype: str,
) -> str:
    action_show = ui.ui_show_ctx
    episode_ids = app_core.ensure_episode_ids(action_show, ttype)
    if not episode_ids:
        app_core.err(app_core.episode_catalog_error(action_show))
        return "DETAILS"

    current_idx = (
        ms.current_ep_index
        if ms.current_ep_index is not None
        and 0 <= ms.current_ep_index < len(episode_ids)
        else 0
    )
    current_ep_id = episode_ids[current_idx]
    current_label = playback_mod._display_episode_label(
        action_show, current_ep_id, ttype
    )
    total_eps = len(episode_ids)
    first_label = playback_mod._display_episode_label(
        action_show, episode_ids[0], ttype
    )
    last_label = playback_mod._display_episode_label(
        action_show, episode_ids[-1], ttype
    )

    opts = []
    acts = []
    hints = {}

    # 1. Download EP <N>
    opts.append(f"Download EP {current_label}")
    acts.append("SINGLE")
    hints[opts[-1]] = "single episode"

    # 2. Download from EP <N> to End (if not on last episode)
    if current_idx < total_eps - 1:
        opts.append(f"Download from EP {current_label} to End")
        acts.append("TO_END")
        hints[opts[-1]] = f"batch remaining ({total_eps - current_idx} eps)"

    # 3. Download All
    opts.append(f"Download All ({first_label}–{last_label})")
    acts.append("ALL")
    hints[opts[-1]] = f"entire series ({total_eps} eps)"

    # 4. Pick Episodes to Download
    opts.append("Pick Episodes to Download…")
    acts.append("PICK")
    hints[opts[-1]] = "custom multi-select (Space/Tab)"

    _dl_hdr_cache: dict[tuple, str] = {}

    def _dl_hdr(si):
        C_K = "\033[38;5;244m"
        R = "\033[0m"
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80
        cache_key = (w, ttype, getattr(ms, "_is_downloads", False))
        if cache_key in _dl_hdr_cache:
            return _dl_hdr_cache[cache_key]
        parts = []
        if action_show:
            app_core.build_info_panel(
                action_show,
                ttype,
                w,
                parts,
                local_only=getattr(ms, "_is_downloads", False),
            )
        p_name = (
            (
                action_show.get("_provider_name")
                or (action_show.get("_provider") or "").title()
            )
            if action_show
            else ""
        )
        prefix = f"{p_name} • " if p_name else ""
        parts.append(
            f"{C_K}{_truncate_display(prefix + 'Enter=select • Left/Esc=back', max(1, w - 1))}{R}"
        )
        res = "\n".join(parts)
        _dl_hdr_cache[cache_key] = res
        return res

    hd = picker_help("Download Menu", "Go back", "Go back")
    idx = tui_pick(
        flags,
        ui,
        "Download Menu",
        opts,
        header_fn=_dl_hdr,
        hints=hints,
        help_dict=hd,
    )

    if idx in (-2, -3):
        return "DETAILS"

    action = acts[idx]
    queued_eps = []

    if action == "SINGLE":
        queued_eps = [current_ep_id]
    elif action == "TO_END":
        queued_eps = episode_ids[current_idx:]
    elif action == "ALL":
        queued_eps = episode_ids[:]
    elif action == "PICK":
        def _build_batch_dl_label(ep_id, idx):
            raw_lbl = playback_mod._display_episode_label(action_show, ep_id, ttype)
            lbl = f"Episode {raw_lbl}"
            w_list = action_show.get("watched_episodes", [])
            is_w = str(ep_id) in w_list or str(raw_lbl) in w_list
            if not is_w:
                eff = action_show.get("_local_progress") or action_show.get("_anilist_progress")
                try:
                    if eff and float(raw_lbl) <= float(eff):
                        is_w = True
                except (ValueError, TypeError):
                    if eff and idx < float(eff):
                        is_w = True
            if is_w:
                return f"\033[38;5;244m{lbl}\033[0m"
            return lbl

        ep_options = [
            _build_batch_dl_label(ep_id, i)
            for i, ep_id in enumerate(episode_ids)
        ]
        _pick_hdr_cache: dict[tuple, str] = {}

        def _pick_hdr(si):
            C_K = "\033[38;5;244m"
            R = "\033[0m"
            try:
                w = os.get_terminal_size().columns
            except OSError:
                w = 80
            cache_key = (w, ttype, getattr(ms, "_is_downloads", False))
            if cache_key in _pick_hdr_cache:
                return _pick_hdr_cache[cache_key]
            parts = []
            if action_show:
                app_core.build_info_panel(
                    action_show,
                    ttype,
                    w,
                    parts,
                    local_only=getattr(ms, "_is_downloads", False),
                )
            parts.append(
                f"{C_K}{_truncate_display('Space/Tab=Toggle • Enter=Download • Left/Esc=Cancel', max(1, w - 1))}{R}"
            )
            res = "\n".join(parts)
            _pick_hdr_cache[cache_key] = res
            return res

        pick_hd = picker_help("Pick episodes", "Cancel", "Cancel")
        chosen = tui_pick(
            flags,
            ui,
            "Select episodes to download",
            ep_options,
            header_fn=_pick_hdr,
            multi_select=True,
            help_dict=pick_hd,
        )
        if chosen in (-2, -3) or not isinstance(chosen, list) or not chosen:
            return "DETAILS"
        queued_eps = [episode_ids[i] for i in chosen if 0 <= i < len(episode_ids)]

    if not queued_eps:
        return "DETAILS"

    _execute_batch_download(flags, ui, ms, cfg, args, ttype, action_show, queued_eps)
    return "DETAILS"


def _execute_batch_download(flags, ui, ms, cfg, args, ttype, action_show, queued_eps):
    args.download = False
    downloader_choice = getattr(args, "downloader", cfg.get("downloader", "auto"))
    extra_args = getattr(args, "extra_args", [])
    if extra_args and extra_args[0] == "--":
        extra_args = extra_args[1:]
    download_dir = cfg.get("download_dir", "")
    from media_cli.providers import title_provider_key

    provider_id = title_provider_key(action_show)

    if not getattr(args, "no_tui", False) and not getattr(flags, "plain_mode", False) and sys.stdin.isatty() and sys.stdout.isatty():
        from media_cli.media.download_worker import DownloadItem, get_global_worker
        from media_cli.ui.download_manager import run_download_manager
        from media_cli.media.download import sanitize_filename

        safe_title = sanitize_filename(ms.show_title) or ms.show_title
        target_dir = os.path.join(download_dir, safe_title)
        try:
            os.makedirs(target_dir, exist_ok=True)
        except Exception:
            pass

        worker = get_global_worker(download_dir)

        def _make_resolver(show_id, ep_id, ttype, provider_id, quality):
            def _res(item):
                stream_res = app_core.fetch_episode_stream(
                    show_id,
                    ep_id,
                    ttype,
                    quality,
                    provider_id=provider_id,
                    exclude_sources=getattr(item, "failed_mirrors", set()),
                )
                if not stream_res or not stream_res[0]:
                    raise RuntimeError(f"No stream found for episode {ep_id}")
                stream, first_source_name = stream_res[0], stream_res[1]
                raw_url = stream.get("link") or stream.get("streamUrl") or stream.get("sourceUrl") or ""
                if not raw_url:
                    raise RuntimeError("No stream URL in resolved stream")
                item.current_mirror_name = first_source_name or stream.get("source_name") or stream.get("source_parent_name") or "selected mirror"
                item.current_mirror_url = raw_url
                item.logs.append(f"▶ Testing mirror: {item.current_mirror_name}")
                headers = dict(stream.get("headers") or {})
                referer = stream.get("referer", "")
                if referer and "Referer" not in headers:
                    headers["Referer"] = referer
                proxy_srv = None
                if (
                    stream.get("requires_proxy")
                    or "uwucdn.top" in raw_url
                    or "owocdn.top" in raw_url
                    or "kwik." in referer
                    or "megap." in raw_url
                    or "akirax.buzz" in raw_url
                ):
                    try:
                        from media_cli.media.local_proxy import start_local_proxy
                        raw_url, proxy_srv = start_local_proxy(raw_url, headers=headers, referer=referer)
                    except Exception:
                        pass
                item.audio_tracks = list(stream.get("audio_tracks") or [])
                item.audio_url = stream.get("audio_url", "")
                item.subtitles = list(stream.get("subtitles") or stream.get("vtt") or [])
                item.subtitle_url = stream.get("subtitle_url", "")
                return raw_url, headers, proxy_srv
            return _res

        def _on_complete(item):
            try:
                from media_cli.core.storage import collect_downloaded_episode_files, register_downloaded_episode_files
                f_key = sanitize_filename(item.title) or item.title
                ep_str = str(getattr(item, "ep_id", item.episode))
                files = list(getattr(item, "output_files", []) or collect_downloaded_episode_files(item.destination_path))
                register_downloaded_episode_files(
                    f_key,
                    ep_str,
                    files,
                    metadata=getattr(item, "show_metadata", None),
                )
            except Exception:
                pass

        quality = cfg.get("quality", "best")
        ext = ".mkv"

        for ep_id in queued_eps:
            ep_label = playback_mod._display_episode_label(action_show, ep_id, ttype)
            item_id = f"{ms.show_id or safe_title}:{ep_id}"
            dest_path = os.path.join(target_dir, f"{safe_title} - Episode {ep_label}{ext}")
            item = DownloadItem(
                id=item_id,
                title=ms.show_title,
                episode=f"Episode {ep_label}",
                destination_path=dest_path,
                engine=downloader_choice if downloader_choice in ("yt-dlp", "aria2c", "ffmpeg") else "auto",
                resolve_fn=_make_resolver(ms.show_id, ep_id, ttype, provider_id, quality),
                on_complete=_on_complete,
                ep_id=ep_id,
                show_metadata=action_show,
                download_sub_langs=getattr(args, "download_sub_langs", None),
                download_audio_langs=getattr(args, "download_audio_langs", None),
                subtitle_mode=getattr(args, "subtitle_mode", None),
            )
            worker.add_item(item)

        manager_result = run_download_manager(worker, standalone=False)
        if manager_result == "QUIT":
            return "QUIT"
        app_core.set_action_feedback(
            action_show, f"✔ Added {len(queued_eps)} episode(s) to downloader"
        )
        return

    # Non-interactive / no-tui mode: sequential console downloader
    app_core.restore_terminal()
    success_count = 0
    total_to_dl = len(queued_eps)
    print(
        f"\n\033[1;36mDownloading {total_to_dl} episode(s) for '{ms.show_title}' ({ttype.upper()})…\033[0m\n"
    )

    for i, ep_id in enumerate(queued_eps, 1):
        ep_label = playback_mod._display_episode_label(action_show, ep_id, ttype)
        print(
            f"\033[1;97m[{i}/{total_to_dl}] Resolving stream for EP {ep_label}…\033[0m"
        )
        stream_res = app_core.fetch_episode_stream(
            ms.show_id,
            ep_id,
            ttype,
            cfg.get("quality", "best"),
            provider_id=provider_id,
        )
        if not stream_res or not stream_res[0]:
            print(f"\033[31m✖ No stream found for EP {ep_label}\033[0m")
            continue
        stream = stream_res[0]
        dl_ok = app_core.download_episode(
            ms.show_title,
            ep_label,
            stream,
            download_dir,
            downloader=downloader_choice,
            extra_args=extra_args,
            download_sub_langs=getattr(args, "download_sub_langs", None),
            download_audio_langs=getattr(args, "download_audio_langs", None),
            subtitle_mode=getattr(args, "subtitle_mode", None),
        )
        if dl_ok:
            success_count += 1
            try:
                from media_cli.core.storage import (
                    collect_downloaded_episode_files,
                    register_downloaded_episode_files,
                )

                from media_cli.media.download import sanitize_filename
                title = ms.show_title
                folder_key = sanitize_filename(title) or title
                offline_file = app_core.find_offline_file_for_episode(title, ep_label, cfg)
                if offline_file:
                    register_downloaded_episode_files(
                        folder_key,
                        str(ep_id),
                        collect_downloaded_episode_files(offline_file),
                        metadata=action_show,
                    )
            except Exception:
                pass

    if success_count > 0:
        app_core.set_action_feedback(
            action_show, f"✔ Downloaded {success_count}/{total_to_dl} episode(s)"
        )
    else:
        app_core.set_action_feedback(
            action_show, f"✖ Downloads failed ({total_to_dl} eps)"
        )
    time.sleep(1.0)


def handle_mirrors_state(
    flags: CliFlags,
    ui: UiState,
    ms: MachineState,
    cfg: dict,
    args: Any,
    ttype: str,
    resolve_tracking_fn,
) -> str:
    target_pid = (ui.ui_show_ctx or {}).get("_provider") or getattr(
        args, "provider", None
    )
    quality = getattr(args, "quality", None) or cfg.get("quality", "best")
    mirror_ep = ms.current_ep
    cur_key = streams.make_stream_key(
        ms.show_id, mirror_ep, ttype, target_pid, quality
    )

    # Check cached episode data first (non-blocking)
    cached_ep_data = app_core._get_cached_ep_data(cur_key)
    if not cached_ep_data:
        cached_ep_data = app_core.with_loading(
            f"Loading {ttype.upper()} sources…",
            _load_provider_episode_data_for_mirrors,
            ms.show_id,
            mirror_ep,
            ttype,
            provider_id=target_pid,
        )
    if not cached_ep_data:
        cached_ep_data = {"episode": {"sourceUrls": []}}
    if cached_ep_data:
        app_core._set_cached_ep_data(cached_ep_data, cur_key)
        _start_torrent_mirror_augmentation(
            cur_key,
            cached_ep_data,
            ui.ui_show_ctx,
            mirror_ep,
            ttype,
            target_pid,
            quality,
        )
    has_cached_sources = (
        cached_ep_data
        and cached_ep_data.get("episode", {}).get("sourceUrls")
    )

    # Get cached streams with version check (non-blocking)
    cached_streams = app_core._stream_snapshot(
        ms.show_id, mirror_ep, ttype, target_pid, quality, cached_ep_data
    )

    exclude_names = set()
    for s in cached_streams:
        if s.get("source_parent_name"):
            exclude_names.add(s["source_parent_name"])
        if s.get("raw_source_name"):
            exclude_names.add(s["raw_source_name"])
        if s.get("source_name"):
            raw_sname = s["source_name"]
            exclude_names.add(raw_sname)
            base_name = re.sub(
                r"\s*(?:\(\s*)?\b(?:\d+p|\d+k|adaptive|source)\b.*$",
                "",
                raw_sname,
                flags=re.I,
            ).strip()
            if base_name:
                exclude_names.add(base_name)
            exclude_names.add(raw_sname.split(" (")[0].strip())

    with streams._bg_lock:
        bg_alive = bool(streams._bg_thread and streams._bg_thread.is_alive())
        is_same_active_key = streams._active_stream_key == cur_key

    cache_ready = streams.is_stream_cache_ready(
        ms.show_id, mirror_ep, ttype, target_pid, quality
    )
    total_sources = (
        len(cached_ep_data.get("episode", {}).get("sourceUrls", []))
        if cached_ep_data
        else 0
    )
    all_resolved = cache_ready or (
        total_sources > 0 and len(exclude_names) >= total_sources
    )

    if not (bg_alive and is_same_active_key) and not all_resolved:
        streams.start_bg_resolve(
            cached_ep_data,
            exclude_names,
            ms.show_id,
            mirror_ep,
            ttype,
            target_pid,
            quality,
        )

    def _mlabel(s):
        tag = " ✓" if s.get("android_safe") else ""
        pref = app_core.get_preferred_mirror(ms.show_id)
        is_pref = pref.get("source_name") == s["source_name"] and pref.get(
            "resolution"
        ) == s.get("resolution", "?")

        # Use same dedup key as _dedup() for consistency
        def _stream_key(s):
            return (s.get("source_name"), s.get("resolution"), s.get("link"))

        selected_key = _stream_key(ms.selected_stream) if ms.selected_stream else None
        if selected_key and _stream_key(s) == selected_key:
            prefix = "▶ "
        elif is_pref:
            prefix = "★ "
        else:
            prefix = ""

        return playback_mod.format_mirror_label(s, prefix=prefix, safe_tag=tag)

    def _dedup():
        seen, out = set(), []
        live_ep_data = app_core._get_cached_ep_data(cur_key) or cached_ep_data
        active_list = app_core._stream_snapshot(
            ms.show_id, mirror_ep, ttype, target_pid, quality, live_ep_data
        )
        target_quality = getattr(args, "quality", None) or cfg.get("quality", "best")
        from ..media.sources import quality_preference_key

        def _mirror_sort_key(s):
            sname = (s.get("source_name") or "").lower()
            res = s.get("resolution") or sname
            prio = s.get("source_priority", 4)
            is_sub = "sub" in sname
            is_dub = ("dub" in sname) or (
                (" eng" in sname or "english" in sname) and not is_sub
            )
            audio_penalty = (
                1
                if (ttype == "sub" and is_dub) or (ttype == "dub" and not is_dub)
                else 0
            )
            is_hard = "hardsub" in sname or "hard-sub" in sname or "hard sub" in sname
            is_soft = "softsub" in sname or "all sub" in sname or "multi sub" in sname
            is_donghua = str(target_pid or "").lower() in {
                "animexin",
                "lucifer",
                "animekhor",
            }
            if is_donghua:
                sub_rank = 0 if is_hard else (2 if is_soft else 1)
            else:
                sub_rank = 0 if is_soft else (2 if is_hard else 1)
            q_key = quality_preference_key(res, target_quality)
            return (
                streams.torrent_fallback_sort_value(s),
                sub_rank,
                audio_penalty,
                q_key,
                prio,
                streams.torrent_size_sort_value(s),
            )

        for s in sorted(active_list, key=_mirror_sort_key):
            key = (s.get("source_name"), s.get("resolution"), s.get("link"))
            if key not in seen:
                seen.add(key)
                out.append(s)
        return out

    _live_deduped = []

    def _mirror_refresh(q=""):
        nonlocal _live_deduped
        _live_deduped = _dedup()
        mopts = [_mlabel(s) for s in _live_deduped]

        _bg_lock = streams._bg_lock
        _bg_thread = streams._bg_thread
        _bg_stats = streams._bg_stats

        with _bg_lock:
            resolver_alive = bool(_bg_thread and _bg_thread.is_alive()) and (
                streams._active_stream_key == cur_key
            )
            with _torrent_mirror_augment_lock:
                torrent_alive = cur_key in _torrent_mirror_augmenting
            alive = resolver_alive or torrent_alive
            r, f = _bg_stats["resolved"], _bg_stats["failed"]
            tot = _bg_stats.get("total", r + f)
            status_msg = _bg_stats.get("status_msg", "")
            if torrent_alive and not resolver_alive:
                status_msg = "searching torrent mirrors..."

        C_D = "\033[38;5;248m"
        R = "\033[0m"

        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80

        num_found = len(mopts)
        found_str = (
            f"{num_found} mirror found"
            if num_found == 1
            else f"{num_found} mirrors found"
        )

        if torrent_alive:
            from media_cli.ui.spinner import spinner_frame, spinner_from_config

            spinner = spinner_frame(spinner_from_config(cfg))
            if resolver_alive and (r + f) < tot:
                left_part = f"{spinner} {found_str} • checking ({r+f}/{tot})"
                right_part = status_msg or "searching torrent mirrors..."
            else:
                left_part = f"{spinner} {found_str}"
                right_part = "searching torrent mirrors..."
            plain_status = f"{left_part}  │  {right_part}"
        elif alive and (r + f) < tot:
            from media_cli.ui.spinner import spinner_frame, spinner_from_config

            spinner = spinner_frame(spinner_from_config(cfg))
            left_part = f"{spinner} {found_str} • checking ({r+f}/{tot})"
            right_part = status_msg or "checking sources..."
            plain_status = f"{left_part}  │  {right_part}"
        elif alive and tot == 0:
            from media_cli.ui.spinner import spinner_frame, spinner_from_config

            spinner = spinner_frame(spinner_from_config(cfg))
            plain_status = f"{spinner} Loading sources…"
        else:
            sources_checked = tot if tot > 0 else (r + f)
            src_str = (
                f"{sources_checked} source checked"
                if sources_checked == 1
                else f"{sources_checked} sources checked"
            )
            if num_found > 0:
                left_part = f"✔ {found_str} • {src_str}"
            else:
                left_part = f"✘ No mirrors found • {src_str}"
            right_part = "Ctrl+R=refresh  ?=Help  Esc=back"
            plain_status = f"{left_part}  │  {right_part}"

        parts = []
        if ui.ui_show_ctx:
            ep_str = playback_mod._display_episode_label(
                ui.ui_show_ctx, mirror_ep, ttype
            )
            app_core.build_info_panel(
                ui.ui_show_ctx,
                ttype,
                w,
                parts,
                override_ep_str=ep_str,
                local_only=getattr(ms, "_is_downloads", False),
            )

        toast = ui.pref_toast
        toast_time = ui.pref_toast_time

        def footer(s):
            return _truncate_display(s, max(1, w - 1))

        if toast and time.time() - toast_time < 3:
            parts.append(f"\033[38;5;222m* {footer(toast)}\033[0m")
        else:
            parts.append(f"{C_D}{footer(plain_status)}{R}")

        hdr = "\n".join(parts)
        return mopts, hdr, not alive

    init_opts, init_hdr, _ = _mirror_refresh()
    # Open mirror list immediately (even if empty) - live_fn will update as mirrors are resolved
    if not init_opts:
        with streams._bg_lock:
            still_alive = (
                streams._bg_thread
                and streams._bg_thread.is_alive()
                and streams._active_stream_key == cur_key
            )
        with _torrent_mirror_augment_lock:
            still_alive = still_alive or cur_key in _torrent_mirror_augmenting
        if not still_alive:
            # Background thread is dead, try one more time
            init_opts, init_hdr, _ = _mirror_refresh()
            if not init_opts:
                if ui.ui_show_ctx:
                    p_name = (
                        ui.ui_show_ctx.get("_provider_name")
                        or ui.ui_show_ctx.get("_provider")
                        or ""
                    ).title() or "Provider"
                    ep_label = playback_mod._display_episode_label(
                        ui.ui_show_ctx, mirror_ep, ttype
                    )
                    app_core.set_action_feedback(
                        ui.ui_show_ctx,
                        f"No stream mirrors available for {playback_mod._fmt_ep(ep_label)} on {p_name}.",
                    )
                return "DETAILS"

    def _tab_pref(opt_idx):
        if 0 <= opt_idx < len(_live_deduped):
            s = _live_deduped[opt_idx]
            app_core.toggle_preferred_mirror(
                ms.show_id, s["source_name"], s.get("resolution", "?")
            )
            ui.pref_toast = "Preferred server updated (Will apply on next playback)"
            ui.pref_toast_time = time.time()
        return _mirror_refresh()[:2]

    def _mirror_force_refresh(opt=None):
        app_core._clear_streams(cur_key)
        ep_data = app_core.with_loading(
            f"Refreshing {ttype.upper()} sources and torrents…",
            _load_episode_data_for_mirrors,
            ms.show_id,
            mirror_ep,
            ttype,
            provider_id=target_pid,
            show_ctx=ui.ui_show_ctx,
        )
        if ep_data:
            app_core._set_cached_ep_data(ep_data, cur_key)
            streams.start_bg_resolve(
                ep_data, set(), ms.show_id, mirror_ep, ttype, target_pid, quality
            )
        return _mirror_refresh()[:2]

    hd8 = picker_help(
        "Play stream", "Go back", "Go back", "Mark preferred", "Force refresh"
    )
    ui.pref_toast = ""

    midx = tui_pick(
        flags,
        ui,
        "Select mirror",
        init_opts,
        header=init_hdr,
        live_fn=_mirror_refresh,
        tab_fn=_tab_pref,
        reverse_fn=_mirror_force_refresh,
        info_fn=app_core.make_single_show_info_fn(ui.ui_show_ctx, ui),
        help_dict=hd8,
    )

    if midx in (-2, -3):
        return "DETAILS"
    elif midx >= 0 and midx < len(_live_deduped):
        ms.selected_stream = _live_deduped[midx]
        return "PLAY"
    else:
        if not _live_deduped and ui.ui_show_ctx:
            p_name = (
                ui.ui_show_ctx.get("_provider_name")
                or ui.ui_show_ctx.get("_provider")
                or ""
            ).title() or "Provider"
            ep_label = playback_mod._display_episode_label(
                ui.ui_show_ctx, mirror_ep, ttype
            )
            app_core.set_action_feedback(
                ui.ui_show_ctx,
                f"No stream mirrors available for {playback_mod._fmt_ep(ep_label)} on {p_name}.",
            )
        return "DETAILS"
