"""Episode selection and label formatting handlers for playback."""

from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..context import CliFlags, MachineState, UiState

from .. import app_core
from ..domain.episodes import (
    clean_episode_identifier,
    episode_id_at,
    episode_label,
)
from ..ui import loading_messages
from ..media.episode_identity import download_identity_conflicts
from ..ui.confirmation import confirm_view_action
from .details_menu_controller import run_details_menu
from .episode_menu_view import EpisodeMenuOption, build_episode_menu_view
from .footer import mode_label, provider_label


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


def _episode_picker_label(label) -> str:
    raw = str(label or "").strip()
    if not raw:
        return "Episode ?"
    if re.match(r"^(?:episode|ep)\s+", raw, flags=re.I):
        return raw
    if re.match(r"^(?:ova|movie|special)(?:\b|$)", raw, flags=re.I):
        return raw
    if re.match(r"^\d+(?:\.\d+)?[.)]\s+\S", raw):
        return raw
    if re.match(r"^\d+(?:\.\d+)?(?:\s*(?:\[[^\]]+\]|\([^)]+\)))*$", raw):
        return f"Episode {raw}"
    return raw


def _prune_missing_download_files(ms: MachineState) -> dict:
    dl_files = getattr(ms, "_download_files", {}) or {}
    if not isinstance(dl_files, dict):
        ms._download_files = {}
        return {}
    stale_keys = []
    for key, path in list(dl_files.items()):
        if (not path or not os.path.isfile(os.path.expanduser(str(path)))
                or download_identity_conflicts(str(key), str(path))):
            stale_keys.append(key)
    for key in stale_keys:
        dl_files.pop(key, None)
    ms._download_files = dl_files
    return dl_files


def _clear_episode_source_state(ms: MachineState) -> None:
    ms.ep_cache_key = None
    ms.ep_cache_data = None
    ms.selected_stream = None


def _build_offline_file_lookup(show_title: str, cfg: dict | None = None) -> dict[str, str]:
    if not show_title:
        return {}
    try:
        from media_cli.core.storage import get_default_download_dir, is_valid_download_video
        from media_cli.media.download import sanitize_filename
    except Exception:
        return {}

    cfg = cfg or {}
    download_dir = cfg.get("download_dir") or get_default_download_dir()
    if not os.path.exists(download_dir):
        return {}

    show_folder = sanitize_filename(show_title)
    full_show_path = os.path.join(download_dir, show_folder)
    if not os.path.isdir(full_show_path):
        full_show_path = os.path.join(download_dir, show_title)
        if not os.path.isdir(full_show_path):
            return {}

    from media_cli.domain.episodes import clean_episode_identifier, parse_episode_dual_numbers

    lookup: dict[str, str] = {}
    ep_explicit_re = re.compile(r'\b(?:episodes?|ep)\b[-_.\s]*([^\n]+)$', re.IGNORECASE)
    ep_fallback_re = re.compile(r'(?:^|[^\d])0*(\d+(?:\.\d+)?)(?:[^\d]|$)')

    def _add_key(value, path):
        key = str(value or "").strip()
        if key:
            lookup.setdefault(key, path)
            lookup.setdefault(key.lstrip("0") or "0", path)

    try:
        for fname in sorted(os.listdir(full_show_path)):
            full_path = os.path.join(full_show_path, fname)
            if not is_valid_download_video(full_path):
                continue
            _add_key(fname, full_path)
            stem = os.path.splitext(fname)[0]
            _add_key(stem, full_path)
            m_exp = ep_explicit_re.search(stem)
            if m_exp:
                raw_label = m_exp.group(1).strip()
                f_prim, f_sec = parse_episode_dual_numbers(raw_label)
                f_clean = f_prim or clean_episode_identifier(raw_label) or raw_label
                _add_key(raw_label, full_path)
                _add_key(f_clean, full_path)
                _add_key(f"Episode {f_clean}", full_path)
                if f_sec:
                    _add_key(f_sec, full_path)
                continue
            m_fallback = ep_fallback_re.search(fname)
            if m_fallback:
                found_num = m_fallback.group(1)
                _add_key(found_num, full_path)
                _add_key(f"Episode {found_num}", full_path)
    except OSError:
        return lookup
    return lookup


def _augment_episode_sources_with_torrents(
    ep_data: dict | None,
    show: dict | None,
    episode_id,
    ttype: str,
    *,
    cancel_check=None,
) -> dict | None:
    if not show:
        return ep_data
    try:
        from media_cli.core.storage import load_config
        from media_cli.providers import is_provider_active
        cfg = load_config()
        fallback_enabled = cfg.get("torrent_fallback", True)
        if isinstance(fallback_enabled, str):
            fallback_enabled = fallback_enabled.strip().casefold() in {"1", "true", "yes", "on"}
        if not fallback_enabled or not is_provider_active("torrent"):
            return ep_data
        from media_cli.torrents import augment_episode_data_with_torrents
        base_data = ep_data if isinstance(ep_data, dict) else {"episode": {"sourceUrls": []}}
        ep_label = _display_episode_label(show, episode_id, ttype)
        kwargs = {
            "show": show,
            "episode_label": ep_label,
            "episode_id": episode_id,
            "ttype": ttype,
        }
        if cancel_check is not None:
            kwargs["cancel_check"] = cancel_check
        return augment_episode_data_with_torrents(base_data, **kwargs)
    except Exception:
        return ep_data


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
    show_cover = flags.show_image and cfg.get("details_cover", True)

    def _load_episode_ids(target_ttype: str) -> list[str]:
        show_id = str((show or {}).get("_id") or (show or {}).get("id") or "")
        provider_hint = (show or {}).get("_provider") or (show or {}).get("_provider_id")
        has_legacy_ids = bool((show or {}).get("_episode_ids")) and not (show or {}).get("_episode_ids_ttype")
        if has_legacy_ids and (getattr(ms, "_is_downloads", False) or not provider_hint or show_id.startswith("local:")):
            show["_episode_ids_ttype"] = target_ttype
        return app_core.load_episode_ids_for_selection(show, target_ttype)

    episode_ids = app_core.with_loading(
        loading_messages.episode_indexing(),
        _load_episode_ids,
        ttype,
    )
    if not episode_ids and ttype in ("dub", "sub"):
        alt_ttype = "sub" if ttype == "dub" else "dub"
        alt_ids = app_core.with_loading(
            f"Checking {alt_ttype.upper()} episodes…",
            _load_episode_ids,
            alt_ttype,
        )
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
    deleted_download_keys: set[str] = set()
    offline_files = _build_offline_file_lookup(
        ms.show_title or (show.get("name", "") if show else ""),
        cfg,
    )

    def _download_identity_keys(eid, lbl) -> set[str]:
        from ..domain.episodes import clean_episode_identifier, parse_episode_dual_numbers
        keys = {str(eid), str(lbl)}
        prim, sec = parse_episode_dual_numbers(str(lbl))
        clean = (prim or clean_episode_identifier(str(lbl)) or str(lbl)).lstrip("0") or "0"
        if clean:
            keys.add(clean)
        clean_eid = clean_episode_identifier(str(eid))
        if clean_eid:
            keys.add(clean_eid.lstrip("0") or "0")
        if sec:
            keys.add(sec.lstrip("0") or "0")
        ep_entry = episodes_map.get(str(eid))
        if ep_entry:
            for value in (ep_entry.get("display_number"), ep_entry.get("franchise_number")):
                if value:
                    keys.add(str(value))
                    keys.add(f"Episode {value}")
        return keys

    def _is_ep_downloaded(eid, lbl):
        identity_keys = _download_identity_keys(eid, lbl)
        if deleted_download_keys.intersection(identity_keys):
            return False
        dl_files = _prune_missing_download_files(ms)
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
        lookup_keys = {str(eid), str(lbl), clean}
        if clean_eid_num:
            lookup_keys.add(clean_eid_num)
        if prim:
            lookup_keys.add(str(prim))
        if sec:
            lookup_keys.add(str(sec))
        if any(key in offline_files for key in lookup_keys):
            return True
        return False

    watched_snapshot = ()
    progress_snapshot = None

    def _is_ep_watched(eid, lbl, orig_idx=None):
        if not show:
            return False
        ep_entry = episodes_map.get(str(eid))
        watched_eps = watched_snapshot

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

        local_p = progress_snapshot
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
        display_lbl = _episode_picker_label(lbl)
        is_dl = _is_ep_downloaded(eid, lbl)
        is_w = _is_ep_watched(eid, lbl, orig_i)
        return EpisodeMenuOption(str(eid), display_lbl, watched=is_w, downloaded=is_dl)

    def _build_ep_options():
        nonlocal watched_snapshot, progress_snapshot
        watched_snapshot = tuple(show.get("watched_episodes") or ())
        if not watched_snapshot:
            try:
                from ..core.storage import load_downloads_db
                db = load_downloads_db()
                folder_name = show.get("_folder_name") or getattr(ms, "show_title", "") or show.get("name")
                watched_snapshot = tuple((db.get("shows", {}).get(folder_name) or {}).get("watched_episodes") or ())
            except Exception:
                pass
        progress_snapshot = show.get("_local_progress")
        if progress_snapshot is None:
            progress_snapshot = app_core.get_local_progress(show, ttype)
        return tuple(_build_ep_option(i) for i in display_order)

    if len(display_order) > 80:
        ep_opts = app_core.with_loading(
            loading_messages.episode_indexing(len(display_order)),
            _build_ep_options,
        )
    else:
        ep_opts = _build_ep_options()

    def _ep_tab_fn(opt=None, direction=1):
        nonlocal ttype, episode_ids, episode_labels, display_order, ep_opts, episodes_map
        target_ttype = "dub" if ttype == "sub" else "sub"
        allowed, reason = app_core.check_translation_switch_capability(show, ttype, target_ttype)
        if not allowed:
            if reason:
                app_core.set_action_feedback(show, reason)
            return

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
            episodes_map = (show.get("_episodes_map_by_ttype") or {}).get(ttype) or show.get("_episodes_map") or {}
            display_order = list(range(len(episode_ids)))
            if app_core.get_episode_order(ms.show_id, cfg.get("episode_order", "asc")) == "desc":
                display_order.reverse()
            ep_opts = _build_ep_options()
            app_core.set_action_feedback(show, f"Switched to {ttype.title()}.")
        else:
            p_name = (show.get("_provider_name") or (show.get("_provider") or "").title() or "this provider") if show else "this provider"
            app_core.set_action_feedback(show, f"{target_ttype.upper()} unavailable on {p_name}")

    def _ep_reverse_fn(opt=None):
        nonlocal ep_opts, display_order
        new_order = app_core.toggle_episode_order(ms.show_id, cfg.get("episode_order", "asc"))
        display_order.reverse()
        ep_opts = ep_opts[::-1]
        app_core.set_action_feedback(show, f"Order: {'Newest first (N → 1)' if new_order == 'desc' else 'Oldest first (1 → N)'}")

    def _delete_ep_file(sel_idx):
        nonlocal ep_opts, deleted_download_keys
        if not (0 <= sel_idx < len(display_order)):
            return None
        orig_i = display_order[sel_idx]
        eid = episode_ids[orig_i]
        lbl = episode_label(eid, episode_labels)
        if not _is_ep_downloaded(eid, lbl):
            app_core.set_action_feedback(show, f"Episode {lbl} is not downloaded.")
            return

        folder_name = show.get("_folder_name", ms.show_title)
        deleted = app_core.delete_offline_episode(folder_name, lbl, cfg)
        if not deleted:
            deleted = app_core.delete_offline_episode(folder_name, str(eid), cfg)

        if deleted:
            deleted_download_keys.update(_download_identity_keys(eid, lbl))
            dl_files = _prune_missing_download_files(ms)
            for key, path in list(dl_files.items()):
                if not path or not os.path.isfile(os.path.expanduser(str(path))):
                    dl_files.pop(key, None)
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

        ep_opts = _build_ep_options()

    def _can_delete_ep(oi):
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
            return f"Delete downloaded EP {clean_num}?"
        return "Delete downloaded episode?"

    provider_id = show.get("_provider") or show.get("provider") or "downloads"
    identity = show.get("_id") or show.get("id") or show.get("_folder_name") or ms.show_id
    image_key = f"{provider_id}:{identity}"

    def _episode_view():
        context = tuple(part for part in (
            mode_label(ui, ms), "[Offline]" if app_core.is_confirmed_offline() else "", provider_label(show),
        ) if part)
        return build_episode_menu_view(
            show, ep_opts, view_id=f"episodes:{image_key}", image_key=image_key,
            ttype=ttype, context=context, local_only=getattr(ms, "_is_downloads", False),
            feedback=app_core.get_active_feedback(show), show_cover=show_cover,
        )

    def _confirm_delete(reference):
        index = next((i for i, option in enumerate(ep_opts) if option.episode_id == reference), None)
        if index is None or not _can_delete_ep(index):
            return
        from ..state.theme import APPLICATION_THEME
        view = _episode_view()
        if confirm_view_action(
            flags, ui, view, item_id=view.items[index].id,
            question=_ep_delete_prompt(index), theme=APPLICATION_THEME,
        ):
            _delete_ep_file(index)

    while True:
        if ms.total_eps <= 1:
            idx = 0
        else:
            init_sel = display_order.index(ms.current_ep_index) if ms.current_ep_index in display_order else 0
            outcome = run_details_menu(
                flags, ui, read_view=_episode_view, switch_translation=_ep_tab_fn,
                information=lambda: app_core.make_single_show_info_fn(show, ui)(0),
                action_handlers={"flip": _ep_reverse_fn, "delete": _confirm_delete},
                initial_selected_id=f"episode:{ttype}:{ep_opts[init_sel].episode_id}",
            )
            if outcome.kind != "selected":
                return "QUIT" if outcome.kind == "quit" else ui.ep_prev_state or "DETAILS"
            idx = next(i for i, option in enumerate(ep_opts) if option.episode_id == outcome.action_id)

        target_idx = display_order[idx]
        target_eid = episode_id_at(episode_ids, target_idx)
        target_lbl = episode_label(target_eid, episode_labels)
        if getattr(ms, "_is_downloads", False) and not _is_ep_downloaded(target_eid, target_lbl):
            if not app_core.is_online():
                app_core.set_action_feedback(show, "✘ Cannot download: Offline")
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
