import os
import re
import shutil
import sys
import logging
from copy import deepcopy

from .. import app_core
from ..app_core import _get_poster, _poster_footer_line, build_info_panel, make_info_fn, set_ui_context, warn
from ..core.storage import get_default_download_dir, is_valid_download_video, load_downloads_db, save_downloads_db
from ..core.terminal import sanitize_terminal_text
from ..core.workers import DaemonExecutor
from ..media.episode_identity import (
    download_identity_conflicts, episode_file_fingerprint,
    read_episode_identities, read_episode_identity,
)
from ..ui.help import picker_help
from ..ui.picker import tui_pick
from ..ui.actions import ActionBindings
from ..ui.background_loading import BackgroundLoadingStatus
from ..ui.footer import Footer, FooterStatus, StatusKind
from ..ui.loading_model import LoadingInteraction, LoadingOwner, LoadingPlacement, LoadingTaskSpec
from ..ui.spinner import spinner_from_config
from .footer import context_footer

_C_HINT = "\033[38;5;244m"
_RST = "\033[0m"
VIDEO_EXTS = ('.mp4', '.mkv', '.avi', '.ts', '.webm')


def _saved_episode_label(filepath: str, show_data: dict, *, identity_reader=None) -> str:
    labels = (show_data.get("metadata") or {}).get("_episode_labels") or {}
    target = os.path.realpath(os.path.expanduser(filepath))
    candidates = set()
    for identifier, files in (show_data.get("episode_files") or {}).items():
        paths = (files,) if isinstance(files, str) else files or ()
        if any(os.path.realpath(os.path.expanduser(str(path))) == target for path in paths):
            label = str(labels.get(str(identifier)) or identifier)
            if not download_identity_conflicts(label, filepath, identity_reader=identity_reader):
                candidates.add(label)
    return next(iter(candidates)) if len(candidates) == 1 else ""


def extract_offline_episode_info(
    filepath: str, *, show_data: dict | None = None, identity_reader=None,
) -> tuple[float, float, str, str]:
    """Extract (primary_num, secondary_num, clean_ep_id, raw_label) from filepath."""
    identity = (identity_reader or read_episode_identity)(filepath)
    labels = ((show_data or {}).get("metadata") or {}).get("_episode_labels") or {}
    raw_label = ""
    if identity is not None:
        raw_label = identity.episode or str(labels.get(identity.episode_id) or identity.episode_id)
    if not raw_label and show_data:
        raw_label = _saved_episode_label(filepath, show_data, identity_reader=identity_reader)
    if raw_label:
        return _offline_label_info(raw_label)

    stem = os.path.splitext(os.path.basename(filepath))[0]
    m = re.search(r'\b(?:episodes?|ep)\b[-_.\s]*([^\n]+)$', stem, re.IGNORECASE)
    if m:
        raw_label = m.group(1).strip()
    else:
        m2 = re.search(r'(?:^|[-_.\s])(ova(?:\s*\d+)?|movie(?:\s*\d+)?|\d+(?:\.\d+)?(?:\[\d+\])?)\s*$', stem, re.IGNORECASE)
        raw_label = m2.group(1).strip() if m2 else stem

    return _offline_label_info(raw_label)


def _offline_label_info(raw_label: str) -> tuple[float, float, str, str]:
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


def offline_file_sort_key(filepath: str, *, show_data: dict | None = None, identity_reader=None) -> tuple[float, float, str]:
    prim_num, sec_num, _, _ = extract_offline_episode_info(filepath, show_data=show_data, identity_reader=identity_reader)
    return (prim_num, sec_num, os.path.basename(filepath))


def _scan_path_kind(path, *, directory=False):
    """True/False for confirmed filesystem state, None when inspection failed."""
    import stat
    try:
        mode = os.stat(path).st_mode
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return None
    return stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)


def _scan_downloads(db, download_dir, ttype, *, persist=True):
    """Validate a caller-owned database snapshot; detached workers do not persist."""
    shows = db.get("shows", {})

    dir_exists = _scan_path_kind(download_dir, directory=True)
    if dir_exists is None:
        logging.getLogger(__name__).debug("Downloads root unreadable; keeping saved records")
        return db, list(shows.items()), False
    try:
        root_names = os.listdir(download_dir) if dir_exists else []
    except OSError:
        logging.getLogger(__name__).debug("Downloads root listing failed; keeping saved records")
        return db, list(shows.items()), False
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

    # Probe across folders together; keep failures local to this scan only.
    folder_files = {}
    folders = [os.path.join(download_dir, title) for title in shows]
    if dir_exists:
        folders.extend(os.path.join(download_dir, name) for name in root_names)
    for folder in dict.fromkeys(folders):
        exists = _scan_path_kind(folder, directory=True)
        if exists is None:
            folder_files[folder] = None
            continue
        if not exists:
            continue
        try:
            folder_files[folder] = [
                os.path.join(folder, name) for name in os.listdir(folder)
                if is_valid_download_video(os.path.join(folder, name), raise_on_error=True)
            ]
        except OSError:
            folder_files[folder] = None
    stamps = {path: episode_file_fingerprint(path) for files in folder_files.values() if files is not None for path in files}
    identities = read_episode_identities(stamps)

    def scan_identity(path):
        if (path in identities and stamps[path] is not None
                and stamps[path] == episode_file_fingerprint(path)):
            return identities[path]
        return read_episode_identity(path)

    for title, data in list(shows.items()):
        folder_path = os.path.join(download_dir, title)
        exists = _scan_path_kind(folder_path, directory=True)
        if exists is None:
            valid_titles.append((title, data))
            continue
        if not exists:
            from media_cli.media.download import sanitize_filename
            alt_path = os.path.join(download_dir, sanitize_filename(title))
            alternative = _scan_path_kind(alt_path, directory=True)
            if alternative is None:
                valid_titles.append((title, data))
                continue
            if alternative:
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

        if folder_files.get(folder_path) is None:
            logging.getLogger(__name__).debug("Download folder unreadable; keeping saved episode records")
            valid_titles.append((title, data))
            continue

        try:
            actual_files = []
            for path in folder_files.get(folder_path, []):
                present = _scan_path_kind(path)
                if present is None:
                    raise OSError("File inspection failed")
                if present:
                    actual_files.append(path)
            actual_files.sort(key=lambda path: offline_file_sort_key(path, show_data=data, identity_reader=scan_identity))
        except OSError:
            valid_titles.append((title, data))
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

        discovered_eps = []
        for f in actual_files:
            _, _, clean_id, _ = extract_offline_episode_info(f, show_data=data, identity_reader=scan_identity)
            if clean_id and clean_id not in discovered_eps:
                discovered_eps.append(clean_id)

        valid_eps = discovered_eps

        if valid_eps != data.get("episodes", []):
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

    if dirty and persist:
        save_downloads_db(db)

    # Discovery scan: auto-import folders on disk that aren't tracked in the DB
    if os.path.isdir(download_dir):
        for folder_name in root_names:
            folder_path = os.path.join(download_dir, folder_name)
            if not os.path.isdir(folder_path) or folder_name in shows:
                continue
            try:
                if folder_path in folder_files and folder_files[folder_path] is None:
                    continue
                files = list(folder_files[folder_path]) if folder_path in folder_files else [
                    os.path.join(folder_path, f)
                    for f in os.listdir(folder_path)
                    if is_valid_download_video(os.path.join(folder_path, f))
                ]
                files = [path for path in files if os.path.isfile(path)]
                files.sort(key=lambda path: offline_file_sort_key(path, identity_reader=scan_identity))
            except OSError:
                continue
            if not files:
                continue
            episodes = []
            for f in files:
                _, _, clean_id, _ = extract_offline_episode_info(f, identity_reader=scan_identity)
                if clean_id and clean_id not in episodes:
                    episodes.append(clean_id)

            # Try to fetch real metadata for the discovered folder
            metadata = {"name": folder_name, "episodeCount": len(episodes)}

            data = {"metadata": metadata, "episodes": episodes}
            shows[folder_name] = data
            valid_titles.append((folder_name, data))
            dirty = True
        if dirty and persist:
            save_downloads_db(db)

    return db, valid_titles, dirty


def handle_downloads_state(flags, ui, ms, cfg, args, ttype, resolveTracking):
    db = load_downloads_db()
    raw_arg_dir = getattr(args, "download_dir", None)
    download_dir = raw_arg_dir.strip() if isinstance(raw_arg_dir, str) and raw_arg_dir.strip() else str(cfg.get("download_dir") or "").strip()
    download_dir = os.path.abspath(os.path.expanduser(download_dir or get_default_download_dir()))
    executor = None
    scan_future = None
    scan_ready = False
    scan_error = False
    initial_db = deepcopy(db)
    loading = BackgroundLoadingStatus(
        LoadingTaskSpec("scan", LoadingOwner("downloads", 0), "Checking downloaded files...",
                        LoadingInteraction.BACKGROUND, LoadingPlacement.FOOTER, ActionBindings(()), delay=0),
        spinner_from_config(cfg),
    )
    if flags.plain_mode or not sys.stdin.isatty():
        db, valid_titles, _ = _scan_downloads(db, download_dir, ttype)
        scan_ready = True
    else:
        valid_titles = list(db.get("shows", {}).items())
        executor = DaemonExecutor(max_workers=1, max_pending=1)
        scan_future = executor.submit(_scan_downloads, deepcopy(db), download_dir, ttype, persist=False)

    def _poll_scan():
        nonlocal db, valid_titles, folder_opts, scan_future, scan_ready, scan_error
        if scan_future is None or not scan_future.done():
            return False
        completed, scan_future = scan_future, None
        try:
            scanned_db, scanned_titles, dirty = completed.result()
        except Exception:
            logging.getLogger(__name__).exception("Downloads validation failed")
            scan_error = True
            return True
        latest = load_downloads_db()
        if latest == initial_db:
            db = scanned_db
            if dirty:
                save_downloads_db(db)
        else:
            # Other downloads/progress may have changed while this scan ran.
            db = latest
            logging.getLogger(__name__).debug("Downloads scan skipped persistence: database changed during validation")
            for title, data in scanned_titles:
                current = latest.get("shows", {}).get(title)
                if current is not None:
                    data["watched_episodes"] = deepcopy(current.get("watched_episodes", []))
                    for key in ("_local_progress", "_local_episode_label"):
                        data.setdefault("metadata", {}).pop(key, None)
        valid_titles = scanned_titles
        folder_opts = _build_folder_opts()
        scan_ready = True
        return True

    if scan_ready and not valid_titles:
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


        show_for_footer = {}
        if 0 <= si < len(valid_titles):
            _, data = valid_titles[si]
            show_for_footer = data.get("metadata", {})
        line = context_footer(
            show_for_footer,
            ui,
            ms,
            f"{download_dir}  │  " + ("Enter=episodes  Del=delete title  Esc=quit" if scan_ready else "Esc=quit"),
            offline=app_core.is_confirmed_offline(),
            mode="Downloads",
        )
        if not (0 <= si < len(valid_titles)):
            parts.extend(("", "", ""))
        status = loading.read(not scan_ready and not scan_error)
        statuses = (FooterStatus("Couldn't check downloads. Reopen to retry.", StatusKind.ERROR),) if scan_error else ((status,) if status else ())
        footer = Footer(statuses=statuses, context=(sanitize_terminal_text(line),))
        parts.append(_poster_footer_line(show_for_footer, footer, w))
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
        if not scan_ready:
            return folder_opts, _folders_hdr(si)
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

    def _live_folders(_query):
        _poll_scan()
        return list(folder_opts), "", scan_ready or scan_error

    try:
        folder_idx = tui_pick(
            flags, ui, "Downloads", folder_opts,
            header_fn=_folders_hdr, top_header_fn=_folders_top_hdr,
            delete_fn=_delete_title, can_delete_fn=lambda _: scan_ready,
            select_fn=lambda _: scan_ready,
            delete_prompt="Delete this downloaded title? y/N",
            info_fn=make_info_fn(lambda: [d.get("metadata", {}) for _, d in valid_titles], ui),
            help_dict=picker_help("Open details", "Quit", "Quit", delete_label="Delete title"),
            live_fn=_live_folders, tick_fn=lambda: scan_future is not None,
            item_key_fn=lambda index: valid_titles[index][0],
        )
    finally:
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        loading.read(False)
    if folder_idx < 0:
        if scan_error:
            warn("Couldn't check downloads. Reopen to retry.")
        elif scan_ready and not valid_titles:
            warn(f"No downloaded videos found in {download_dir}.")
        return "QUIT"

    title, data = valid_titles[folder_idx]
    folder_path = os.path.join(download_dir, title)
    try:
        raw_files = os.listdir(folder_path)
        files = [os.path.join(folder_path, f) for f in raw_files if is_valid_download_video(os.path.join(folder_path, f))]
        files.sort(key=lambda path: offline_file_sort_key(path, show_data=data))
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
        _, _, clean_id, raw_label = extract_offline_episode_info(filepath, show_data=data)
        ep_str = clean_id
        episodes_list.append(ep_str)
        ms._download_files[ep_str] = filepath
        identity = read_episode_identity(filepath)
        if identity is not None and identity.episode_id:
            ms._download_files[identity.episode_id] = filepath
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

    episode_files = data.setdefault("episode_files", {})
    files_dirty = False
    for eid in show["_episode_ids"]:
        lbl = show["_episode_labels"].get(eid, show["_episode_labels"].get(str(eid), eid))
        fp = (
            ms._download_files.get(eid)
            or ms._download_files.get(str(eid))
            or ms._download_files.get(str(lbl))
        )
        if not fp:
            continue
        abs_fp = os.path.abspath(os.path.expanduser(str(fp)))
        if not os.path.isfile(abs_fp):
            continue
        key = str(eid)
        if episode_files.get(key) != [abs_fp]:
            episode_files[key] = [abs_fp]
            files_dirty = True
    if files_dirty:
        save_downloads_db(db)

    # Setup state machine variables for the selected offline anime
    ms._is_downloads = True
    ms._download_dir = folder_path
    ms.show_title = title
    ms.show_id = show.get("_id") or show.get("id") or show.get("_folder_name") or title
    ms.auto_download_next = cfg.get("auto_download_next", "adaptive")
    ms.auto_delete_watched = cfg.get("auto_delete_watched", False)
    ms.auto_delete_buffer = int(cfg.get("auto_delete_buffer", 1))
    set_ui_context(ui, show, ttype)
    ui.search_prev_state = "DOWNLOADS"
    ui.action_prev_state = "DOWNLOADS"
    ui.ep_prev_state = "DETAILS"

    return "DETAILS"
