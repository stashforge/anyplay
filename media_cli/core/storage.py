"""Local persistence: prefs, watch/search history, resume state, config.

Owns every read/write of on-disk application state plus the in-memory
caches in front of it.  Pure data transforms stay in ``state/`` and
``domain/history.py``; this module is the stateful layer that decides
*when* to touch disk (and owns the incognito no-write policy).

Dependencies point downward only:

    storage -> state.paths / state.io / state.config / state.preferences
               state.lists / state.secrets / domain.history
    storage -> core.reporting (warn/debug_warn)
    storage -> context.FLAGS (incognito/debug mode)

Two facts about shows live above this layer and are injected instead of
imported (see :func:`configure`):

* ``episode_ids_fn(show, ttype)`` — provider episode catalogs (needed to
  map an AniList progress number onto a provider episode ID).
* ``prepare_display_state_fn(show, ttype)`` — display-state priming used
  by the history formatting helpers.

Paths are read from ``state.paths`` at *call time* (``paths.HISTORY_PATH``,
never ``from ..state.paths import HISTORY_PATH``), so tests can point the
whole layer at a temp directory by patching ``media_cli.state.paths``
and calling :func:`reset_caches`.
"""

import os
import shutil
import tempfile
import time
import traceback

from ..context import FLAGS
from ..domain import history as history_domain
from ..state import lists as list_state
from ..state import paths
from ..state import preferences as preference_state
from ..state import secrets as secret_state
from ..state.config import (
    load_config_file,
    save_config_file,
)
from ..state.io import atomic_write_json, write_private_text
from .reporting import debug_warn, warn

# ---------------------------------------------------------------------------
# Runtime mode + injected upper-layer helpers
# ---------------------------------------------------------------------------

_episode_ids_fn = None
_prepare_display_state_fn = None


def is_incognito():
    return FLAGS.incognito_mode


def configure(*, episode_ids_fn=None, prepare_display_state_fn=None):
    """Inject the upper-layer callbacks storage needs (see module docstring)."""
    global _episode_ids_fn, _prepare_display_state_fn
    if episode_ids_fn is not None:
        _episode_ids_fn = episode_ids_fn
    if prepare_display_state_fn is not None:
        _prepare_display_state_fn = prepare_display_state_fn


def _episode_ids(show, ttype):
    return _episode_ids_fn(show, ttype) if _episode_ids_fn else []


def _prepare_display_state(show, ttype="sub", sync_enabled=None):
    if _prepare_display_state_fn:
        return _prepare_display_state_fn(show, ttype, sync_enabled)
    return show


# ---------------------------------------------------------------------------
# In-memory caches + reset API
# ---------------------------------------------------------------------------

_prefs_cache = None  # playback prefs
_history_cache = None  # watch history
_search_history_cache = None  # search history
_incognito_cache_dir = None


def reset_caches():
    """Drop every in-memory cache (tests; also safe after path changes)."""
    global _prefs_cache, _history_cache, _search_history_cache
    _prefs_cache = None
    _history_cache = None
    _search_history_cache = None


# ---------------------------------------------------------------------------
# Incognito cover-cache policy
# ---------------------------------------------------------------------------

def cleanup_incognito_cache():
    global _incognito_cache_dir
    if _incognito_cache_dir:
        shutil.rmtree(_incognito_cache_dir, ignore_errors=True)
        _incognito_cache_dir = None


from ..constants import APP_NAME


def cover_cache_dir():
    """Return the directory to write new cover downloads into."""
    global _incognito_cache_dir
    if not is_incognito():
        path = os.path.expanduser(f"~/.cache/{APP_NAME}/covers/")
        try:
            os.makedirs(path, exist_ok=True)
        except Exception:
            pass
        return path
    if not _incognito_cache_dir:
        _incognito_cache_dir = tempfile.mkdtemp(prefix=f"{APP_NAME}-incognito-")
        os.chmod(_incognito_cache_dir, 0o700)
    return _incognito_cache_dir


def cover_read_cache_dirs():
    """Return all directories to read existing covers from (temp first in incognito, then main cache)."""
    dirs = []
    global _incognito_cache_dir
    if is_incognito() and _incognito_cache_dir and os.path.exists(_incognito_cache_dir):
        dirs.append(_incognito_cache_dir)
    main_dir = os.path.expanduser(f"~/.cache/{APP_NAME}/covers/")
    if os.path.exists(main_dir):
        dirs.append(main_dir)
    legacy_dir = os.path.expanduser("~/.cache/allmanga-cli/covers/")
    if os.path.exists(legacy_dir) and legacy_dir not in dirs:
        dirs.append(legacy_dir)
    return dirs


# ---------------------------------------------------------------------------
# Low-level write helpers
# ---------------------------------------------------------------------------

def _atomic_write_json(path, data, indent=None):
    return atomic_write_json(
        path, data, indent=indent, disabled=is_incognito()
    )


def redact_sensitive_text(content):
    # set by configure_redactor(); identity until then
    return content


def configure_redactor(fn):
    """Inject the sensitive-text redactor used for private logs."""
    global redact_sensitive_text
    redact_sensitive_text = fn


def write_private_log(filename, content):
    if is_incognito() and not FLAGS.debug_mode:
        return None
    return write_private_text(paths.LOG_DIR, filename, redact_sensitive_text(content))


def write_exception_log(filename):
    return write_private_log(filename, traceback.format_exc())


def _preserve_invalid_state_file(path, label):
    if is_incognito():
        debug_warn(f"Invalid {label.lower()} left untouched in incognito mode", ValueError(path))
        return
    backup_path = f"{path}.bad-{int(time.time())}"
    try:
        os.replace(path, backup_path)
        warn(f"{label} was invalid; moved it to {backup_path}")
    except Exception as move_error:
        debug_warn(f"Failed to move invalid {label.lower()} aside", move_error)


# ---------------------------------------------------------------------------
# Playback prefs
# ---------------------------------------------------------------------------

def load_prefs() -> dict:
    global _prefs_cache
    if _prefs_cache is not None:
        return _prefs_cache
    if not os.path.exists(paths.PLAYBACK_PATH):
        _prefs_cache = {}
        return _prefs_cache
    try:
        import copy
        import json
        with open(paths.PLAYBACK_PATH) as f:
            raw = json.load(f)
            _prefs_cache = copy.deepcopy(raw) if is_incognito() else raw
            return _prefs_cache
    except Exception as e:
        debug_warn("Failed to load playback prefs", e)
        if not is_incognito():
            _preserve_invalid_state_file(paths.PLAYBACK_PATH, "Playback prefs")
        _prefs_cache = {}
        return _prefs_cache


def save_prefs(prefs: dict):
    global _prefs_cache
    _prefs_cache = prefs
    if is_incognito():
        return True
    _atomic_write_json(paths.PLAYBACK_PATH, prefs)
    return True


def get_preferred_mirror(show_id: str) -> dict:
    return preference_state.preferred_mirror(load_prefs(), show_id)


def toggle_preferred_mirror(show_id: str, sname: str, resolution: str):
    save_prefs(preference_state.toggle_preferred_mirror(
        load_prefs(), show_id, sname, resolution
    ))


def get_episode_order(show_id: str, default_order: str) -> str:
    return preference_state.episode_order(
        load_prefs(), show_id, default_order
    )


def toggle_episode_order(show_id: str, default_order: str) -> str:
    prefs = load_prefs()
    new_order = preference_state.toggle_episode_order(
        prefs, show_id, default_order
    )
    save_prefs(prefs)
    return new_order


def get_title_sync_preference(show):
    if not show:
        return None
    return preference_state.title_sync_preference(load_prefs(), show)


def get_title_sync(show) -> bool:
    return get_title_sync_preference(show) is True


def set_title_sync(show, enabled: bool):
    if not show:
        return
    if is_incognito():
        show["_sync_enabled"] = False
        return
    save_prefs(preference_state.set_title_sync(
        load_prefs(), show, enabled
    ))
    show["_sync_enabled"] = bool(enabled)
    try:
        from media_cli.ui.info_panel import invalidate_panel_cache
        invalidate_panel_cache()
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Resume / pending completion / AniList match prefs
# ---------------------------------------------------------------------------

def get_resume_time(show_id: str, ep: int) -> int:
    return preference_state.resume_time(load_prefs(), show_id, ep)


def save_resume_time(show_id: str, ep: int, time: int):
    save_prefs(preference_state.save_resume_time(
        load_prefs(), show_id, ep, time
    ))


def get_pending_completion(show_id: str) -> dict:
    return preference_state.pending_completion(load_prefs(), show_id)


def save_pending_completion(show_id: str, ep, progress_ep: int, next_ep, time_pos: int, duration: int):
    save_prefs(preference_state.save_pending_completion(
        load_prefs(),
        show_id,
        ep,
        progress_ep,
        next_ep,
        time_pos,
        duration,
    ))


def clear_pending_completion(show_id: str):
    save_prefs(preference_state.clear_pending_completion(
        load_prefs(), show_id
    ))


def get_al_match(al_id: str) -> dict:
    return preference_state.anilist_match(load_prefs(), al_id)


def save_al_match(al_id: str, aa_show: dict):
    if is_incognito():
        return
    save_prefs(preference_state.save_anilist_match(
        load_prefs(), al_id, aa_show
    ))


def get_source_anilist_match(show_id: str) -> dict:
    return preference_state.source_anilist_match(
        load_prefs(), show_id
    )


def save_source_anilist_match(aa_show: dict, anilist_show: dict):
    if is_incognito():
        return
    save_prefs(preference_state.save_source_anilist_match(
        load_prefs(), aa_show, anilist_show
    ))

def get_source_anilist_matches(show_ids: list[str]) -> dict:
    prefs = load_prefs()
    return {sid: preference_state.source_anilist_match(prefs, sid) for sid in show_ids}

def save_source_anilist_matches(pairs: list[tuple[dict, dict]]):
    if is_incognito() or not pairs:
        return
    prefs = load_prefs()
    for aa_show, anilist_show in pairs:
        prefs = preference_state.save_source_anilist_match(prefs, aa_show, anilist_show)
    save_prefs(prefs)


# ---------------------------------------------------------------------------
# Watch history
# ---------------------------------------------------------------------------

ANILIST_HISTORY_STRIP_KEYS = {
    "_anilist_context",
    "_anilist_progress",
    "_anilist_list",
    "_anilist_updated_at",
    "_anilist_media",
    "_progress_authority",
    "_sync_conflict",
    "_sync_enabled",
}

HISTORY_SHOW_STRIP_KEYS = ANILIST_HISTORY_STRIP_KEYS | {
    "_poster_raw",
    "_poster_status",
    "_poster_status_time",
    "_poster_failed",
    "availableEpisodesDetail",
    "_episode_catalog_state",
    "_allanime_checked_at",
    "_local_progress",
    "_local_episode_label",
    "watched_episodes",
    "_folder_name",
    "_download_files",
    "_is_downloads",
    "_enrichment_in_progress",
    # "_enrichment_attempted" - MUST persist across sessions to prevent re-enrichment
    "_anilist_search_checked",
    "_hist_backfill_checked",
}


def sanitize_show_for_history(show):
    stored = dict(show or {})
    for key in HISTORY_SHOW_STRIP_KEYS:
        stored.pop(key, None)
    prov = stored.get("_provider") or stored.get("provider")
    if not prov:
        from ..providers.shared.models import title_provider_key
        prov = title_provider_key(show, default="")
    if prov:
        stored["_provider"] = str(prov)
    stored.pop("provider", None)
    return stored


def sanitize_history_list(history):
    clean_history = []
    for entry in history:
        clean_entry = {**entry, "show": sanitize_show_for_history(entry.get("show"))}
        show = clean_entry["show"]
        show.pop("_local_progress", None)
        show.pop("_local_episode_label", None)
        if "_anilist_id" in show:
            if "aniListId" not in show:
                show["aniListId"] = str(show["_anilist_id"])
            del show["_anilist_id"]
        show.pop("provider", None)
        clean_history.append(clean_entry)
    return clean_history


def load_history():
    global _history_cache
    if _history_cache is not None:
        return _history_cache
    try:
        raw = list_state.load_json_list(
            paths.HISTORY_PATH,
            _history_cache,
        )
        sanitized = sanitize_history_list(raw)
        if is_incognito():
            import copy
            _history_cache = copy.deepcopy(sanitized)
        else:
            _history_cache = sanitized
        return _history_cache
    except Exception as e:
        debug_warn("Failed to load watch history", e)
        if not is_incognito():
            _preserve_invalid_state_file(paths.HISTORY_PATH, "Watch history")
        _history_cache = []
        return _history_cache


def get_history_entry(show, ttype="sub"):
    show_id = str((show or {}).get("_id") or (show or {}).get("id") or "")
    from media_cli.domain.matching import is_same_show
    for entry in load_history():
        if entry.get("translation_type", "sub") != ttype:
            continue
        entry_show = entry.get("show", {})
        e_id = str(entry_show.get("_id") or entry_show.get("id") or "")
        if show_id and e_id and e_id == show_id:
            return entry
        if is_same_show(entry_show, show):
            return entry
    return None



def get_local_progress(show, ttype="sub"):
    return history_domain.local_progress(
        load_history(),
        show,
        ttype,
    )


def get_local_episode_label(show, ttype="sub"):
    entry = get_history_entry(show, ttype)
    if entry and "episode" in entry:
        eid = entry["episode"]
        if str(eid) in ("0", "0.0"):
            return "0"
        labels = (show or {}).get("_episode_labels") or (entry.get("show") or {}).get("_episode_labels") or {}
        if labels and str(eid) in labels:
            return str(labels[str(eid)])
        from ..domain.episodes import clean_episode_identifier
        if str(eid).startswith(("http://", "https://", "/")):
            cleaned = clean_episode_identifier(str(eid))
            if cleaned:
                return cleaned
        return str(eid)
    return None


def get_last_synced_progress(show, ttype="sub"):
    entry = get_history_entry(show, ttype)
    if not entry:
        return None
    value = entry.get("last_synced_progress")
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def episode_id_for_progress(show, ttype, progress):
    import decimal
    try:
        numeric_prog = decimal.Decimal(str(progress))
        if numeric_prog < 0:
            numeric_prog = decimal.Decimal("0")
    except decimal.InvalidOperation:
        numeric_prog = None

    if numeric_prog == decimal.Decimal("0"):
        return "0"

    episode_ids = _episode_ids(show, ttype)
    if not episode_ids:
        return str(progress)

    if str(progress) in episode_ids:
        return str(progress)

    labels = show.get("_episode_labels") or {}
    if labels:
        from ..domain.episodes import clean_episode_identifier
        clean_target = str(clean_episode_identifier(str(progress)) or progress)
        for eid, lbl in labels.items():
            if str(clean_episode_identifier(str(lbl)) or lbl) == clean_target or str(lbl) == str(progress) or str(eid) == str(progress):
                return str(eid)

    from ..domain.episodes import episode_index_for_id
    ep_idx = episode_index_for_id(episode_ids, progress, labels=labels)
    if ep_idx is not None and 0 <= ep_idx < len(episode_ids):
        return str(episode_ids[ep_idx])

    if numeric_prog is not None:
        for eid in episode_ids:
            try:
                if decimal.Decimal(str(eid)) == numeric_prog:
                    return str(eid)
            except decimal.InvalidOperation:
                continue

    if show.get("_episode_catalog_state") == "loaded":
        return None
    return str(progress)


def write_history_progress(show, progress, ttype, last_synced=None, touch=False):
    global _history_cache
    if not is_incognito():
        os.makedirs(paths.STATE_DIR, exist_ok=True)

    import decimal
    try:
        if decimal.Decimal(str(progress)) < 0:
            progress = "0"
    except decimal.InvalidOperation:
        pass
    history = load_history()
    old = get_history_entry(show, ttype)
    timestamp = int(time.time()) if touch else int((old or {}).get("timestamp") or time.time())
    if old and isinstance(old.get("show"), dict):
        merged_show = dict(old["show"])
        merged_show.update({k: v for k, v in (show or {}).items() if v is not None and v != ""})
        stored_show = sanitize_show_for_history(merged_show)
    else:
        stored_show = sanitize_show_for_history(show)
    episode_id = episode_id_for_progress(show, ttype, progress)
    if episode_id is None:
        debug_warn(
            "Skipped history progress update",
            ValueError(f"No provider episode mapping for progress {progress}"),
        )
        return None
    entry = {
        "show": stored_show,
        "episode": episode_id,
        "translation_type": ttype,
        "timestamp": timestamp,
    }
    if old:
        if "was_caught_up" in old:
            entry["was_caught_up"] = old["was_caught_up"]
        if "has_new_release" in old:
            entry["has_new_release"] = old["has_new_release"]
    from media_cli.domain.history import history_available_episode_count
    avail = history_available_episode_count(entry)
    try:
        from media_cli.domain.episodes import clean_episode_identifier
        p_str = clean_episode_identifier(str(progress)) or clean_episode_identifier(str(episode_id)) or str(progress)
        p_dec = decimal.Decimal(str(p_str))
        if avail is not None and p_dec >= decimal.Decimal(str(avail)):
            entry["was_caught_up"] = True
            entry["has_new_release"] = False
    except (decimal.InvalidOperation, TypeError, ValueError):
        pass
    if last_synced is None and old and "last_synced_progress" in old:
        entry["last_synced_progress"] = old["last_synced_progress"]
    elif last_synced is not None:
        entry["last_synced_progress"] = max(0, int(last_synced))
    from media_cli.domain.matching import is_same_show
    cfg = load_config()
    auto_merge = bool(cfg.get("auto_merge_history", True))
    history = [
        item for item in history
        if not (
            is_same_show(item.get("show", {}), show, strict=not auto_merge)
            and item.get("translation_type", "sub") == ttype
        )
    ]
    history.insert(0, entry)
    history = history[:paths.HISTORY_MAX]
    _atomic_write_json(paths.HISTORY_PATH, sanitize_history_list(history), indent=2)
    _history_cache = history
    show["_local_progress"] = get_local_progress(show, ttype) or progress
    show["_local_episode_label"] = get_local_episode_label(show, ttype) or show.get("_local_progress")
    try:
        from media_cli.ui.info_panel import invalidate_panel_cache
        invalidate_panel_cache()
    except ImportError:
        pass
    return entry


def set_last_synced_progress(show, progress, ttype="sub"):
    local_progress = get_local_progress(show, ttype)
    write_history_progress(
        show,
        local_progress if local_progress is not None else progress,
        ttype,
        last_synced=progress,
        touch=False,
    )


def save_history(show, episode, ttype):
    from ..domain.episodes import episode_index_for_id
    episode_ids = show.get("_episode_ids") or []
    labels = show.get("_episode_labels") or {}
    if episode_ids:
        episode_index = episode_index_for_id(episode_ids, episode, labels=labels)
        if episode_index is None:
            debug_warn(
                "Skipped history update",
                ValueError(f"Episode ID {episode!r} is absent from the catalog"),
            )
            return

    progress = str(episode)
    show["_sync_enabled"] = get_title_sync(show)
    try:
        show["_progress_authority"] = "LOCAL"
        write_history_progress(show, progress, ttype, touch=True)
    except Exception as e:
        debug_warn("Failed to save watch history", e)


def touch_history(show, ttype):
    """Update show's recency timestamp in history without modifying watched progress."""
    old = get_history_entry(show, ttype)
    if old and "episode" in old:
        write_history_progress(show, old["episode"], ttype, touch=True)
    else:
        write_history_progress(show, "0", ttype, touch=True)


def delete_history_entry(show_id, ttype):
    global _history_cache
    history, changed = list_state.delete_history_entry(
        load_history(),
        show_id,
        ttype,
    )
    if not changed:
        return False
    if is_incognito():
        _history_cache = history
        return True
    try:
        _atomic_write_json(paths.HISTORY_PATH, sanitize_history_list(history), indent=2)
        _history_cache = history
        return True
    except Exception as e:
        debug_warn("Failed to delete history entry", e)
        return False


def save_refreshed_history(history):
    global _history_cache
    clean_history = sanitize_history_list(history)
    _history_cache = clean_history
    if is_incognito():
        return True
    try:
        _atomic_write_json(paths.HISTORY_PATH, clean_history, indent=2)
        return True
    except Exception as e:
        debug_warn("Failed to save refreshed history", e)
        return False


def patch_history_entry_show(show_id, ttype, updated_show):
    if not show_id:
        return False
    history = load_history()
    changed = False
    str_show_id = str(show_id)
    safe_keys = {
        "status", "episodeCount", "availableEpisodes", "availableEpisodesDetail",
        "name", "englishName", "nativeName", "thumbnail", "altNames",
        "type", "season", "airedStart", "airedEnd", "score",
        "_episode_ids", "_episode_ids_ttype", "_episode_labels", "_episode_labels_ttype",
        "_episode_catalog_state", "_allanime_checked_at",
        "aniListId", "malId", "_provider", "_provider_id", "_provider_name",
        "description", "genres", "banner", "format",
        "_next_airing_ep", "_next_airing_time", "_next_airing_at",
        "_anilist_score", "anilistMatch", "_title_enriched",
    }
    for entry in history:
        if ttype is None or entry.get("translation_type", "sub") == ttype:
            s = entry.get("show")
            if s and str(s.get("_id") or s.get("id") or "") == str_show_id:
                if s is updated_show:
                    changed = True
                else:
                    for k in safe_keys:
                        v = updated_show.get(k)
                        if v is not None and s.get(k) != v:
                            s[k] = v
                            changed = True
                if ttype is not None:
                    break
    if changed:
        return save_refreshed_history(history)
    return False


# ---------------------------------------------------------------------------
# Search history
# ---------------------------------------------------------------------------

def load_search_history():
    global _search_history_cache
    try:
        _search_history_cache = list_state.load_json_list(
            paths.SEARCH_HISTORY_PATH,
            _search_history_cache,
        )
        return _search_history_cache
    except Exception as e:
        debug_warn("Failed to load search history", e)
        _preserve_invalid_state_file(paths.SEARCH_HISTORY_PATH, "Search history")
        _search_history_cache = []
        return _search_history_cache


def save_search_history(query):
    global _search_history_cache
    if is_incognito():
        return
    query = query.strip()
    if not query:
        return
    os.makedirs(paths.STATE_DIR, exist_ok=True)
    history = list_state.update_search_history(
        load_search_history(),
        query,
        paths.HISTORY_MAX,
    )
    try:
        _atomic_write_json(paths.SEARCH_HISTORY_PATH, history, indent=2)
        _search_history_cache = history
    except Exception as e:
        debug_warn("Failed to save search history", e)


def delete_search_history_entry(query: str):
    global _search_history_cache
    if is_incognito():
        return
    q_str = (query or "").strip().lower()
    if not q_str:
        return
    history = [q for q in load_search_history() if q.strip().lower() != q_str]
    try:
        _atomic_write_json(paths.SEARCH_HISTORY_PATH, history, indent=2)
        _search_history_cache = history
    except Exception as e:
        debug_warn("Failed to delete search history entry", e)
    return history


def clear_search_history():
    global _search_history_cache
    if is_incognito():
        return
    try:
        _atomic_write_json(paths.SEARCH_HISTORY_PATH, [], indent=2)
        _search_history_cache = []
    except Exception as e:
        debug_warn("Failed to clear search history", e)
    return []


# ---------------------------------------------------------------------------
# History formatting (delegates to domain.history with injected helpers)
# ---------------------------------------------------------------------------

def format_history_entry(h):
    return history_domain.format_history_entry(
        h,
        prepare_display_state=_prepare_display_state,
        get_local_progress=get_local_progress,
    )


def format_history_updated_time(h):
    return history_domain.format_relative_time((h or {}).get("timestamp"))


def history_entry_progress(entry):
    return history_domain.history_entry_progress(
        entry,
        prepare_display_state=_prepare_display_state,
        get_local_progress=get_local_progress,
    )


def history_entry_category(entry):
    return history_domain.history_entry_category(
        entry,
        prepare_display_state=_prepare_display_state,
        get_local_progress=get_local_progress,
    )


def filter_history_entries(history, mode):
    return history_domain.filter_history_entries(
        history,
        mode,
    )


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def sanitize_token(token):
    value = str(token or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1].strip()
    return value


def get_default_download_dir(require_existing=False):
    import platform
    # Termux Android storage
    termux_storage = os.path.expanduser("~/storage/downloads")
    if os.path.isdir(termux_storage) and os.access(termux_storage, os.W_OK):
        return os.path.join(termux_storage, APP_NAME)

    # Windows native
    if platform.system() == "Windows":
        win_path = os.path.join(os.environ.get("USERPROFILE", os.path.expanduser("~")), "Downloads")
        if os.path.isdir(win_path) and os.access(win_path, os.W_OK):
            return os.path.join(win_path, APP_NAME)

    # Standard macOS/Linux
    unix_path = os.path.expanduser("~/Downloads")
    if os.path.isdir(unix_path) and os.access(unix_path, os.W_OK):
        return os.path.join(unix_path, APP_NAME)

    if require_existing:
        return None

    # Absolute fallback
    return os.path.join(os.getcwd(), APP_NAME)

def load_config():
    cfg = load_config_file(
        paths.CONFIG_PATH,
        disabled=is_incognito(),
        on_error=lambda exc: debug_warn("Failed to load config", exc),
        on_invalid=lambda path: warn(
            f"Config was invalid; moved it to {path}"
        ),
    )
    if secret_state.is_available():
        secret_token = secret_state.get_secret(secret_state.ANILIST_KEY)
        disk_token = cfg.get("anilist_token")
        if disk_token and not secret_token:
            secret_state.set_secret(secret_state.ANILIST_KEY, disk_token)
            secret_token = disk_token
        if secret_token:
            cfg["anilist_token"] = sanitize_token(secret_token)
            if disk_token:
                disk_cfg = dict(cfg)
                disk_cfg["anilist_token"] = ""
                save_config_file(paths.CONFIG_PATH, disk_cfg, disabled=is_incognito())
    return cfg


def save_config(cfg):
    disk_cfg = dict(cfg)
    if secret_state.is_available() and secret_state.get_secret(secret_state.ANILIST_KEY):
        disk_cfg["anilist_token"] = ""
    res = save_config_file(paths.CONFIG_PATH, disk_cfg, disabled=is_incognito())
    try:
        from ..domain.titles import invalidate_title_language_cache
        invalidate_title_language_cache()
    except Exception:
        pass
    return res


def load_downloads_db():
    import json
    if not os.path.exists(paths.DOWNLOADS_DB_PATH):
        return {"current_download_dir": "", "shows": {}}
    try:
        with open(paths.DOWNLOADS_DB_PATH) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"current_download_dir": "", "shows": {}}
        if "shows" not in data:
            data["shows"] = {}
        return data
    except Exception as e:
        debug_warn(f"Failed to load downloads db: {e}")
        return {"current_download_dir": "", "shows": {}}

_DOWNLOADS_DB_META_STRIP_KEYS = {
    "_poster_raw",
    "_poster_status",
    "_poster_status_time",
    "_poster_failed",
}


def save_downloads_db(db):
    if is_incognito():
        return
    import copy
    import os
    import shutil
    os.makedirs(paths.STATE_DIR, exist_ok=True)
    if os.path.isfile(paths.DOWNLOADS_DB_PATH) and os.path.getsize(paths.DOWNLOADS_DB_PATH) > 10:
        try:
            shutil.copy2(paths.DOWNLOADS_DB_PATH, paths.DOWNLOADS_DB_PATH + ".bak")
        except Exception:
            pass
    # Strip session-only poster fields so stale/protocol-specific ANSI blobs
    # don't persist across sessions (they'd bypass fresh chafa rendering).
    clean_db = copy.deepcopy(db)
    for show_data in clean_db.get("shows", {}).values():
        meta = show_data.get("metadata")
        if isinstance(meta, dict):
            for key in _DOWNLOADS_DB_META_STRIP_KEYS:
                meta.pop(key, None)
    _atomic_write_json(paths.DOWNLOADS_DB_PATH, clean_db, indent=2)

def update_offline_watch_status(title, episode):
    db = load_downloads_db()
    if title not in db.get("shows", {}):
        return False
    show_data = db["shows"][title]
    watched = show_data.get("watched_episodes", [])
    ep_str = str(episode)
    if ep_str not in watched:
        watched.append(ep_str)
        show_data["watched_episodes"] = watched
        save_downloads_db(db)
        return True
    return False


def find_offline_file_for_episode(show_title: str, episode, cfg: dict | None = None) -> str | None:
    if not show_title or episode is None:
        return None
    import re
    cfg = cfg or load_config()
    download_dir = cfg.get("download_dir") or get_default_download_dir()
    if not os.path.exists(download_dir):
        return None
    from media_cli.media.download import sanitize_filename
    show_folder = sanitize_filename(show_title)
    full_show_path = os.path.join(download_dir, show_folder)
    if not os.path.isdir(full_show_path):
        full_show_path = os.path.join(download_dir, show_title)
        if not os.path.isdir(full_show_path):
            return None

    target_ep_str = str(episode).strip()
    from media_cli.domain.episodes import clean_episode_identifier, parse_episode_dual_numbers
    t_prim, t_sec = parse_episode_dual_numbers(target_ep_str)
    target_clean = (t_prim or clean_episode_identifier(target_ep_str) or target_ep_str).lstrip("0") or "0"
    target_sec_clean = (t_sec.lstrip("0") or "0") if t_sec else None

    EP_EXPLICIT_RE = re.compile(r'\b(?:episodes?|ep)\b[-_.\s]*([^\n]+)$', re.IGNORECASE)
    EP_FALLBACK_RE = re.compile(r'(?:^|[^\d])0*(\d+(?:\.\d+)?)(?:[^\d]|$)')
    try:
        for fname in sorted(os.listdir(full_show_path)):
            if not fname.lower().endswith((".mp4", ".mkv", ".webm", ".avi", ".ts")):
                continue
            stem = os.path.splitext(fname)[0]
            m_exp = EP_EXPLICIT_RE.search(stem)
            if m_exp:
                raw_label = m_exp.group(1).strip()
                f_prim, f_sec = parse_episode_dual_numbers(raw_label)
                f_clean = (f_prim or clean_episode_identifier(raw_label) or raw_label).lstrip("0") or "0"
                f_sec_clean = (f_sec.lstrip("0") or "0") if f_sec else None
                if (
                    f_clean == target_clean
                    or raw_label == target_ep_str
                    or (f_sec_clean and f_sec_clean == target_clean)
                    or (target_sec_clean and f_clean == target_sec_clean)
                    or (f_sec_clean and target_sec_clean and f_sec_clean == target_sec_clean)
                ):
                    return os.path.join(full_show_path, fname)
            else:
                m = EP_FALLBACK_RE.search(fname)
                if m:
                    found_num = m.group(1).lstrip("0") or "0"
                    if found_num == target_clean or m.group(1) == target_ep_str:
                        return os.path.join(full_show_path, fname)
    except OSError:
        pass
    return None


def delete_offline_episode(show_title: str, episode, cfg: dict | None = None) -> bool:
    """Delete a downloaded episode video file from disk and update downloads.db."""
    filepath = find_offline_file_for_episode(show_title, episode, cfg)
    deleted = False
    if filepath and os.path.isfile(filepath):
        try:
            os.remove(filepath)
            deleted = True
        except OSError:
            pass

    db = load_downloads_db()
    for title_key in (show_title, show_title.strip()):
        if title_key in db.get("shows", {}):
            show_data = db["shows"][title_key]
            eps = show_data.get("episodes", [])
            ep_str = str(episode).strip()
            from media_cli.domain.episodes import clean_episode_identifier, parse_episode_dual_numbers
            t_prim, _ = parse_episode_dual_numbers(ep_str)
            t_clean = (t_prim or clean_episode_identifier(ep_str) or ep_str).lstrip("0") or "0"

            new_eps = []
            for e in eps:
                e_prim, _ = parse_episode_dual_numbers(str(e))
                e_clean = (e_prim or clean_episode_identifier(str(e)) or str(e)).lstrip("0") or "0"
                if e_clean != t_clean and str(e).strip() != ep_str:
                    new_eps.append(e)

            show_data["episodes"] = new_eps
            meta = show_data.get("metadata", {})
            meta["downloadedCount"] = len(new_eps)
            watched = show_data.get("watched_episodes", [])
            show_data["watched_episodes"] = [w for w in watched if str(w).strip() != ep_str]
            save_downloads_db(db)
            break

    return deleted

