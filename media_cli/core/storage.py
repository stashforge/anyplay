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
from copy import deepcopy
from datetime import datetime
from functools import wraps
from threading import RLock

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
from .diagnostics import append_rotating_text, get_log_paths

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
    if show:
        stored_type = show.get("_episode_ids_ttype")
        if (not stored_type or stored_type == ttype) and show.get("_episode_ids"):
            return show.get("_episode_ids") or []
        by_ttype = show.get("_episode_ids_by_ttype") or {}
        if isinstance(by_ttype, dict) and by_ttype.get(ttype):
            return by_ttype.get(ttype) or []
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
_incognito_history_allowed = False
_history_lock = RLock()


def _history_transaction(function):
    @wraps(function)
    def transact(*args, **kwargs):
        with _history_lock:
            return function(*args, **kwargs)
    return transact


def set_incognito_history_allowed(allowed: bool) -> None:
    """Set whether incognito mode is permitted to read an existing history snapshot (e.g. anyplay history --incognito)."""
    global _incognito_history_allowed
    _incognito_history_allowed = bool(allowed)


def is_incognito_history_allowed() -> bool:
    """Check whether incognito mode is allowed to read an existing history snapshot."""
    return _incognito_history_allowed


def reset_caches():
    """Drop every in-memory cache (tests; also safe after path changes)."""
    global _prefs_cache, _history_cache, _search_history_cache, _incognito_history_allowed
    _prefs_cache = None
    _history_cache = None
    _search_history_cache = None
    _incognito_history_allowed = False


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
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    content = redact_sensitive_text(f"{timestamp} ERROR crash traceback\n{traceback.format_exc()}")
    if is_incognito() and not FLAGS.debug_mode:
        return None
    target = os.path.join(paths.LOG_DIR, filename)
    if os.path.basename(filename) == filename:
        paths_map = get_log_paths()
        if filename == "crash.log":
            target = paths_map["crash"]
    return append_rotating_text(target, content)


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
    if is_incognito() and not is_incognito_history_allowed():
        _prefs_cache = {}
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


def get_preferred_mirror(show_id: str, provider_id=None) -> dict:
    return preference_state.preferred_mirror(load_prefs(), show_id, provider_id)


def toggle_preferred_mirror(show_id: str, sname: str, resolution: str, provider_id=None):
    save_prefs(preference_state.toggle_preferred_mirror(
        load_prefs(), show_id, sname, resolution, provider_id
    ))


def get_episode_order(show_id: str, default_order: str, provider_id=None) -> str:
    return preference_state.episode_order(
        _owned_title_preferences(show_id, provider_id, ("episode_order",), "_episode_order_scope_owner"),
        show_id, default_order, provider_id
    )


def toggle_episode_order(show_id: str, default_order: str, provider_id=None) -> str:
    prefs = _owned_title_preferences(show_id, provider_id, ("episode_order",), "_episode_order_scope_owner")
    new_order = preference_state.toggle_episode_order(
        prefs, show_id, default_order, provider_id
    )
    save_prefs(prefs)
    return new_order


def get_title_sync_preference(show):
    if not show:
        return None
    return preference_state.title_sync_preference(_title_sync_preferences(show), show)


def get_title_sync(show) -> bool:
    return get_title_sync_preference(show) is True


def set_title_sync(show, enabled: bool):
    if not show:
        return
    if is_incognito():
        show["_sync_enabled"] = False
        return
    save_prefs(preference_state.set_title_sync(
        _title_sync_preferences(show), show, enabled
    ))
    show["_sync_enabled"] = bool(enabled)
    try:
        from media_cli.ui.info_panel import invalidate_panel_cache
        invalidate_panel_cache()
    except ImportError:
        pass


def get_custom_skips(show, fallback_show_id=None):
    return preference_state.custom_skips(_custom_skip_preferences(show, fallback_show_id), show or {}, fallback_show_id)


def set_custom_skips(show, fallback_show_id, entry):
    prefs = _custom_skip_preferences(show, fallback_show_id)
    save_prefs(preference_state.set_custom_skips(prefs, show or {}, fallback_show_id, entry))


def clear_custom_skips(show, fallback_show_id=None):
    prefs = _custom_skip_preferences(show, fallback_show_id)
    save_prefs(preference_state.clear_custom_skips(prefs, show or {}, fallback_show_id))


# ---------------------------------------------------------------------------
# Resume / pending completion / AniList match prefs
# ---------------------------------------------------------------------------

def _owned_title_preferences(show_id, provider_id, fields, owner_key, *,
                             preferences=None, history_entries=None, persist=True):
    """Adopt legacy fields once, only with one exact, consistent history owner."""
    from copy import deepcopy
    from ..provider_manifest import _PROVIDER_ALIASES
    from ..domain.titles import title_provider_key

    prefs = load_prefs() if preferences is None else preferences
    if not provider_id:
        return prefs
    provider = str(provider_id).strip().casefold()
    provider = _PROVIDER_ALIASES.get(provider, provider)
    legacy = prefs.get(str(show_id)) or {}
    known_owners = {str(legacy[key]) for key in ("_resume_scope_owner", "_episode_order_scope_owner", "_sync_scope_owner", "_custom_skip_scope_owner") if legacy.get(key)}
    if (legacy.get(owner_key) or (known_owners and known_owners != {provider})
            or not any(field in legacy for field in fields)):
        return prefs
    try:
        owners = set()
        for entry in (load_history() if history_entries is None else history_entries):
            show = entry.get("show") or {}
            if str(show.get("_id") or show.get("id") or "") == str(show_id):
                owner = title_provider_key(show).casefold()
                owners.add(_PROVIDER_ALIASES.get(owner, owner))
    except Exception:
        return prefs
    if owners != {provider}:
        return prefs
    scoped = prefs.setdefault(preference_state._provider_title_key(show_id, provider), {})
    for field in fields:
        if field in legacy and field not in scoped:
            scoped[field] = deepcopy(legacy[field])
    legacy[owner_key] = provider
    if persist:
        save_prefs(prefs)
    return prefs


def _title_sync_preferences(show, **kwargs):
    from ..domain.titles import title_provider_key

    return _owned_title_preferences((show or {}).get("_id"), title_provider_key(show),
                                    ("sync_enabled",), "_sync_scope_owner", **kwargs)


def _custom_skip_preferences(show, fallback_show_id=None):
    from ..domain.titles import title_provider_key

    show = show or {}
    return _owned_title_preferences(show.get("_id") or show.get("id") or fallback_show_id,
                                    title_provider_key(show), ("custom_skips",), "_custom_skip_scope_owner")


def _resume_preferences(show_id, provider_id=None):
    return _owned_title_preferences(show_id, provider_id,
                                    ("resumes", "pending_completion"), "_resume_scope_owner")


def legacy_resume_owner(show_id):
    return (load_prefs().get(str(show_id)) or {}).get("_resume_scope_owner")


def get_resume_time(show_id: str, ep: int, provider_id=None) -> int:
    return preference_state.resume_time(_resume_preferences(show_id, provider_id), show_id, ep, provider_id)


def save_resume_time(show_id: str, ep: int, time: int, provider_id=None):
    save_prefs(preference_state.save_resume_time(
        _resume_preferences(show_id, provider_id), show_id, ep, time, provider_id
    ))


def get_pending_completion(show_id: str, provider_id=None) -> dict:
    return preference_state.pending_completion(_resume_preferences(show_id, provider_id), show_id, provider_id)


def save_pending_completion(show_id: str, ep, progress_ep: int, next_ep, time_pos: int, duration: int, provider_id=None):
    save_prefs(preference_state.save_pending_completion(
        _resume_preferences(show_id, provider_id),
        show_id,
        ep,
        progress_ep,
        next_ep,
        time_pos,
        duration,
        provider_id,
    ))


def clear_pending_completion(show_id: str, provider_id=None):
    save_prefs(preference_state.clear_pending_completion(
        _resume_preferences(show_id, provider_id), show_id, provider_id
    ))


def get_al_match(al_id: str) -> dict:
    return preference_state.anilist_match(load_prefs(), al_id)


def save_al_match(al_id: str, aa_show: dict):
    if is_incognito():
        return
    save_prefs(preference_state.save_anilist_match(
        load_prefs(), al_id, aa_show
    ))


def get_source_anilist_match(show_id: str, provider_id=None) -> dict:
    return preference_state.source_anilist_match(
        load_prefs(), show_id, provider_id
    )


def save_source_anilist_match(aa_show: dict, anilist_show: dict):
    if is_incognito():
        return
    save_prefs(preference_state.save_source_anilist_match(
        load_prefs(), aa_show, anilist_show
    ))

def get_source_anilist_matches(show_ids: list, provider_id=None) -> dict:
    from ..domain.titles import title_provider_key

    prefs = load_prefs()
    matches = {}
    for source in show_ids:
        if isinstance(source, dict):
            sid = str(source.get("_id") or source.get("id") or "")
            provider = title_provider_key(source)
            key = preference_state._provider_title_key(sid, provider)
        else:
            sid, provider, key = source, provider_id, source
        matches[key] = preference_state.source_anilist_match(prefs, sid, provider)
    return matches


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
    "_episode_ids",
    "_episode_ids_ttype",
    "_episode_ids_by_ttype",
    "_episode_labels",
    "_episode_labels_ttype",
    "_episodes_map",
    "_episodes_map_by_ttype",
    "_episodes_by_number",
    "_episodes_by_number_by_ttype",
    "_allanime_checked_at",
    "_local_progress",
    "_local_episode_label",
    "watched_episodes",
    "_folder_name",
    "_download_files",
    "_downloaded_episodes",
    "_is_downloads",
    "_enrichment_in_progress",
    # "_enrichment_attempted" - MUST persist across sessions to prevent re-enrichment
    "_anilist_search_checked",
    "_hist_backfill_checked",
}


def sanitize_show_for_history(show):
    stored = dict(show or {})
    from ..domain.episodes import episode_display_summary
    from ..domain.metadata import provider_status_is_authoritative

    summaries = {}
    for ttype in ("sub", "dub", "raw"):
        summary = episode_display_summary(stored, ttype)
        if summary is not None:
            available, latest = summary
            summaries[ttype] = {"available": available, "latest_label": latest}
    if summaries:
        stored["_episode_display"] = summaries
    else:
        stored.pop("_episode_display", None)
    for key in HISTORY_SHOW_STRIP_KEYS:
        stored.pop(key, None)
    prov = stored.get("_provider") or stored.get("provider")
    if not prov:
        from ..providers.shared.models import title_provider_key
        prov = title_provider_key(show, default="")
    if prov:
        stored["_provider"] = str(prov)
    stored.pop("provider", None)
    if stored.get("_anilist_status") == "FINISHED" and not provider_status_is_authoritative(stored):
        stored["status"] = "FINISHED"
    elif any(term in str(stored.get("status") or "").upper() for term in ("FINISHED", "COMPLETED", "ENDED")):
        stored["status"] = "FINISHED"
    return stored


def _capture_history_progress(entry, show=None):
    # Retain a small progress snapshot before discarding provider catalogues.
    source = show if show is not None else entry.get("show", {})
    progress = history_domain.local_progress((entry,), source, entry.get("translation_type", "sub"))
    if progress is not None:
        entry["progress"] = progress
    if source.get("_episode_labels_ttype", entry.get("translation_type", "sub")) == entry.get("translation_type", "sub"):
        label = (source.get("_episode_labels") or {}).get(str(entry.get("episode")))
        if label:
            entry["episode_label"] = str(label)


def sanitize_history_list(history):
    clean_history = []
    for entry in history:
        clean_entry = {**entry, "show": sanitize_show_for_history(entry.get("show"))}
        _capture_history_progress(clean_entry, entry.get("show"))
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


@_history_transaction
def load_history():
    """Return a detached snapshot; mutations must use the history write APIs."""
    return deepcopy(_load_history())


def _load_history():
    global _history_cache
    if _history_cache is not None:
        return _history_cache
    if is_incognito() and not is_incognito_history_allowed():
        _history_cache = []
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


def get_history_entry(show, ttype="sub", *, history_entries=None):
    from media_cli.domain.matching import is_same_show
    for entry in load_history() if history_entries is None else history_entries:
        if entry.get("translation_type", "sub") != ttype:
            continue
        entry_show = entry.get("show", {})
        if is_same_show(entry_show, show):
            return entry
    return None



def get_local_progress(show, ttype="sub", *, history_entries=None):
    return history_domain.local_progress(
        load_history() if history_entries is None else history_entries,
        show,
        ttype,
    )


def get_local_episode_label(show, ttype="sub", *, history_entries=None):
    entry = (get_history_entry(show, ttype) if history_entries is None else
             get_history_entry(show, ttype, history_entries=history_entries))
    if entry and "episode" in entry:
        eid = entry["episode"]
        if str(eid) in ("0", "0.0"):
            return "0"
        labels = (show or {}).get("_episode_labels") or (entry.get("show") or {}).get("_episode_labels") or {}
        if labels and str(eid) in labels:
            return str(labels[str(eid)])
        if entry.get("episode_label"):
            return entry["episode_label"]
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
    import decimal
    try:
        if decimal.Decimal(str(progress)) < 0:
            progress = "0"
    except decimal.InvalidOperation:
        pass
    # Episode mapping may call a provider. Resolve it before the storage lock.
    episode_id = episode_id_for_progress(show, ttype, progress)
    if episode_id is None:
        debug_warn(
            "Skipped history progress update",
            ValueError(f"No provider episode mapping for progress {progress}"),
        )
        return None
    return _write_history_progress(show, progress, ttype, episode_id, last_synced, touch)


@_history_transaction
def _write_history_progress(show, progress, ttype, episode_id, last_synced, touch):
    global _history_cache
    import decimal
    if not is_incognito():
        os.makedirs(paths.STATE_DIR, exist_ok=True)
    history = load_history()
    old = get_history_entry(show, ttype)
    timestamp = int(time.time()) if touch else int((old or {}).get("timestamp") or time.time())
    if old and isinstance(old.get("show"), dict):
        merged_show = dict(old["show"])
        merged_show.update({k: v for k, v in (show or {}).items() if v is not None and v != ""})
        stored_show = sanitize_show_for_history(merged_show)
    else:
        stored_show = sanitize_show_for_history(show)
    entry = {
        "show": stored_show,
        "episode": episode_id,
        "translation_type": ttype,
        "timestamp": timestamp,
    }
    if old:
        if str(old.get("episode")) == str(episode_id) and "progress" in old:
            entry["progress"] = old["progress"]
        if str(old.get("episode")) == str(episode_id) and old.get("episode_label"):
            entry["episode_label"] = old["episode_label"]
        if "was_caught_up" in old:
            entry["was_caught_up"] = old["was_caught_up"]
        if "has_new_release" in old:
            entry["has_new_release"] = old["has_new_release"]
    _capture_history_progress(entry, show)
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
    _history_cache = deepcopy(history)
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


@_history_transaction
def delete_history_entry(show_id, ttype, *, provider_id=None):
    global _history_cache
    history, changed = list_state.delete_history_entry(
        load_history(),
        show_id,
        ttype,
        provider_id=provider_id,
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


@_history_transaction
def save_refreshed_history(history):
    global _history_cache
    clean_history = deepcopy(sanitize_history_list(history))
    _history_cache = clean_history
    if is_incognito():
        return True
    try:
        _atomic_write_json(paths.HISTORY_PATH, clean_history, indent=2)
        return True
    except Exception as e:
        debug_warn("Failed to save refreshed history", e)
        return False


@_history_transaction
def patch_history_entry_show(show_id, ttype, updated_show, *, fields=None):
    from ..domain.titles import title_provider_key

    if not show_id:
        return False
    provider = title_provider_key(updated_show)
    history = load_history()
    changed = False
    str_show_id = str(show_id)
    safe_keys = {
        "_episode_display",
        "status", "episodeCount", "availableEpisodes", "availableEpisodesDetail",
        "name", "englishName", "romajiName", "nativeName", "thumbnail", "altNames",
        "type", "season", "airedStart", "airedEnd", "score",
        "_episode_ids", "_episode_ids_ttype", "_episode_labels", "_episode_labels_ttype",
        "_episode_catalog_state", "_allanime_checked_at",
        "aniListId", "malId", "_provider", "_provider_id", "_provider_name",
        "description", "genres", "banner", "format",
        "_next_airing_ep", "_next_airing_time", "_next_airing_at",
        "_anilist_score", "_anilist_status", "anilistMatch", "_title_enriched",
        "_metadata_fields_loaded",
        "_enrichment_attempted",
        "_provider_catalog_checked_at", "_provider_catalog_attempted_at",
        "_provider_catalog_failed_at", "_provider_catalog_refresh_error",
    }
    if fields is not None:
        requested = set(fields)
        if not requested <= safe_keys:
            raise ValueError("Unsupported history metadata fields")
        safe_keys = requested
    for entry in history:
        if ttype is None or entry.get("translation_type", "sub") == ttype:
            s = entry.get("show")
            if (s and str(s.get("_id") or s.get("id") or "") == str_show_id
                    and title_provider_key(s) == provider):
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


def patch_history_entry_state(updated_entry):
    """Persist one refreshed entry through the shared batch transaction."""
    return patch_history_entries_state((updated_entry,))


@_history_transaction
def patch_history_entries_state(updated_entries):
    """Patch matching entries against current progress, with at most one save."""
    updates = tuple(updated_entries)
    if not updates:
        return False
    history = load_history()
    changed = False
    for updated_entry in updates:
        changed = _patch_history_entry_state(history, updated_entry) or changed
    return save_refreshed_history(history) if changed else False


def _patch_history_entry_state(history, updated_entry):
    from ..domain.titles import title_provider_key

    if not isinstance(updated_entry, dict):
        return False
    updated_show = updated_entry.get("show") or {}
    show_id = updated_show.get("_id") or updated_show.get("id")
    if not show_id:
        return False
    ttype = updated_entry.get("translation_type", "sub")
    changed = False
    str_show_id = str(show_id)
    provider = title_provider_key(updated_show)
    entry_keys = {"was_caught_up", "has_new_release"}
    show_keys = {
        "_episode_display",
        "status", "episodeCount", "availableEpisodes", "availableEpisodesDetail",
        "name", "englishName", "romajiName", "nativeName", "thumbnail", "altNames",
        "type", "season", "airedStart", "airedEnd", "score",
        "_episode_ids", "_episode_ids_ttype", "_episode_labels", "_episode_labels_ttype",
        "_episode_ids_by_ttype", "_episodes_map", "_episodes_map_by_ttype",
        "_episodes_by_number", "_episodes_by_number_by_ttype",
        "_episode_catalog_state", "_episode_catalog_error", "_allanime_checked_at",
        "_provider_catalog_checked_at", "_anilist_airing_checked_at",
        "_provider_catalog_attempted_at", "_provider_catalog_failed_at", "_provider_catalog_refresh_error",
        "aniListId", "malId", "_provider", "_provider_id", "_provider_name",
        "description", "genres", "banner", "format",
        "_next_airing_ep", "_next_airing_time", "_next_airing_at",
        "_anilist_score", "_anilist_status", "_anilist_episode_count",
        "anilistMatch", "_title_enriched", "_anilist_search_checked",
        "_metadata_fields_loaded",
    }
    for entry in history:
        if entry.get("translation_type", "sub") != ttype:
            continue
        show = entry.get("show") or {}
        if (str(show.get("_id") or show.get("id") or "") != str_show_id
                or title_provider_key(show) != provider):
            continue
        if entry is updated_entry:
            changed = True
        for key in entry_keys:
            if updated_entry.get(key) and not entry.get(key):
                entry[key] = True
                changed = True
        if not isinstance(entry.get("show"), dict):
            entry["show"] = {}
            show = entry["show"]
            changed = True
        for key in show_keys:
            if key in updated_show and show.get(key) != updated_show.get(key):
                show[key] = updated_show.get(key)
                changed = True
        # A refresh's progress snapshot may predate playback. Classify using
        # the latest stored progress, never write the worker's old episode back.
        previous_category = (entry.get("was_caught_up"), entry.get("has_new_release"))
        history_domain.history_entry_category(entry)
        changed = changed or previous_category != (entry.get("was_caught_up"), entry.get("has_new_release"))
        break
    return changed


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


def _download_watched_through(show: dict | None, episode) -> list[str] | None:
    if str(episode).strip() == "0":
        return []
    show = show or {}
    episode_ids = [str(ep) for ep in (show.get("_episode_ids") or [])]
    if not episode_ids:
        return None
    from media_cli.domain.episodes import episode_index_for_id

    idx = episode_index_for_id(episode_ids, episode, labels=show.get("_episode_labels"))
    if idx is None:
        return None
    return episode_ids[: idx + 1]


def set_offline_watch_progress(title, show: dict | None, episode) -> bool:
    watched = _download_watched_through(show, episode)
    if watched is None:
        return update_offline_watch_status(title, episode)
    db = load_downloads_db()
    if title not in db.get("shows", {}):
        return False
    show_data = db["shows"][title]
    old_watched = [str(ep) for ep in show_data.get("watched_episodes", [])]
    if old_watched == watched:
        return False
    show_data["watched_episodes"] = watched
    save_downloads_db(db)
    return True


_DOWNLOAD_SIDECAR_EXTS = (
    ".srt",
    ".vtt",
    ".ass",
    ".ssa",
    ".sub",
    ".idx",
    ".ttml",
    ".dfxp",
)


def _normal_download_file_path(path) -> str:
    return os.path.abspath(os.path.expanduser(str(path or "")))


def collect_downloaded_episode_files(video_path: str) -> list[str]:
    """Return the downloaded video plus existing same-stem sidecar files."""
    if not video_path:
        return []
    video = _normal_download_file_path(video_path)
    files: list[str] = []
    if os.path.isfile(video):
        files.append(video)
    base, _ = os.path.splitext(video)
    folder = os.path.dirname(video)
    stem = os.path.basename(base)
    try:
        for name in sorted(os.listdir(folder)):
            full_path = os.path.join(folder, name)
            if not os.path.isfile(full_path):
                continue
            n_base, n_ext = os.path.splitext(name)
            if n_base.startswith(stem + ".") and n_ext.lower() in _DOWNLOAD_SIDECAR_EXTS:
                n_path = _normal_download_file_path(full_path)
                if n_path not in files:
                    files.append(n_path)
    except OSError:
        pass
    return files


def _episode_matches(left, right) -> bool:
    left_s = str(left).strip()
    right_s = str(right).strip()
    if left_s == right_s:
        return True
    from media_cli.domain.episodes import clean_episode_identifier, parse_episode_dual_numbers
    l_prim, _ = parse_episode_dual_numbers(left_s)
    r_prim, _ = parse_episode_dual_numbers(right_s)
    l_clean = (l_prim or clean_episode_identifier(left_s) or left_s).lstrip("0") or "0"
    r_clean = (r_prim or clean_episode_identifier(right_s) or right_s).lstrip("0") or "0"
    return l_clean == r_clean


def _matching_episode_file_keys(episode_files: dict, episode) -> list[str]:
    if not isinstance(episode_files, dict):
        return []
    return [str(key) for key in episode_files.keys() if _episode_matches(key, episode)]


def _download_episode_registration_key(show_data: dict, episode) -> str | None:
    """Resolve an alias to one registration without broadening episode deletion."""
    from ..domain.episodes import episode_index_for_id

    keys = [str(key) for key in (show_data.get("episode_files") or {})]
    requested = str(episode).strip()
    if requested in keys:
        return requested
    labels = (show_data.get("metadata") or {}).get("_episode_labels") or {}
    catalog = list(labels)
    catalog_index = episode_index_for_id(catalog, requested, labels=labels)
    if catalog_index is None and any(
        episode_index_for_id([key], requested, labels=labels) is not None for key in catalog
    ):
        # An ambiguous catalogue alias must not become "unique" merely because
        # only one of those episodes currently has a local download.
        return None
    expected = labels[catalog[catalog_index]] if catalog_index is not None else requested
    index = episode_index_for_id(keys, expected, labels=labels)
    return keys[index] if index is not None else None


def _download_show_lookup_keys(show_title: str) -> list[str]:
    keys = []
    for key in (show_title, str(show_title or "").strip()):
        if key and key not in keys:
            keys.append(key)
    try:
        from media_cli.media.download import sanitize_filename
        safe_key = sanitize_filename(show_title)
        if safe_key and safe_key not in keys:
            keys.append(safe_key)
    except Exception:
        pass
    return keys


def _managed_download_file_identity(path: str) -> list[int] | None:
    """Stable across renames, but not copies or edits; never follow a symlink."""
    import stat

    try:
        info = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]


def _repair_registered_download_path(show_title: str, found: str) -> None:
    """Repair unique original-file ownership after lookup verified episode tags."""
    fingerprint = _managed_download_file_identity(found)
    if fingerprint is None:
        return
    db = load_downloads_db()
    for title in _download_show_lookup_keys(show_title):
        data = db.get("shows", {}).get(title)
        if not data:
            continue
        identities = data.get("episode_file_identities") or {}
        registered = data.get("episode_files") or {}
        matches = []
        # Saved keys can be provider IDs, season numbers or dual-number labels.
        # File identity is stronger evidence than parsing those keys again.
        for key, raw_files in registered.items():
            files = [raw_files] if isinstance(raw_files, str) else list(raw_files or ())
            for index, old in enumerate(files):
                if (old != found and not os.path.lexists(old)
                        and identities.get(old) == fingerprint):
                    matches.append((key, files, index, old))
        if len(matches) != 1:
            continue
        key, files, index, old = matches[0]
        if _managed_download_file_identity(found) != fingerprint:
            return
        files[index] = found
        registered[key] = files
        identities.pop(old, None)
        identities[found] = fingerprint
        save_downloads_db(db)
        return


def register_downloaded_episode_files(show_title: str, episode, files: list[str] | tuple[str, ...], metadata: dict | None = None) -> bool:
    """Remember the exact files Anyplay created for a downloaded episode."""
    if not show_title or episode is None:
        return False
    from ..media.episode_identity import download_identity_conflicts

    expected = ((metadata or {}).get("_episode_labels") or {}).get(str(episode), str(episode))
    clean_files = []
    for file_path in files or []:
        path = _normal_download_file_path(file_path)
        if (path and os.path.isfile(path) and path not in clean_files
                and not download_identity_conflicts(expected, path)):
            clean_files.append(path)
    if not clean_files:
        return False

    db = load_downloads_db()
    shows = db.setdefault("shows", {})
    show_data = shows.setdefault(show_title, {"episodes": []})
    ep_str = str(episode).strip()
    episodes = [str(ep) for ep in show_data.get("episodes", [])]
    if ep_str not in episodes:
        episodes.append(ep_str)
    show_data["episodes"] = episodes
    if metadata:
        show_data["metadata"] = metadata
    episode_files = show_data.setdefault("episode_files", {})
    episode_files[ep_str] = clean_files
    identities = show_data.setdefault("episode_file_identities", {})
    for path in clean_files:
        fingerprint = _managed_download_file_identity(path)
        if fingerprint is not None:
            identities[path] = fingerprint
    meta = show_data.get("metadata", {})
    if isinstance(meta, dict):
        meta["downloadedCount"] = len(show_data.get("episodes", []))
    save_downloads_db(db)
    return True


def _set_show_watched_episode(show, episode, watched=True):
    if not isinstance(show, dict):
        return
    ep_str = str(episode)
    watched_eps = [str(ep) for ep in show.get("watched_episodes", [])]
    if watched:
        if ep_str not in watched_eps:
            watched_eps.append(ep_str)
    else:
        watched_eps = [ep for ep in watched_eps if ep != ep_str]
    show["watched_episodes"] = watched_eps


def _set_show_watched_progress(show, episode):
    if not isinstance(show, dict):
        return
    watched = _download_watched_through(show, episode)
    if watched is None:
        _set_show_watched_episode(show, episode, watched=True)
        return
    show["watched_episodes"] = watched


def _latest_watched_episode(watched):
    if not watched:
        return "0"
    import decimal
    from media_cli.domain.episodes import clean_episode_identifier, parse_episode_dual_numbers

    def score(value):
        raw = str(value)
        primary, _secondary = parse_episode_dual_numbers(raw)
        cleaned = primary or clean_episode_identifier(raw) or raw
        try:
            return decimal.Decimal(str(cleaned))
        except (decimal.InvalidOperation, TypeError, ValueError):
            return decimal.Decimal("-1")

    return str(max((str(ep) for ep in watched), key=score))


def record_download_watch_progress(show, folder_name, episode, ttype="sub", sync_enabled=False):
    """Record watched progress for a local file and mirror title progress to history."""
    changed = set_offline_watch_progress(folder_name, show, episode)
    _set_show_watched_progress(show, episode)
    if not sync_enabled:
        save_history(show, episode, ttype)
    try:
        from media_cli.ui.info_panel import invalidate_panel_cache
        invalidate_panel_cache()
    except ImportError:
        pass
    return bool(changed or (not sync_enabled and get_history_entry(show, ttype)))


def record_download_unwatch_progress(show, folder_name, episode, ttype="sub", sync_enabled=False):
    """Remove local watched state and lower title progress to the latest watched local file."""
    ep_str = str(episode)
    changed = False
    db = load_downloads_db()
    if folder_name in db.get("shows", {}):
        watched = [str(ep) for ep in db["shows"][folder_name].get("watched_episodes", [])]
        new_watched = [ep for ep in watched if ep != ep_str]
        if new_watched != watched:
            db["shows"][folder_name]["watched_episodes"] = new_watched
            save_downloads_db(db)
            changed = True
    _set_show_watched_episode(show, episode, watched=False)
    if not sync_enabled:
        next_progress = _latest_watched_episode((show or {}).get("watched_episodes", []))
        write_history_progress(show, next_progress, ttype, touch=True)
    try:
        from media_cli.ui.info_panel import invalidate_panel_cache
        invalidate_panel_cache()
    except ImportError:
        pass
    return changed


VIDEO_DOWNLOAD_EXTS = (".mp4", ".mkv", ".webm", ".avi", ".ts")
_TEXT_FAKE_VIDEO_MARKERS = (
    b"#EXTM3U",
    b"#EXT-X-",
    b"<html",
    b"<!doctype html",
    b"<?xml",
)


def is_valid_download_video(filepath: str, *, raise_on_error: bool = False) -> bool:
    """Return False for known non-video payloads saved with video extensions.

    Keep this cheap because downloads/history views call it while scanning
    folders. It intentionally rejects known playlist/error text, while leaving
    deeper media probing to the downloader completion path. Scanners can request
    I/O errors explicitly so unreadable files are not treated as absent.
    """
    if not filepath:
        return False
    path = os.path.abspath(os.path.expanduser(str(filepath)))
    if not path.lower().endswith(VIDEO_DOWNLOAD_EXTS):
        return False
    try:
        import stat
        stamp = os.stat(path)
        if not stat.S_ISREG(stamp.st_mode) or stamp.st_size <= 0:
            return False
        with open(path, "rb") as fh:
            head = fh.read(4096)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        if raise_on_error:
            raise
        return False

    stripped = head.lstrip().lower()
    if any(stripped.startswith(marker.lower()) for marker in _TEXT_FAKE_VIDEO_MARKERS):
        return False
    if b"#extm3u" in stripped[:512] or b"#ext-x-" in stripped[:1024]:
        return False
    if len(head) < 1024 * 1024:
        textish = sum(1 for b in head if b in (9, 10, 13) or 32 <= b <= 126)
        if head and textish / len(head) > 0.92:
            sample = stripped[:2048]
            if b".m3u8" in sample or b"content-type: text" in sample or b"not found" in sample:
                return False
    return True


def find_offline_file_for_episode(
    show_title: str, episode, cfg: dict | None = None, *,
    registered_only: bool = False, downloads_db: dict | None = None,
) -> str | None:
    """Find local media; cleanup can restrict lookup to a shared registry snapshot."""
    if not show_title or episode is None:
        return None
    from ..media.download import sanitize_filename
    from ..domain.episodes import episode_index_for_id
    from ..media.episode_identity import (
        download_identity_conflicts, find_matching_episode_identity, read_episode_identity,
    )

    cfg = cfg or load_config()
    root = os.path.realpath(os.path.expanduser(str(cfg.get("download_dir") or get_default_download_dir())))
    if not os.path.isdir(root):
        return None
    shows = (load_downloads_db() if downloads_db is None else downloads_db).get("shows", {})
    data = next((shows[key] for key in _download_show_lookup_keys(show_title) if key in shows), {})
    metadata = data.get("metadata") or {}
    title_id = str(metadata.get("_id") or metadata.get("id") or metadata.get("_provider_id") or "")
    names = {str(name).casefold() for name in (show_title, metadata.get("name"), metadata.get("englishName")) if name}
    names |= {sanitize_filename(name).casefold() for name in tuple(names)}
    expected = (metadata.get("_episode_labels") or {}).get(str(episode), str(episode))

    def title_matches(identity, *, require_identity=False):
        if title_id and identity.title_id:
            return title_id == identity.title_id
        if identity.title:
            return identity.title.casefold() in names or sanitize_filename(identity.title).casefold() in names
        return not require_identity

    def tagged_match(identity):
        return title_matches(identity, require_identity=True) and (
            bool(identity.episode_id and identity.episode_id == str(episode))
            or bool(identity.episode and episode_index_for_id([identity.episode], expected) is not None)
        )

    def known_path(path):
        path = _normal_download_file_path(path)
        if os.path.commonpath((root, os.path.realpath(path))) != root or not is_valid_download_video(path):
            return None
        identity = read_episode_identity(path)
        if identity is not None and (not title_matches(identity)
                or download_identity_conflicts(expected, path, identity_reader=lambda _: identity)):
            return None
        return path

    registered = data.get("episode_files") or {}
    for key in _matching_episode_file_keys(registered, episode):
        paths = registered[key]
        for path in (paths,) if isinstance(paths, str) else paths or ():
            valid = known_path(path)
            if valid:
                return valid
    if registered_only:
        return None
    named = _find_offline_file_by_name(show_title, episode, {**cfg, "download_dir": root})
    if named:
        valid = known_path(named)
        if valid:
            _repair_registered_download_path(show_title, valid)
            return valid

    # Only direct show folders; never recursively probe an entire media library.
    folders = []
    for name in _download_show_lookup_keys(show_title):
        folder = os.path.join(root, name)
        if os.path.isdir(folder) and not os.path.islink(folder) and os.path.dirname(os.path.realpath(folder)) == root:
            folders.append(folder)
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if len(folders) >= 128:
                    break
                if entry.is_dir(follow_symlinks=False) and entry.path not in folders:
                    folders.append(entry.path)
        candidates = []
        for folder in folders:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if entry.is_file(follow_symlinks=False) and is_valid_download_video(entry.path):
                        candidates.append(entry.path)
                    if len(candidates) >= 128:
                        break
            if len(candidates) >= 128:
                break
    except OSError:
        return None
    found = find_matching_episode_identity(candidates, tagged_match)
    if found:
        _repair_registered_download_path(show_title, found)
    return found


def _find_offline_file_by_name(show_title: str, episode, cfg: dict | None = None) -> str | None:
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
            full_path = os.path.join(full_show_path, fname)
            if not is_valid_download_video(full_path):
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
                    return full_path
            else:
                m = EP_FALLBACK_RE.search(fname)
                if m:
                    found_num = m.group(1).lstrip("0") or "0"
                    if found_num == target_clean or m.group(1) == target_ep_str:
                        return full_path
    except OSError:
        pass
    return None


def delete_offline_episode(show_title: str, episode, cfg: dict | None = None, require_recorded: bool = False) -> bool:
    """Delete a downloaded episode and update downloads.db.

    When require_recorded is true, only files explicitly registered in
    downloads.db are removed. This keeps automatic cleanup from touching files
    the user may have copied into the download folder.
    """
    from ..media.episode_identity import download_identity_conflicts

    db = load_downloads_db()
    show_data = None
    for title_key in _download_show_lookup_keys(show_title):
        if title_key in db.get("shows", {}):
            show_data = db["shows"][title_key]
            break

    registered_files: list[str] = []
    registered_key = None
    if show_data:
        registered_key = _download_episode_registration_key(show_data, episode)
        expected = (((show_data.get("metadata") or {}).get("_episode_labels") or {})
                    .get(registered_key, registered_key or str(episode)))
        episode_files = show_data.get("episode_files", {})
        for key in ([registered_key] if registered_key is not None else []):
            raw_files = episode_files.get(key, [])
            if isinstance(raw_files, str):
                raw_files = [raw_files]
            for raw_file in raw_files or []:
                path = _normal_download_file_path(raw_file)
                if path and path not in registered_files and not download_identity_conflicts(expected, path):
                    registered_files.append(path)

    if require_recorded and not registered_files:
        return False

    filepath = None if registered_files else find_offline_file_for_episode(show_title, episode, cfg)
    target_files = registered_files or ([filepath] if filepath else [])
    deleted = False
    deleted_parents = set()
    for target in target_files:
        try:
            target_path = _normal_download_file_path(target)
            if target_path and os.path.isfile(target_path):
                os.remove(target_path)
                deleted = True
                deleted_parents.add(os.path.dirname(target_path))
        except OSError:
            pass

    for title_key in _download_show_lookup_keys(show_title):
        if title_key in db.get("shows", {}):
            show_data = db["shows"][title_key]
            eps = show_data.get("episodes", [])
            ep_str = str(episode).strip()
            def matches_removed_episode(value):
                if registered_key is not None:
                    return _download_episode_registration_key(show_data, value) == registered_key
                return _episode_matches(value, ep_str)

            new_eps = [e for e in eps if not matches_removed_episode(e)]

            show_data["episodes"] = new_eps
            meta = show_data.get("metadata", {})
            meta["downloadedCount"] = len(new_eps)
            watched = show_data.get("watched_episodes", [])
            show_data["watched_episodes"] = [w for w in watched if not matches_removed_episode(w)]
            episode_files = show_data.get("episode_files", {})
            if isinstance(episode_files, dict):
                for key in ([registered_key] if registered_key is not None else _matching_episode_file_keys(episode_files, ep_str)):
                    removed_paths = episode_files.get(key) or []
                    if isinstance(removed_paths, str):
                        removed_paths = [removed_paths]
                    for path in removed_paths:
                        (show_data.get("episode_file_identities") or {}).pop(path, None)
                    episode_files.pop(key, None)
            save_downloads_db(db)
            break

    if deleted_parents:
        cleanup_cfg = cfg or load_config()
        download_root = os.path.realpath(os.path.expanduser(str(
            cleanup_cfg.get("download_dir") or get_default_download_dir()
        )))
        for folder in deleted_parents:
            # Never recurse, remove the download root, or follow a show symlink.
            if os.path.islink(folder) or os.path.dirname(os.path.realpath(folder)) != download_root:
                continue
            try:
                os.rmdir(folder)  # Atomic: any remaining file keeps the folder.
            except OSError:
                pass

    return deleted
