"""Sub-modals for details screen: updating progress, status, and scores."""

from __future__ import annotations

import os
from typing import Any

from .. import app_core
from ..context import CliFlags, MachineState, UiState
from ..domain.episodes import highest_episode_number
from ..domain.tracking import (
    apply_tracking_progress_local,
    completed_media_total,
    tracking_status_for_progress,
)
from ..ui.help import picker_help
from ..ui.picker import tui_pick


def handle_update_progress_state(
    flags: CliFlags,
    ui: UiState,
    ms: MachineState,
    cfg: dict,
    args: Any,
    ttype: str,
    resolve_tracking_fn,
) -> str:
    s = ui.ui_show_ctx
    ttype_local = ui.ui_ttype_ctx
    episode_ids = app_core.ensure_episode_ids(s, ttype_local)

    local_p = app_core.get_local_progress(s, ttype_local)

    released = s.get("availableEpisodes", {}).get(ttype_local, 0)
    try:
        released = int(released) if released is not None else 0
    except ValueError:
        released = 0
    if episode_ids:
        import decimal as _dec
        _highest = highest_episode_number(episode_ids)
        try:
            _highest_num = int(_dec.Decimal(str(_highest)))
        except (_dec.InvalidOperation, ValueError, TypeError):
            _highest_num = len(episode_ids)
        released = max(released, _highest_num)

    al_id = app_core.get_show_anilist_id(s)
    has_token = bool(cfg.get("anilist_token")) and not flags.incognito_mode
    sync_active = bool(al_id and has_token and resolve_tracking_fn(ui.search_prev_state, args, cfg, s))

    from ..domain.episodes import (
        build_progress_entries,
        clean_episode_identifier,
        parse_episode_dual_numbers,
        resolve_dual_episode_label,
    )
    entries = build_progress_entries(s, episode_ids, labels=s.get("_episode_labels"), ttype=ttype_local)
    progress_entries = list(reversed(entries))

    local_lbl = s.get("_local_episode_label") or local_p
    resolved_watched = resolve_dual_episode_label(s, local_lbl or local_p)
    w_prim, w_sec = parse_episode_dual_numbers(resolved_watched)

    try:
        cur_rel = int(s.get("_anilist_progress") or w_prim or 0)
    except (ValueError, TypeError):
        cur_rel = 0

    try:
        cur_abs = int(w_sec or clean_episode_identifier(str(local_p or 0)) or 0)
    except (ValueError, TypeError):
        cur_abs = 0

    watched_list = s.get("watched_episodes", [])
    if not watched_list:
        try:
            from ..core.storage import load_downloads_db
            db = load_downloads_db()
            folder_name = s.get("_folder_name") or getattr(ms, "show_title", "") or s.get("name")
            if folder_name and folder_name in db.get("shows", {}):
                watched_list = db["shows"][folder_name].get("watched_episodes", [])
        except Exception:
            pass

    def _is_entry_watched(entry):
        if entry.get("id") == "0":
            return False
        if watched_list:
            if str(entry.get("id")) in watched_list or str(entry.get("ep_number")) in watched_list:
                return True
        ep_rel = entry.get("anilist_progress", 0)
        try:
            ep_abs = int(entry.get("ep_number", 0))
        except (ValueError, TypeError):
            ep_abs = 0
        if cur_rel > 0 and ep_rel <= cur_rel:
            return True
        if cur_abs > 0 and ep_abs <= cur_abs:
            return True
        return False

    def _is_entry_current(entry):
        if entry.get("id") == "0":
            return False
        ep_rel = entry.get("anilist_progress", 0)
        try:
            ep_abs = int(entry.get("ep_number", 0))
        except (ValueError, TypeError):
            ep_abs = 0
        if cur_rel > 0 and ep_rel == cur_rel:
            return True
        if cur_abs > 0 and ep_abs == cur_abs:
            return True
        return False

    _hdr_cache: dict[tuple, str] = {}

    def _progress_hdr(si):
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80
        cache_key = (w, ttype_local, getattr(ms, "_is_downloads", False))
        if cache_key in _hdr_cache:
            return _hdr_cache[cache_key]
        parts = []
        app_core.build_info_panel(s, ttype_local, w, parts, local_only=getattr(ms, "_is_downloads", False))
        parts.append(app_core._poster_footer_line(s, "Enter/Right=set progress  Ctrl+R=flip  ? = Help  Left/Esc=back", w))
        res = "\n".join(parts)
        _hdr_cache[cache_key] = res
        return res

    def _format_entry_label(entry):
        lbl = entry["label"]
        if _is_entry_watched(entry):
            return f"\033[38;5;244m{lbl}\033[0m"
        return lbl

    progress_opts = [_format_entry_label(e) for e in progress_entries]
    progress_hints = {}
    for e in progress_entries:
        lbl = e["label"]
        if e.get("id") == "0":
            progress_hints[lbl] = "reset"
        elif _is_entry_current(e):
            progress_hints[lbl] = "current"
        elif _is_entry_watched(e):
            progress_hints[lbl] = "lower progress"
        else:
            progress_hints[lbl] = "sync to AniList" if sync_active else "mark watched"

    def _progress_tab_fn(opt=None):
        nonlocal progress_entries, progress_opts
        progress_entries.reverse()
        progress_opts = [_format_entry_label(e) for e in progress_entries]
        return (progress_opts, _progress_hdr(0))

    hd9 = picker_help(
        "Set progress",
        "Go back",
        "Go back",
        "Flip order",
        reverse_label="Flip order",
    )
    picker_title = "Set AniList Progress" if sync_active else "Set Watch Progress"
    idx = tui_pick(
        flags, ui,
        picker_title, progress_opts,
        header_fn=_progress_hdr,
        hints=progress_hints,
        tab_fn=_progress_tab_fn,
        reverse_fn=_progress_tab_fn,
        info_fn=app_core.make_single_show_info_fn(s, ui),
        help_dict=hd9
    )

    if idx >= 0:
        chosen = progress_entries[idx]
        al_target = chosen["anilist_progress"]
        local_target = chosen["ep_number"]
        target_ep_id = chosen["provider_id"]

        if sync_active:
            status_value = tracking_status_for_progress(s, al_target) if al_target > 0 else None
            updated = app_core.with_loading(
                "Syncing to AniList…",
                app_core.update_anilist_entry,
                cfg["anilist_token"],
                int(al_id),
                progress=al_target,
                status=status_value,
                show=s,
            )
            if updated:
                apply_tracking_progress_local(s, al_target, status_value)
                s["_progress_authority"] = "AL"
                if target_ep_id and str(target_ep_id) != "0":
                    app_core.write_history_progress(s, target_ep_id, ttype_local, touch=True)
                s["_local_progress"] = local_target
                s["_local_episode_label"] = chosen["label"]
                app_core.set_action_feedback(s, f"✔ Synced {chosen['label']} to AniList")
            else:
                app_core.err("AniList sync failed.")
        else:
            if target_ep_id and str(target_ep_id) != "0":
                app_core.write_history_progress(s, target_ep_id, ttype_local, touch=True)
            else:
                app_core.write_history_progress(s, "0", ttype_local, touch=True)
            s["_local_progress"] = local_target
            s["_local_episode_label"] = chosen["label"]
            s["_progress_authority"] = "LOCAL"
            app_core.set_action_feedback(s, f"✔ Saved progress: {chosen['label']}")

        if s.get("_id") and target_ep_id and str(target_ep_id) != "0":
            app_core.save_resume_time(s.get("_id"), target_ep_id, 0)

        ms.current_ep_index = None
        ms.current_ep = None

    return "DETAILS"


def handle_update_status_state(
    flags: CliFlags,
    ui: UiState,
    ms: MachineState,
    cfg: dict,
    args: Any,
    ttype: str,
    resolve_tracking_fn,
) -> str:
    s = ui.ui_show_ctx
    ttype_local = ui.ui_ttype_ctx

    _hdr_cache: dict[tuple, str] = {}

    def _status_hdr(si):
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80
        cache_key = (w, ttype_local, getattr(ms, "_is_downloads", False))
        if cache_key in _hdr_cache:
            return _hdr_cache[cache_key]
        parts = []
        app_core.build_info_panel(s, ttype_local, w, parts, local_only=getattr(ms, "_is_downloads", False))
        parts.append(app_core._poster_footer_line(s, "Enter/Right=select  ? = Help  Left/Esc=back", w))
        res = "\n".join(parts)
        _hdr_cache[cache_key] = res
        return res

    status_choices = [
        ("Watching", "CURRENT"),
        ("Rewatching", "REPEATING"),
        ("Planning", "PLANNING"),
        ("Completed", "COMPLETED"),
        ("Dropped", "DROPPED"),
        ("Paused", "PAUSED"),
    ]
    opts = [label for label, _status in status_choices]
    hd7 = picker_help("Select status", "Go back", "Go back")

    idx = tui_pick(
        flags, ui,
        "Update AniList Status", opts,
        header_fn=_status_hdr,
        info_fn=app_core.make_single_show_info_fn(s, ui),
        help_dict=hd7
    )

    if idx >= 0:
        al_id = app_core.get_show_anilist_id(s)
        if al_id:
            label, status_value = status_choices[idx]
            progress_value = None
            if status_value == "COMPLETED":
                progress_value = completed_media_total(s, ttype_local)
                if not progress_value:
                    app_core.err("This anime is not finished or its total EP count is unknown.")
                    return "DETAILS"

            updated = app_core.with_loading(
                "Updating status…",
                app_core.update_anilist_entry,
                cfg["anilist_token"],
                int(al_id),
                progress=progress_value,
                status=status_value,
                show=s,
            )

            if updated:
                s["_anilist_list"] = status_value
                if progress_value is not None:
                    apply_tracking_progress_local(s, progress_value, status_value)
                    s["_progress_authority"] = "AL"
                    app_core.set_action_feedback(
                        s,
                        "✔ Synced status to AniList"
                    )
            else:
                app_core.err(f"Could not update AniList status to {label}.")

    return "DETAILS"


def handle_update_score_state(
    flags: CliFlags,
    ui: UiState,
    ms: MachineState,
    cfg: dict,
    args: Any,
    ttype: str,
    resolve_tracking_fn,
) -> str:
    s = ui.ui_show_ctx
    ttype_local = ui.ui_ttype_ctx

    _hdr_cache: dict[tuple, str] = {}

    def _score_hdr(si):
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80
        cache_key = (w, ttype_local, getattr(ms, "_is_downloads", False))
        if cache_key in _hdr_cache:
            return _hdr_cache[cache_key]
        parts = []
        app_core.build_info_panel(s, ttype_local, w, parts, local_only=getattr(ms, "_is_downloads", False))
        parts.append(app_core._poster_footer_line(s, "Enter/Right=select  ? = Help  Left/Esc=back", w))
        res = "\n".join(parts)
        _hdr_cache[cache_key] = res
        return res

    opts = [str(i) for i in range(10, 0, -1)]
    hd8 = picker_help("Select score", "Go back", "Go back")

    idx = tui_pick(
        flags, ui,
        "Update Score", opts,
        header_fn=_score_hdr,
        info_fn=app_core.make_single_show_info_fn(s, ui),
        help_dict=hd8
    )

    if idx >= 0:
        al_id = app_core.get_show_anilist_id(s)
        if al_id:
            updated = app_core.with_loading(
                "Updating score…",
                app_core.update_anilist_entry,
                cfg["anilist_token"],
                int(al_id),
                score=int(opts[idx]) * 10,
                show=s,
            )
            if not updated:
                app_core.err(f"Could not update AniList score to {opts[idx]}/10.")

    return "DETAILS"
