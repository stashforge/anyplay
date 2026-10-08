import os
import re
import shutil

from .. import app_core
from ..app_core import _get_poster, _poster_footer_line, build_info_panel, make_info_fn, set_ui_context, warn
from ..core.storage import get_default_download_dir, is_valid_download_video, load_config, load_downloads_db, save_config, save_downloads_db
from ..core.terminal import truncate_display as _truncate_display
from ..ui.help import picker_help
from ..ui.picker import tui_pick

_C_HINT = "\033[38;5;244m"
_RST = "\033[0m"
VIDEO_EXTS = ('.mp4', '.mkv', '.avi', '.ts', '.webm')


def extract_offline_episode_info(filepath: str) -> tuple[float, float, str, str]:
    """Extract (primary_num, secondary_num, clean_ep_id, raw_label) from filepath."""
    stem = os.path.splitext(os.path.basename(filepath))[0]
    m = re.search(r'\b(?:episodes?|ep)\b[-_.\s]*([^\n]+)$', stem, re.IGNORECASE)
    if m:
        raw_label = m.group(1).strip()
    else:
        m2 = re.search(r'(?:^|[-_.\s])(ova(?:\s*\d+)?|movie(?:\s*\d+)?|\d+(?:\.\d+)?(?:\[\d+\])?)\s*$', stem, re.IGNORECASE)
        raw_label = m2.group(1).strip() if m2 else stem

    from ..domain.episodes import clean_episode_identifier, parse_episode_dual_numbers
    prim, sec = parse_episode_dual_numbers(raw_label)
    clean_id = prim or clean_episode_identifier(raw_label) or raw_label

    try:
        prim_num = float(clean_id)
    except (ValueError, TypeError):
        prim_num = 999999.0

    try:
        sec_num = float(sec) if sec else 0.0
    except (ValueError, TypeError):
        sec_num = 0.0

    return prim_num, sec_num, clean_id, raw_label


def offline_file_sort_key(filepath: str) -> tuple[float, float, str]:
    prim_num, sec_num, _, _ = extract_offline_episode_info(filepath)
    return (prim_num, sec_num, os.path.basename(filepath))


def handle_downloads_state(flags, ui, ms, cfg, args, ttype, resolveTracking):
    db = load_downloads_db()

    raw_arg_dir = getattr(args, "download_dir", None)
    if isinstance(raw_arg_dir, str) and raw_arg_dir.strip():
        download_dir = raw_arg_dir.strip()
    else:
        download_dir = str(cfg.get("download_dir", "") or "").strip()

    if not download_dir:
        download_dir = get_default_download_dir()

    download_dir = os.path.abspath(os.path.expanduser(download_dir))
    shows = db.get("shows", {})

    dir_exists = os.path.isdir(download_dir)
    db_dir = db.get("current_download_dir", "")
    dir_matches_db = bool(db_dir and os.path.abspath(os.path.expanduser(db_dir)) == os.path.abspath(download_dir))
    found_any_known_show = False
    if dir_exists and shows:
        for t in shows.keys():
            if os.path.isdir(os.path.join(download_dir, t)):
                found_any_known_show = True
                break

    # Safe to prune only if download_dir is the established downloads folder or contains known shows.
    # Prevents wiping the database when download_dir temporarily points to an empty or invalid directory.
    safe_to_prune = dir_exists and (dir_matches_db or found_any_known_show or not shows)

    dirty = False
    valid_titles = []

    for title, data in list(shows.items()):
        folder_path = os.path.join(download_dir, title)
        if not os.path.isdir(folder_path):
            from media_cli.media.download import sanitize_filename
            alt_path = os.path.join(download_dir, sanitize_filename(title))
            if os.path.isdir(alt_path):
                shows[sanitize_filename(title)] = data
                del shows[title]
                title = sanitize_filename(title)
                folder_path = alt_path
                dirty = True
            elif safe_to_prune:
                del shows[title]
                dirty = True
                continue
            else:
                continue

        meta = data.get("metadata", {})
        temp_show = {"name": title, "_folder_name": title, **meta}
        h_entry = app_core.get_history_entry(temp_show, ttype)
        if h_entry and isinstance(h_entry.get("show"), dict):
            h_show = h_entry["show"]
            for k in (
                "_id", "id", "thumbnail", "banner", "description",
                "type", "format", "status", "season", "airedStart", "airedEnd",
                "episodeCount", "availableEpisodes", "score", "genres", "tags",
                "aniListId", "malId", "_provider", "_provider_id", "_provider_name",
                "_display_name", "_allanime_name", "englishName", "romajiName",
                "anilistMatch", "_episodes_map", "_episodes_map_by_ttype",
                "_episodes_by_number", "_episodes_by_number_by_ttype",
                "_episode_ids", "_episode_labels", "_episode_ids_by_ttype",
            ):
                if h_show.get(k) and (k not in meta or not meta.get(k)):
                    meta[k] = h_show[k]
                    dirty = True
            if h_show.get("episodeCount") and meta.get("episodeCount") != h_show["episodeCount"]:
                meta["episodeCount"] = h_show["episodeCount"]
                meta["originalEpisodeCount"] = h_show["episodeCount"]
                dirty = True
            if h_entry.get("episode") and not meta.get("_local_progress"):
                from media_cli.domain.episodes import clean_episode_identifier
                ep_clean = clean_episode_identifier(str(h_entry.get("episode")))
                if ep_clean:
                    meta["_local_progress"] = ep_clean
            data["metadata"] = meta
        elif "_id" not in meta and "availableEpisodes" not in meta:
            meta["name"] = title
            data["metadata"] = meta
            dirty = True

        try:
            actual_files = [
                os.path.join(folder_path, f)
                for f in os.listdir(folder_path)
                if is_valid_download_video(os.path.join(folder_path, f))
            ]
            actual_files.sort(key=offline_file_sort_key)
        except OSError:
            actual_files = []

        discovered_eps = []
        for f in actual_files:
            _, _, clean_id, _ = extract_offline_episode_info(f)
            if clean_id and clean_id not in discovered_eps:
                discovered_eps.append(clean_id)

        valid_eps = discovered_eps

        if len(valid_eps) != len(data.get("episodes", [])):
            data["episodes"] = valid_eps
            dirty = True

        meta = data.get("metadata", {})
        if "originalEpisodeCount" not in meta and "episodeCount" in meta:
            meta["originalEpisodeCount"] = meta["episodeCount"]
        if not meta.get("episodeCount") and not meta.get("availableEpisodes"):
            meta["episodeCount"] = len(valid_eps)
            data["metadata"] = meta
            dirty = True
        meta["downloadedCount"] = len(valid_eps)

        if valid_eps:
            valid_titles.append((title, data))
        elif safe_to_prune:
            del shows[title]
            dirty = True

    if dirty:
        save_downloads_db(db)

    # Discovery scan: auto-import folders on disk that aren't tracked in the DB
    if os.path.isdir(download_dir):
        for folder_name in os.listdir(download_dir):
            folder_path = os.path.join(download_dir, folder_name)
            if not os.path.isdir(folder_path) or folder_name in shows:
                continue
            try:
                files = [
                    os.path.join(folder_path, f)
                    for f in os.listdir(folder_path)
                    if is_valid_download_video(os.path.join(folder_path, f))
                ]
                files.sort(key=offline_file_sort_key)
            except OSError:
                continue
            if not files:
                continue
            episodes = []
            for f in files:
                _, _, clean_id, _ = extract_offline_episode_info(f)
                if clean_id and clean_id not in episodes:
                    episodes.append(clean_id)

            # Try to fetch real metadata for the discovered folder
            metadata = {"name": folder_name, "episodeCount": len(episodes)}

            data = {"metadata": metadata, "episodes": episodes}
            shows[folder_name] = data
            valid_titles.append((folder_name, data))
            dirty = True
        if dirty:
            save_downloads_db(db)

    if not valid_titles:
        warn(f"No downloaded videos found in {download_dir}.")
        return "QUIT"

    def _build_folder_opts():
        return [f"{title}  ({len(data.get('episodes', []))})" for title, data in valid_titles]

    folder_opts = _build_folder_opts()

    def _folders_hdr(si):
        try:
            w = os.get_terminal_size().columns
        except OSError:
            w = 80
        parts = []
        if 0 <= si < len(valid_titles):
            title, data = valid_titles[si]
            show = data.get("metadata", {})
            show["watched_episodes"] = data.get("watched_episodes", [])
            show["_folder_name"] = title
            if show.get("_local_progress") is None:
                show["_local_progress"] = app_core.get_local_progress(show, "sub")
            if show.get("_local_episode_label") is None:
                show["_local_episode_label"] = app_core.get_local_episode_label(show, "sub")
            build_info_panel(show, "sub", w, parts, local_only=True)


        online_badge = "\033[38;2;225;85;85m[Offline]\033[0m │ " if app_core.is_confirmed_offline() else ""
        line = f"{online_badge}Downloaded anime  │  {download_dir}  │  Enter=episodes  Del=delete title  Esc=quit"
        if 0 <= si < len(valid_titles):
            title, data = valid_titles[si]
            show = data.get("metadata", {})
            parts.append(_poster_footer_line(show, line, w))
        else:
            parts.append(f"{_C_HINT}{_truncate_display(line, max(1, w - 1))}{_RST}")
        return "\n".join(parts)

    def _folders_top_hdr(si):
        if 0 <= si < len(valid_titles):
            title, data = valid_titles[si]
            show = data.get("metadata", {})
            ui.hovered_show_id = show.get("_id")
            ui.hovered_show_obj = show
            import media_cli.app_core as core
            core._hovered_show_id = ui.hovered_show_id
            poster = _get_poster(show)
            if poster:
                return poster
        return ""

    def _delete_title(si):
        nonlocal valid_titles, folder_opts, db
        if 0 <= si < len(valid_titles):
            title, data = valid_titles[si]
            folder_path = os.path.join(download_dir, title)
            if os.path.isdir(folder_path):
                shutil.rmtree(folder_path, ignore_errors=True)
            if title in db["shows"]:
                del db["shows"][title]
            save_downloads_db(db)
            valid_titles.pop(si)
            folder_opts = _build_folder_opts()
        return folder_opts, _folders_hdr(0)

    folder_idx = tui_pick(
        flags, ui, "Downloads", folder_opts,
        header_fn=_folders_hdr,
        top_header_fn=_folders_top_hdr,
        delete_fn=_delete_title,
        delete_prompt="Delete this downloaded title? y/N",
        info_fn=make_info_fn(lambda: [d.get("metadata", {}) for _, d in valid_titles], ui),
        help_dict=picker_help("Open details", "Quit", "Quit", delete_label="Delete title")
    )
    if folder_idx < 0:
        return "QUIT"

    title, data = valid_titles[folder_idx]
    folder_path = os.path.join(download_dir, title)
    try:
        raw_files = os.listdir(folder_path)
        files = [os.path.join(folder_path, f) for f in raw_files if is_valid_download_video(os.path.join(folder_path, f))]
        files.sort(key=offline_file_sort_key)
    except OSError:
        files = []

    if not files:
        warn(f"No downloaded videos found in {title}.")
        return "DOWNLOADS"

    # Inject episodes into show metadata so the streaming UI can read them
    show = data.get("metadata", {})
    show["watched_episodes"] = data.get("watched_episodes", [])
    episodes_list = []
    if "_episode_labels" not in show or not isinstance(show.get("_episode_labels"), dict):
        show["_episode_labels"] = {}

    ms._download_files = {} # map ep_str -> filepath
    for filepath in files:
        _, _, clean_id, raw_label = extract_offline_episode_info(filepath)
        ep_str = clean_id
        episodes_list.append(ep_str)
        ms._download_files[ep_str] = filepath
        if raw_label:
            ms._download_files[raw_label] = filepath
            show["_episode_labels"][ep_str] = raw_label
            show["_episode_labels"][raw_label] = raw_label
        ms._download_files[os.path.basename(filepath)] = filepath

    show["_folder_name"] = title
    show["_downloaded_episodes"] = episodes_list

    # Check if catalog episode metadata exists in show or history
    catalog_eids = show.get("_episode_ids")
    if not catalog_eids:
        from media_cli.core.storage import load_history
        from media_cli.domain.matching import is_same_show
        for h in load_history():
            h_show = h.get("show") or {}
            if is_same_show(h_show, show):
                for k in (
                    "_id", "id", "thumbnail", "banner", "description",
                    "type", "format", "status", "season", "airedStart", "airedEnd",
                    "episodeCount", "availableEpisodes", "score", "genres", "tags",
                    "aniListId", "malId", "_provider", "_provider_id", "_provider_name",
                    "_display_name", "_allanime_name", "englishName", "romajiName",
                    "anilistMatch", "_episodes_map", "_episodes_map_by_ttype",
                    "_episodes_by_number", "_episodes_by_number_by_ttype",
                    "_episode_ids", "_episode_labels", "_episode_ids_by_ttype",
                ):
                    if h_show.get(k) and (k not in show or not show.get(k)):
                        show[k] = h_show[k]
                if h_show.get("episodeCount"):
                    show["originalEpisodeCount"] = h_show["episodeCount"]
                catalog_eids = list(show.get("_episode_ids") or [])
                break


    if (not catalog_eids or len(catalog_eids) <= len(episodes_list)) and app_core.is_online():
        try:
            saved_eids = show.pop("_episode_ids", None)
            saved_state = show.pop("_episode_catalog_state", None)
            online_eids = app_core.ensure_episode_ids(show, ttype)
            if online_eids and len(online_eids) > len(episodes_list):
                catalog_eids = list(online_eids)
                data["metadata"] = show
                save_downloads_db(db)
            else:
                if saved_eids:
                    show["_episode_ids"] = saved_eids
                if saved_state:
                    show["_episode_catalog_state"] = saved_state
        except Exception:
            pass

    from media_cli.domain.episodes import clean_episode_identifier, parse_episode_dual_numbers

    def _safe_ep_num(val):
        if val is None:
            return None
        s = str(val).strip()
        prim, _ = parse_episode_dual_numbers(s)
        clean = (prim or clean_episode_identifier(s) or s).lstrip("0") or "0"
        try:
            return int(float(clean))
        except (ValueError, TypeError):
            return None

    known_nums = set()
    for ep in episodes_list:
        n = _safe_ep_num(ep)
        if n is not None:
            known_nums.add(n)
    for w in show.get("watched_episodes", []):
        n = _safe_ep_num(w)
        if n is not None:
            known_nums.add(n)
    lp = _safe_ep_num(show.get("_local_progress"))
    if lp is not None and lp > 0:
        known_nums.add(lp)
    ap = _safe_ep_num(show.get("_anilist_progress"))
    if ap is not None and ap > 0:
        known_nums.add(ap)
    ec = _safe_ep_num(show.get("episodeCount") or show.get("originalEpisodeCount"))
    if ec is not None and ec > 0:
        known_nums.add(ec)
    if catalog_eids:
        for eid in catalog_eids:
            lbl = (show.get("_episode_labels") or {}).get(eid, show.get("_episode_labels", {}).get(str(eid)))
            n = _safe_ep_num(lbl or eid)
            if n is not None:
                known_nums.add(n)

    max_num = max(known_nums) if known_nums else len(episodes_list)

    if not catalog_eids:
        ep_map = {}
        for ep in episodes_list:
            n = _safe_ep_num(ep)
            if n is not None:
                ep_map[n] = ep
        catalog_eids = []
        for n in range(1, max_num + 1):
            eid = ep_map.get(n, str(n))
            catalog_eids.append(eid)
            if eid not in show["_episode_labels"] and str(eid) not in show["_episode_labels"]:
                show["_episode_labels"][str(eid)] = f"Episode {n}"
    else:
        cat_nums = set()
        for eid in catalog_eids:
            lbl = show["_episode_labels"].get(eid, show["_episode_labels"].get(str(eid)))
            n = _safe_ep_num(lbl or eid)
            if n is not None:
                cat_nums.add(n)
        min_cat = min(cat_nums) if cat_nums else 1
        if min_cat > 1:
            backfill = []
            for n in range(1, min_cat):
                eid = str(n)
                backfill.append(eid)
                if eid not in show["_episode_labels"]:
                    show["_episode_labels"][eid] = f"Episode {n}"
            catalog_eids = backfill + catalog_eids

    from media_cli.domain.episodes import build_episode_map
    raw_eps = [
        {"id": eid, "label": show["_episode_labels"].get(eid) or show["_episode_labels"].get(str(eid)) or eid}
        for eid in catalog_eids
    ]
    ep_map, by_num = build_episode_map(raw_eps, translation_type=ttype)
    show["_episodes_map"] = ep_map
    show["_episodes_by_number"] = by_num
    show["_episode_ids"] = list(ep_map.keys())
    show["_episode_labels"] = {eid: ep["label"] for eid, ep in ep_map.items()}
    show["_episode_ids_ttype"] = ttype
    show["_episode_catalog_state"] = "loaded"
    if "availableEpisodesDetail" not in show:
        show["availableEpisodesDetail"] = {}
    show["availableEpisodesDetail"][ttype] = show["_episode_ids"]
    show["availableEpisodes"] = {ttype: len(show["_episode_ids"])}
    if "originalEpisodeCount" not in show and "episodeCount" in show:
        show["originalEpisodeCount"] = show["episodeCount"]
    show["episodeCount"] = max(show.get("episodeCount") or 0, len(show["_episode_ids"]))

    if show.get("_local_progress") is None:
        show["_local_progress"] = app_core.get_local_progress(show, ttype)
    if show.get("_local_episode_label") is None:
        show["_local_episode_label"] = app_core.get_local_episode_label(show, ttype)

    for eid in show["_episode_ids"]:

        lbl = show["_episode_labels"].get(eid, show["_episode_labels"].get(str(eid)))
        if lbl:
            prim, sec = parse_episode_dual_numbers(str(lbl))
            clean = (prim or clean_episode_identifier(str(lbl)) or str(lbl)).lstrip("0") or "0"
            for ep_key, fp in list(ms._download_files.items()):
                f_prim, f_sec = parse_episode_dual_numbers(str(ep_key))
                f_clean = (f_prim or clean_episode_identifier(str(ep_key)) or str(ep_key)).lstrip("0") or "0"
                if f_clean == clean or (sec and sec == f_sec) or ep_key == lbl:
                    ms._download_files[eid] = fp
                    ms._download_files[str(eid)] = fp
                    break

    # Setup state machine variables for the selected offline anime
    ms._is_downloads = True
    ms._download_dir = folder_path
    ms.show_title = title
    ms.show_id = show.get("_id")
    ms.auto_download_next = cfg.get("auto_download_next", False)
    ms.auto_delete_watched = cfg.get("auto_delete_watched", False)
    ms.auto_delete_buffer = int(cfg.get("auto_delete_buffer", 1))
    set_ui_context(ui, show, ttype)
    ui.search_prev_state = "DOWNLOADS"
    ui.action_prev_state = "DOWNLOADS"
    ui.ep_prev_state = "DETAILS"

    return "DETAILS"
