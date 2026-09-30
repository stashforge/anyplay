"""Episode selection and label formatting handlers for playback."""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..context import CliFlags, MachineState, UiState

from .. import app_core
from ..core.terminal import truncate_display as _truncate_display
from ..domain.episodes import (
    clean_episode_identifier,
    episode_id_at,
    episode_label,
)
from ..ui.help import picker_help
from ..ui.picker import tui_pick

_C_HINT = "\033[38;5;244m"
_RST    = "\033[0m"


def _episode_labels_for(show: dict, ttype: str) -> dict:
    if not show:
        return {}
    labels = show.get("_episode_labels") or {}
    labels_ttype = show.get("_episode_labels_ttype")
    if labels_ttype and labels_ttype != ttype:
        return {}
    return labels


def _display_episode_label(show: dict, episode_id, ttype: str) -> str:
    labels = _episode_labels_for(show, ttype)
    raw = str(labels.get(str(episode_id)) or episode_id)
    from media_cli.domain.episodes import resolve_dual_episode_label
    dual = resolve_dual_episode_label(show, raw)
    if dual:
        return dual
    return clean_episode_identifier(raw) or raw


def _fmt_ep(label):
    raw = str(label).strip()
    from media_cli.domain.episodes import parse_episode_dual_numbers
    _, sec = parse_episode_dual_numbers(raw)
    clean = raw if sec else (clean_episode_identifier(raw) or raw)
    label_str = clean
    if not label_str:
        return "EP ?"
    if label_str[0].isdigit():
        return f"EP {label_str}"
    if label_str.lower() == "ova":
        return "OVA"
    if label_str.lower().startswith("ova "):
        return "OVA " + label_str[4:]
    return label_str.title()


def _clear_episode_source_state(ms: MachineState) -> None:
    ms.ep_cache_key = None
    ms.ep_cache_data = None
    ms.selected_stream = None


def format_mirror_label(stream: dict, *, prefix: str = "", safe_tag: str = "") -> str:
    name = str(stream.get("source_name") or "Unknown").strip()
    stream_type = str(stream.get("type") or "?").upper()
    resolution = str(stream.get("resolution") or "?").strip()

    cat_match = re.search(r"\[(Softsub|Hardsub|Dub|RAW|Sub|Multi-Sub)\]", name, flags=re.I)
    cat_prefix = ""
    if cat_match:
        cat_prefix = f"[{cat_match.group(1).capitalize()}] "
        name = name[:cat_match.start()] + name[cat_match.end():]
        name = re.sub(r"\s+", " ", name).strip()

    res_match = re.search(r"\(([^)]+)\)", name)
    if res_match:
        quality_str = res_match.group(1).strip()
        name = name[:res_match.start()] + name[res_match.end():]
        name = re.sub(r"\s+", " ", name).strip()
    elif resolution != "?" and resolution.casefold() not in name.casefold():
        quality_str = resolution
    else:
        quality_str = ""

    if quality_str:
        if quality_str.lower().endswith("p") and quality_str[:-1].isdigit():
            res_part = f"({quality_str.upper()})"
        elif quality_str.lower() in ("auto", "adaptive"):
            res_part = "(Auto)"
        else:
            res_part = f"({quality_str})"
    else:
        res_part = ""

    name = re.sub(r"\[\s*" + re.escape(stream_type) + r"[^\]]*\]", "", name, flags=re.I).strip()
    name = re.sub(r"\s+", " ", name).strip()

    check = "✓" if (safe_tag and ("✓" in safe_tag or "✔" in safe_tag)) else ""
    if check:
        type_part = f"{stream_type} {check}"
    elif stream_type != "?":
        type_part = stream_type
    else:
        type_part = ""

    head = f"{prefix}{cat_prefix}{name}".strip()
    parts = [head] if head else []
    if res_part:
        parts.append(res_part)
    if type_part:
        parts.append(type_part)

    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def handle_episode_state(
    flags: CliFlags,
    ui: UiState,
    ms: MachineState,
    cfg: dict,
    args: Any,
    ttype: str,
    resolve_tracking_fn,
) -> str:
    show = ui.ui_show_ctx
    episode_ids = app_core.ensure_episode_ids(show, ttype)
    if not episode_ids and ttype in ("dub", "sub"):
        alt_ttype = "sub" if ttype == "dub" else "dub"
        alt_ids = app_core.ensure_episode_ids(show, alt_ttype)
        if alt_ids:
            p_name = (show.get("_provider_name") or (show.get("_provider") or "").title() or "provider") if show else "provider"
            missing_mode = ttype.upper()
            ttype = alt_ttype
            ui.ui_ttype_ctx = ttype
            episode_ids = alt_ids
            app_core.set_action_feedback(
                show,
                f"No {missing_mode} available for this title on {p_name}. Switched to {ttype.upper()}."
            )
    if not episode_ids:
        app_core.err(app_core.episode_catalog_error(show))
        if ui.ep_prev_state in ("SEARCH", "ANILIST_SEARCH", "ANILIST_BROWSE", "ANILIST_AIRING"):
            return "DETAILS"
        return ui.ep_prev_state

    ms.total_eps = len(episode_ids) or ms.total_eps
    episodes_map = show.get("_episodes_map") or {}
    if not episodes_map and show.get("_episodes_map_by_ttype"):
        episodes_map = show["_episodes_map_by_ttype"].get(ttype) or {}
    episode_labels = _episode_labels_for(show, ttype)
    display_order = list(range(len(episode_ids)))
    if app_core.get_episode_order(ms.show_id, cfg.get("episode_order", "asc")) == "desc":
        display_order.reverse()

    def _is_ep_downloaded(eid, lbl):
        dl_files = getattr(ms, "_download_files", {}) or {}
        if str(eid) in dl_files or str(lbl) in dl_files:
            return True
        ep_entry = episodes_map.get(str(eid))
        if ep_entry:
            disp = ep_entry.get("display_number")
            f_num = ep_entry.get("franchise_number")
            if disp and (disp in dl_files or f"Episode {disp}" in dl_files):
                return True
            if f_num and f_num in dl_files:
                return True
        from ..domain.episodes import clean_episode_identifier, parse_episode_dual_numbers
        prim, sec = parse_episode_dual_numbers(str(lbl))
        clean = (prim or clean_episode_identifier(str(lbl)) or str(lbl)).lstrip("0") or "0"
        clean_eid = clean_episode_identifier(str(eid))
        clean_eid_num = (clean_eid.lstrip("0") or "0") if clean_eid else None
        if clean in dl_files or (clean_eid_num and clean_eid_num in dl_files):
            return True
        for k in dl_files:
            kp, ks = parse_episode_dual_numbers(str(k))
            kc = (kp or clean_episode_identifier(str(k)) or str(k)).lstrip("0") or "0"
            if kc == clean or (clean_eid_num and kc == clean_eid_num) or (sec and sec == ks):
                return True
        f = app_core.find_offline_file_for_episode(ms.show_title or (show.get("name", "") if show else ""), lbl, cfg)
        if f:
            return True
        if prim:
            f = app_core.find_offline_file_for_episode(ms.show_title or (show.get("name", "") if show else ""), prim, cfg)
            if f:
                return True
        return False

    def _is_ep_watched(eid, lbl, orig_idx=None):
        if not show:
            return False
        ep_entry = episodes_map.get(str(eid))
        watched_eps = show.get("watched_episodes", [])
        if not watched_eps:
            try:
                from ..core.storage import load_downloads_db
                db = load_downloads_db()
                folder_name = show.get("_folder_name") or getattr(ms, "show_title", "") or show.get("name")
                if folder_name and folder_name in db.get("shows", {}):
                    watched_eps = db["shows"][folder_name].get("watched_episodes", [])
            except Exception:
                pass

        if watched_eps:
            if str(eid) in watched_eps or str(lbl) in watched_eps:
                return True
            if ep_entry:
                disp = ep_entry.get("display_number")
                f_num = ep_entry.get("franchise_number")
                if disp and (disp in watched_eps or f"Episode {disp}" in watched_eps):
                    return True
                if f_num and f_num in watched_eps:
                    return True
            else:
                from ..domain.episodes import clean_episode_identifier, parse_episode_dual_numbers
                prim, sec = parse_episode_dual_numbers(str(lbl))
                clean = (prim or clean_episode_identifier(str(lbl)) or str(lbl)).lstrip("0") or "0"
                clean_eid = clean_episode_identifier(str(eid))
                clean_eid_num = (clean_eid.lstrip("0") or "0") if clean_eid else None
                for w in watched_eps:
                    wp, ws = parse_episode_dual_numbers(str(w))
                    wc = (wp or clean_episode_identifier(str(w)) or str(w)).lstrip("0") or "0"
                    if wc == clean or (clean_eid_num and wc == clean_eid_num) or (sec and sec == ws):
                        return True

        local_p = show.get("_local_progress")
        if local_p is None:
            local_p = app_core.get_local_progress(show, ttype)
        al_p = show.get("_anilist_progress")
        eff_p = None
        if local_p is not None:
            try:
                eff_p = float(str(local_p))
            except (ValueError, TypeError):
                pass
        if al_p is not None:
            try:
                al_val = float(str(al_p))
                if eff_p is None or al_val > eff_p:
                    eff_p = al_val
            except (ValueError, TypeError):
                pass

        if eff_p is not None and eff_p > 0:
            if ep_entry and ep_entry.get("number", 0.0) > 0:
                if ep_entry["number"] <= eff_p:
                    return True
            else:
                from ..domain.episodes import clean_episode_identifier, parse_episode_dual_numbers
                prim, sec = parse_episode_dual_numbers(str(lbl))
                clean = (prim or clean_episode_identifier(str(lbl)) or clean_episode_identifier(str(eid)) or str(lbl)).lstrip("0") or "0"
                try:
                    ep_val = float(clean)
                    if ep_val <= eff_p:
                        return True
                except (ValueError, TypeError):
                    pass
            if orig_idx is not None and orig_idx < eff_p:
                return True

        return False

    def _build_ep_option(orig_i):

        eid = episode_ids[orig_i]
        lbl = episode_label(eid, episode_labels)
        is_dl = _is_ep_downloaded(eid, lbl)
        is_w = _is_ep_watched(eid, lbl, orig_i)
        if is_w:
            if is_dl:
                return f"\033[38;5;244m{lbl}  \033[38;2;166;227;161m✔\033[0m"
            return f"\033[38;5;244m{lbl}\033[0m"
        else:
            if is_dl:
                return f"{lbl}  \033[38;2;166;227;161m✔\033[0m"
            return f"{lbl}"

    def _compute_disabled_indices():
        return set()

    def _ep_hint_fn(oi):
        if not (0 <= oi < len(display_order)):
            return ""
        orig_i = display_order[oi]
        eid = episode_ids[orig_i]
        lbl = episode_label(eid, episode_labels)
        is_dl = _is_ep_downloaded(eid, lbl)
        is_w = _is_ep_watched(eid, lbl, orig_i)
        if getattr(ms, "_is_downloads", False):
            if is_w:
                if is_dl:
                    return "watched • ready to watch"
                return "\033[38;5;244mwatched • not downloaded\033[0m"
            else:
                if is_dl:
                    return "ready to watch"
                return "\033[38;5;167m✘\033[38;5;244m not downloaded\033[0m"
        else:
            if is_w:
                if is_dl:
                    return "watched • ready to watch"
                return "\033[38;5;244mwatched\033[0m"
            else:
                if is_dl:
                    return "ready to watch"
                return ""

    ep_opts = [_build_ep_option(i) for i in display_order]
    disabled_indices = _compute_disabled_indices()

    _hdr_cache: dict[tuple, str] = {}

    def _ep_hdr(si):
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80
        feedback = app_core.get_active_feedback(show)
        cache_key = (w, ttype, feedback, getattr(ms, "_is_downloads", False), si)
        if cache_key in _hdr_cache:
            return _hdr_cache[cache_key]
        parts = []
        if show:
            app_core.build_info_panel(show, ttype, w, parts, local_only=getattr(ms, "_is_downloads", False))

        def _t(s):
            return _truncate_display(s, max(1, w - 1))
        direct_single = ui.ep_prev_state == "SEARCH" and len(ms.shows) <= 1 and ms.just_searched
        nav_text = "Esc=Search" if direct_single else "Esc=Back"
        if feedback:
            parts.append(f"\033[38;5;222m{_t(feedback)}{_RST}")
        else:
            p_name = (show.get("_provider_name") or (show.get("_provider") or "").title()) if show else ""
            offline_badge = "\033[38;2;225;85;85m[Offline]\033[0m • " if not app_core.is_online() else ""
            prefix = f"{offline_badge}{p_name} • " if p_name else offline_badge
            action_nav = ""
            if getattr(ms, "_is_downloads", False) and 0 <= si < len(display_order):
                orig_i = display_order[si]
                eid = episode_ids[orig_i]
                lbl = episode_label(eid, episode_labels)
                if _is_ep_downloaded(eid, lbl):
                    action_nav = "Enter=Play • Del=Delete • "
                else:
                    action_nav = "Enter=Download • "
            parts.append(f"{_C_HINT}{_t(prefix + action_nav + 'Tab=Sub/Dub • Ctrl+R=flip • ?=Help • ' + nav_text)}{_RST}")
        res = "\n".join(parts)
        _hdr_cache[cache_key] = res
        return res

    def _ep_tab_fn(opt=None, direction=1):
        nonlocal ttype, episode_ids, episode_labels, display_order, ep_opts, disabled_indices
        _hdr_cache.clear()
        target_ttype = "dub" if ttype == "sub" else "sub"
        allowed, reason = app_core.check_translation_switch_capability(show, ttype, target_ttype)
        if not allowed:
            if reason:
                app_core.set_action_feedback(show, reason)
            return (ep_opts, _ep_hdr(0), disabled_indices)

        new_ids = app_core.with_loading(
            f"Switching to {target_ttype.upper()}…",
            app_core.ensure_episode_ids,
            show,
            target_ttype,
        )
        if new_ids:
            ttype = target_ttype
            ui.ui_ttype_ctx = ttype
            episode_ids = new_ids
            ms.total_eps = len(episode_ids)
            episode_labels = _episode_labels_for(show, ttype)
            display_order = list(range(len(episode_ids)))
            if app_core.get_episode_order(ms.show_id, cfg.get("episode_order", "asc")) == "desc":
                display_order.reverse()
            ep_opts = [_build_ep_option(i) for i in display_order]
            disabled_indices = _compute_disabled_indices()
        else:
            p_name = (show.get("_provider_name") or (show.get("_provider") or "").title() or "this provider") if show else "this provider"
            app_core.set_action_feedback(show, f"{target_ttype.upper()} unavailable on {p_name}")
        return (ep_opts, _ep_hdr(0), disabled_indices)

    def _ep_reverse_fn(opt=None):
        nonlocal ep_opts, display_order, disabled_indices
        _hdr_cache.clear()
        app_core.toggle_episode_order(ms.show_id, cfg.get("episode_order", "asc"))
        display_order.reverse()
        ep_opts = [_build_ep_option(i) for i in display_order]
        disabled_indices = _compute_disabled_indices()
        new_order = app_core.get_episode_order(ms.show_id, cfg.get("episode_order", "asc"))
        app_core.set_action_feedback(show, f"Order: {'Newest first (N → 1)' if new_order == 'desc' else 'Oldest first (1 → N)'}")
        return (ep_opts, _ep_hdr(0), disabled_indices)

    def _delete_ep_file(sel_idx):
        nonlocal ep_opts, disabled_indices
        if not getattr(ms, "_is_downloads", False):
            return None
        if not (0 <= sel_idx < len(display_order)):
            return None
        orig_i = display_order[sel_idx]
        eid = episode_ids[orig_i]
        lbl = episode_label(eid, episode_labels)
        if not _is_ep_downloaded(eid, lbl):
            app_core.set_action_feedback(show, f"Episode {lbl} is not downloaded.")
            _hdr_cache.clear()
            return ep_opts, _ep_hdr(sel_idx)

        folder_name = show.get("_folder_name", ms.show_title)
        deleted = app_core.delete_offline_episode(folder_name, lbl, cfg)
        if not deleted:
            deleted = app_core.delete_offline_episode(folder_name, str(eid), cfg)

        if deleted:
            dl_files = getattr(ms, "_download_files", {})
            dl_files.pop(str(eid), None)
            dl_files.pop(str(lbl), None)
            dl_files.pop(eid, None)
            dl_files.pop(lbl, None)
            dl_eps = show.get("_downloaded_episodes", [])
            if str(lbl) in dl_eps:
                dl_eps.remove(str(lbl))
            if str(eid) in dl_eps:
                dl_eps.remove(str(eid))
            app_core.set_action_feedback(show, f"✔ Deleted EP {lbl}")
        else:
            app_core.set_action_feedback(show, f"✘ Could not delete EP {lbl}")

        _hdr_cache.clear()
        ep_opts = [_build_ep_option(i) for i in display_order]
        disabled_indices = _compute_disabled_indices()
        return ep_opts, _ep_hdr(sel_idx)

    def _can_delete_ep(oi):
        if not getattr(ms, "_is_downloads", False):
            return False
        if not (0 <= oi < len(display_order)):
            return False
        orig_i = display_order[oi]
        eid = episode_ids[orig_i]
        lbl = episode_label(eid, episode_labels)
        return _is_ep_downloaded(eid, lbl)

    def _ep_delete_prompt(oi):
        if 0 <= oi < len(display_order):
            orig_i = display_order[oi]
            eid = episode_ids[orig_i]
            lbl = episode_label(eid, episode_labels)
            clean_num = re.sub(r"^(?:episode|ep)\s*", "", str(lbl), flags=re.I).strip()
            return f"Delete downloaded EP {clean_num}? y/N"
        return "Delete downloaded episode? y/N"

    while True:
        if ms.total_eps <= 1:
            idx = 0
        else:
            direct_single = ui.ep_prev_state == "SEARCH" and len(ms.shows) <= 1 and ms.just_searched
            hd6 = picker_help(
                "Play episode",
                "New search" if direct_single else "Go back",
                "Quit" if direct_single else "Go back",
                "Toggle Sub/Dub",
                reverse_label="Flip order",
                delete_label="Delete episode" if getattr(ms, "_is_downloads", False) else None,
            )
            init_sel = display_order.index(ms.current_ep_index) if ms.current_ep_index in display_order else 0
            idx = tui_pick(
                flags, ui,
                "Select episode", ep_opts,
                header_fn=_ep_hdr,
                tab_fn=_ep_tab_fn,
                reverse_fn=_ep_reverse_fn,
                delete_fn=_delete_ep_file if getattr(ms, "_is_downloads", False) else None,
                delete_prompt=_ep_delete_prompt if getattr(ms, "_is_downloads", False) else None,
                can_delete_fn=_can_delete_ep if getattr(ms, "_is_downloads", False) else None,
                hints=_ep_hint_fn,
                info_fn=app_core.make_single_show_info_fn(ui.ui_show_ctx, ui),
                help_dict=hd6,
                disabled_indices=disabled_indices,
                initial_selected=init_sel,
            )

        if idx in (-2, -3):
            return ui.ep_prev_state or "DETAILS"

        target_idx = display_order[idx]
        target_eid = episode_id_at(episode_ids, target_idx)
        target_lbl = episode_label(target_eid, episode_labels)
        if getattr(ms, "_is_downloads", False) and not _is_ep_downloaded(target_eid, target_lbl):
            if not app_core.is_online():
                app_core.set_action_feedback(show, "✘ Cannot download: Offline")
                _hdr_cache.clear()
                if ms.total_eps <= 1:
                    return ui.ep_prev_state or "DETAILS"
                continue
            ms.current_ep_index = target_idx
            ms.current_ep = target_eid
            ms._android_pending_watched_ep = None
            ms._android_pending_watched_idx = None
            ui.action_prev_state = "EPISODE"
            ms.selected_stream = None
            app_core._clear_streams()
            args.download = True
            return "PLAY"

        ms.current_ep_index = target_idx
        ms.current_ep = target_eid
        ms._android_pending_watched_ep = None
        ms._android_pending_watched_idx = None
        ui.action_prev_state = "EPISODE"
        ms.selected_stream = None
        app_core._clear_streams()
        return "PLAY"
