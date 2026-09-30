"""AniList domain: auth/token storage, API calls, and list caches.

Owns every interaction with the AniList service that does not require the
provider/UI layers.  Sync orchestration and the write-queue worker live in
the app layer (they reach into provider catalogs and UI feedback); this
module is the network + credential + read-cache leader beneath them.

Dependencies point downward only:

    core.anilist -> services.anilist (GraphQL), services.http (urlopen)
    core.anilist -> state.secrets (OS keyring), core.storage (config)
    core.anilist -> core.reporting (debug_warn), context.FLAGS (incognito)

ANSI colour constants are declared locally, matching the existing
per-module convention (download.py, playback.py, …); there is no shared
colours module to depend on yet.
"""


from ..context import FLAGS
from ..core import reporting, storage
from ..core.api import (
    SearchFailure,
    anilist_account_cache_key,
    read_json_response,
    search_failure_message,
)
from ..services import anilist as anilist_service
from ..services import normalize as anilist_normalize
from ..services.anilist_auth import (
    save_anilist_token,
    clear_anilist_token,
    anilist_token_storage_status,
    mask_token,
    anilist_auth_status_lines,
    stored_anilist_token,
    anilist_auth_login_existing_lines,
    anilist_auth_token_lines,
    prompt_anilist_token,
)
from ..services.anilist_queue import (
    _load_anilist_queue,
    _save_anilist_queue,
    _anilist_mutation_key,
    _enqueue_anilist_progress,
    _update_queued_mutation,
    _remove_queued_mutation,
    _checkpoint_queued_progress,
    _finish_queued_pending_completion,
    _run_anilist_callback,
    _run_queued_anilist_progress,
    _start_queued_anilist_progress,
    queue_anilist_progress,
    retry_queued_anilist_writes,
    flush_anilist_writes,
)
from ..services.http import anilist_urlopen
from ..state import paths

GREEN = "\033[1;32m"
RED = "\033[1;31m"
BOLD = "\033[1m"
RESET = "\033[0m"

debug_warn = reporting.debug_warn

# Read caches, invalidated on any successful write (update_anilist_entry).
_anilist_list_cache = {}
_anilist_search_cache = {}


def _is_incognito():
    return FLAGS.incognito_mode


# ---------------------------------------------------------------------------
# Token / auth
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Media / list / search API
# ---------------------------------------------------------------------------

def fetch_anilist_media(token, media_id):
    try:
        raw_media = anilist_service.fetch_one(
            anilist_urlopen,
            read_json_response,
            token,
            anilist_id=media_id,
        )
        return raw_media or {}
    except Exception as e:
        debug_warn("Failed to refresh AniList media", e)
        return {}


def get_show_anilist_id(show):
    if not isinstance(show, dict):
        return None
    try:
        if show.get("_anilist_id"):
            return int(show.get("_anilist_id"))
        if show.get("aniListId"):
            return int(show.get("aniListId"))
        match = show.get("anilistMatch")
        if isinstance(match, dict) and match.get("id"):
            return int(match["id"])
        prov = str(show.get("_provider") or show.get("provider") or "").lower()
        if str(show.get("_id", "")).isdigit() and prov in ("anikoto", "miruro"):
            return int(show.get("_id"))
        return None
    except (ValueError, TypeError):
        return None


def get_show_mal_id(show):
    if not isinstance(show, dict):
        return None
    try:
        if show.get("malId"):
            return int(show.get("malId"))
        if show.get("idMal"):
            return int(show.get("idMal"))
        match = show.get("anilistMatch")
        if isinstance(match, dict) and match.get("idMal"):
            return int(match["idMal"])
        al_id = get_show_anilist_id(show)
        if al_id:
            results = fetch_anilist_by_ids("", anilist_ids=[int(al_id)])
            if results and results[0].get("malId"):
                mal_id = int(results[0]["malId"])
                show["malId"] = mal_id
                return mal_id
        return None
    except (ValueError, TypeError):
        return None


def get_anilist_media_id(anime):
    return get_show_anilist_id(anime)


def update_anime_from_anilist_media(anime, media):
    return anilist_normalize.apply_media_update(anime, media)


def fetch_anilist_list(token, status=None, force_refresh=False):
    cache_key = (
        anilist_account_cache_key(token),
        str(status or "ALL").upper(),
    )
    if not force_refresh and cache_key in _anilist_list_cache:
        return _anilist_list_cache[cache_key]

    try:
        raw_shows = anilist_service.fetch(
            anilist_urlopen,
            read_json_response,
            token,
            user_list_status=status or "",
            user_list=True if not status else False,
        )
        shows = [
            anilist_normalize.normalize_media(
                raw, list_name=raw.pop("_list_name", None)
            )
            for raw in raw_shows
        ]
        _anilist_list_cache[cache_key] = shows
        return shows
    except Exception as e:
        debug_warn("Failed to fetch AniList list", e)
        return []


def search_anilist(token, query, raise_errors=False):
    cache_key = (
        anilist_account_cache_key(token),
        str(query or "").strip().casefold(),
    )
    if cache_key in _anilist_search_cache:
        return _anilist_search_cache[cache_key]

    try:
        raw_shows = anilist_service.fetch(
            anilist_urlopen,
            read_json_response,
            token,
            search=query,
        )
        shows = [anilist_normalize.normalize_media(raw) for raw in raw_shows]
        _anilist_search_cache[cache_key] = shows
        return shows
    except SearchFailure as e:
        if not raise_errors:
            debug_warn("AniList search failed", e)
        if raise_errors:
            raise
        return []
    except Exception as e:
        if not raise_errors:
            debug_warn("AniList search failed", e)
        if raise_errors:
            raise SearchFailure(search_failure_message("AniList", e)) from e
        return []


def fetch_anilist_by_ids(token, anilist_ids=None, mal_ids=None, raise_errors=False):
    if not anilist_ids and not mal_ids:
        return []

    # We could implement a cache here by exact IDs, but for now just pass through
    try:
        raw_shows = anilist_service.fetch(
            anilist_urlopen,
            read_json_response,
            token,
            anilist_ids=anilist_ids,
            mal_ids=mal_ids,
        )
        shows = [anilist_normalize.normalize_media(raw) for raw in raw_shows]
        return shows
    except SearchFailure as e:
        debug_warn("AniList fetch_by_ids failed", e)
        if raise_errors:
            raise
        return []
    except Exception as e:
        debug_warn("AniList fetch_by_ids failed", e)
        if raise_errors:
            raise SearchFailure(search_failure_message("AniList", e)) from e
        return []


def load_anilist_browse(token, status):
    from ..ui.display import with_anilist_menu_loading
    return with_anilist_menu_loading(
        status,
        f"Loading AniList list: {status}",
        fetch_anilist_list,
        token,
        status,
    )



# ---------------------------------------------------------------------------
# Entry writes (dates + mutation). Read caches invalidated on success.
# ---------------------------------------------------------------------------

def scrobble_anilist(
        token,
        title,
        ep,
        media_id=None,
        status=None,
        show=None,
        started_at=None,
        completed_at=None):
    try:
        if media_id is None:
            media_id = anilist_service.search_media_id(
                anilist_urlopen,
                read_json_response,
                title,
            )

        return update_anilist_entry(
            token,
            int(media_id),
            progress=ep,
            status=status,
            show=show,
            started_at=started_at,
            completed_at=completed_at,
        )
    except Exception:
        return False


def _fuzzy_date_has_value(value):
    return bool(
        isinstance(value, dict)
        and value.get("year")
        and value.get("month")
        and (value.get("day") or value.get("date"))
    )


def _today_fuzzy_date():
    from datetime import date
    today = date.today()
    return {
        "year": today.year,
        "month": today.month,
        "day": today.day,
    }


def _anilist_date_updates(show, progress=None, status=None):
    updates = {}
    normalized_status = str(status or "").upper()
    try:
        progress_value = int(progress) if progress is not None else None
    except (TypeError, ValueError):
        progress_value = None
    should_start = (
        (progress_value is not None and progress_value > 0)
        or normalized_status in {"CURRENT", "REPEATING", "COMPLETED"}
    )
    if should_start and not _fuzzy_date_has_value(show.get("_anilist_started_at")):
        updates["started_at"] = _today_fuzzy_date()
    if (
        normalized_status == "COMPLETED"
        and not _fuzzy_date_has_value(show.get("_anilist_completed_at"))
    ):
        updates["completed_at"] = _today_fuzzy_date()
    return updates


def update_anilist_entry(
        token,
        media_id,
        progress=None,
        status=None,
        score=None,
        show=None,
        started_at=None,
        completed_at=None):
    if _is_incognito():
        return False
    try:
        date_updates = _anilist_date_updates(show or {}, progress, status)
        if started_at is not None:
            date_updates["started_at"] = started_at
        if completed_at is not None:
            date_updates["completed_at"] = completed_at
        res = anilist_service.update_entry(
            anilist_urlopen,
            read_json_response,
            token,
            media_id,
            progress=progress,
            status=status,
            score=score,
            **date_updates,
        )
        if res.get("errors"):
            debug_warn("AniList update returned errors", res.get("errors"))
            return False
        entry = (res.get("data") or {}).get("SaveMediaListEntry") or {}
        if show is not None and entry:
            if entry.get("startedAt"):
                show["_anilist_started_at"] = entry["startedAt"]
            if entry.get("completedAt"):
                show["_anilist_completed_at"] = entry["completedAt"]
        _anilist_list_cache.clear()
        _anilist_search_cache.clear()
        return True
    except Exception as e:
        debug_warn("AniList update failed", e)
        return False


# ---------------------------------------------------------------------------
# Injected upper-layer hooks (wired by app_core; see storage.configure pattern)
# ---------------------------------------------------------------------------

_action_feedback_fn = None
_provider_catalog_refresh_fn = None


def configure(*, action_feedback_fn=None, provider_catalog_refresh_fn=None, allanime_catalog_refresh_fn=None):
    """Inject the app-layer callbacks the sync/refresh paths need.

    * ``action_feedback_fn(show, msg)`` — post a toast on the show dict.
    * ``provider_catalog_refresh_fn(entry)`` — refresh a history entry's
      provider episode catalog after an episode airs.
    """
    global _action_feedback_fn, _provider_catalog_refresh_fn
    if action_feedback_fn is not None:
        _action_feedback_fn = action_feedback_fn
    fn = provider_catalog_refresh_fn or allanime_catalog_refresh_fn
    if fn is not None:
        _provider_catalog_refresh_fn = fn



def _feedback(show, msg):
    if _action_feedback_fn:
        _action_feedback_fn(show, msg)


# ---------------------------------------------------------------------------
# Sync orchestration
# ---------------------------------------------------------------------------

def should_update_anilist_progress(existing_progress, watched_episode):
    if watched_episode is None:
        return False

    from ..domain.episodes import anilist_progress_target_for_episode
    target_progress = anilist_progress_target_for_episode(watched_episode, fallback=None)

    if target_progress is None:
        return False

    if existing_progress is None:
        return True
    try:
        return target_progress > int(existing_progress)
    except (TypeError, ValueError):
        return True


def sync_progress_and_checkpoint(token, title, progress, media_id, show, ttype, status=None):
    from ..domain.tracking import (
        apply_tracking_progress_local,
        tracking_progress_for_sync,
        tracking_status_for_progress,
    )
    status = tracking_status_for_progress(show, progress) if status is None else status
    progress = tracking_progress_for_sync(show, progress, status)

    existing = show.get("_anilist_progress")
    prog_to_send = progress if should_update_anilist_progress(existing, progress) else None

    if not scrobble_anilist(
        token,
        title,
        prog_to_send,
        media_id=media_id,
        status=status,
        show=show,
    ):
        return False

    if progress is not None:
        apply_tracking_progress_local(show, progress, status)
    else:
        if status:
            show["_anilist_list"] = status
    show["_progress_authority"] = "AL"
    return True


def sync_watched_to_anilist(token, title, ep, media_id, show, ttype):
    return sync_progress_and_checkpoint(
        token, title, ep, media_id, show, ttype
    )


def save_and_sync_watched(show, episode, ttype, token, title, progress, media_id):
    storage.save_history(show, episode, ttype)

    if not (token and title and media_id):
        _feedback(show, f"Saved locally as EP {episode}.")
        return {"status": "local_saved"}

    from ..domain.episodes import anilist_progress_target_for_episode
    sync_prog = anilist_progress_target_for_episode(episode, fallback=progress)

    try:
        current_al = max(0, int(show.get("_anilist_progress") or 0))
    except (TypeError, ValueError):
        current_al = 0

    if sync_prog is None:
        # Fallback if invalid
        _feedback(show, f"Saved locally as EP {episode}.")
        return {"status": "local_saved"}

    if sync_prog <= current_al:
        _feedback(show, f"Saved locally as EP {episode}. AniList already ahead at EP {current_al}, sync skipped.")
        return {"status": "skipped", "anilist_target": sync_prog, "anilist_current": current_al}

    result = sync_watched_to_anilist(token, title, sync_prog, media_id, show, ttype)
    if result:
        _feedback(show, f"Saved locally as EP {episode}. AniList synced as EP {sync_prog}.")
        return {"status": "synced", "anilist_target": sync_prog}
    else:
        _feedback(show, f"Saved locally as EP {episode}. AniList sync failed.")
        return {"status": "failed"}


# ---------------------------------------------------------------------------
# Durable write-queue (mutations survive crashes; one worker thread drains)
# ---------------------------------------------------------------------------

import time as _time

from ..services.anilist_queue import (
    configure_queue,
)

configure_queue(
    scrobble_fn=lambda *args, **kwargs: scrobble_anilist(*args, **kwargs),
    should_update_fn=lambda *args, **kwargs: should_update_anilist_progress(*args, **kwargs),
    date_updates_fn=lambda *args, **kwargs: _anilist_date_updates(*args, **kwargs),
)



# ---------------------------------------------------------------------------
# Progress reconciliation (local history vs AniList remote)
# ---------------------------------------------------------------------------

def _reconcile_status(show, progress):
    from ..domain import reconciliation as reconciliation_domain
    from ..domain.metadata import positive_int as _positive_int
    return reconciliation_domain.reconcile_status(
        show,
        progress,
        _positive_int,
    )


def _push_local_progress(show, ttype, token, progress):
    from ..domain.tracking import apply_tracking_progress_local
    al_id = get_show_anilist_id(show)
    if not token or not al_id:
        return {"action": "failed", "progress": progress}
    status = _reconcile_status(show, progress)

    existing = show.get("_anilist_progress")
    prog_to_send = int(progress) if should_update_anilist_progress(existing, progress) else None

    if not update_anilist_entry(
        token,
        int(al_id),
        progress=prog_to_send,
        status=status,
        show=show,
    ):
        return {"action": "failed", "progress": progress}

    if progress is not None:
        apply_tracking_progress_local(show, int(progress), status)
    else:
        if status:
            show["_anilist_list"] = status

    show["_progress_authority"] = "AL"
    show.pop("_sync_conflict", None)
    return {"action": "pushed", "progress": int(progress) if progress is not None else None, "status": status}


# ---------------------------------------------------------------------------
# History refresh from AniList
# ---------------------------------------------------------------------------

def refresh_history_entry_from_anilist(entry, token):
    show = (entry or {}).get("show", {})
    media_id = get_anilist_media_id(show)
    if not token or not media_id or not show.get("_sync_enabled"):
        return False
    media = fetch_anilist_media(token, media_id)
    if not media:
        return False
    changed = update_anime_from_anilist_media(show, media)
    if changed and show.get("_anilist_progress") is not None:
        show["_progress_authority"] = "AL"
    return changed


def refresh_history_anilist_airing_batch(history_entries):
    now = int(_time.time())

    # No cooldown, check every time user enters history
    COOLDOWN = 0

    media_ids_to_fetch = []
    entry_map = {}

    for entry in history_entries:
        show = entry.get("show")
        if not show:
            continue

        al_id = get_show_anilist_id(show)
        if not al_id:
            continue

        last_checked = show.get("_anilist_airing_checked_at", 0)
        next_airing_at = show.get("_next_airing_at")
        status = str(show.get("_anilist_status") or show.get("status") or "").upper()

        needs_refresh = False
        if next_airing_at and next_airing_at <= now:
            needs_refresh = True
        elif last_checked == 0:
            needs_refresh = True
        elif not show.get("description") or not show.get("genres") or not show.get("thumbnail"):
            needs_refresh = True
        elif status in ("RELEASING", "NOT_YET_RELEASED", "UNKNOWN", "") and (now - last_checked) > COOLDOWN:
            needs_refresh = True

        if needs_refresh:
            media_ids_to_fetch.append(str(al_id))
            if str(al_id) not in entry_map:
                entry_map[str(al_id)] = []
            entry_map[str(al_id)].append({"entry": entry, "old_next_airing_at": next_airing_at})

    changed = False

    if media_ids_to_fetch:
        # Deduplicate IDs
        media_ids_to_fetch = list(set(media_ids_to_fetch))

        try:
            raw_shows = anilist_service.fetch(
                anilist_urlopen,
                read_json_response,
                token="",
                anilist_ids=media_ids_to_fetch,
            )
            batch_results = {str(media["id"]): media for media in raw_shows if "id" in media}
        except Exception as e:
            debug_warn("AniList batch fetch failed", e)
            batch_results = {}

        for al_id, media in batch_results.items():
            items = entry_map.get(str(al_id), [])
            for item in items:
                entry = item["entry"]
                old_next_airing_at = item["old_next_airing_at"]
                show = entry.get("show")
                if not show: continue

                show["_anilist_airing_checked_at"] = now
                changed = True

                # Additive full metadata enrichment
                try:
                    norm_media = anilist_normalize.normalize_media(media)
                    if norm_media:
                        from .enrichment import _merge_anilist_into_provider
                        _merge_anilist_into_provider(show, norm_media)
                        show["_title_enriched"] = True
                except Exception as e:
                    debug_warn("AniList batch metadata merge failed", e)

                airing = media.get("nextAiringEpisode")
                if airing:
                    new_ep = airing.get("episode")
                    new_time = airing.get("timeUntilAiring")
                    new_at = airing.get("airingAt")
                    if not new_at and new_time:
                        new_at = now + new_time
                    if new_ep != show.get("_next_airing_ep"):
                        show["_next_airing_ep"] = new_ep
                    if new_at != show.get("_next_airing_at"):
                        show["_next_airing_at"] = new_at
                    if new_time != show.get("_next_airing_time"):
                        show["_next_airing_time"] = new_time
                else:
                    show.pop("_next_airing_ep", None)
                    show.pop("_next_airing_time", None)
                    show.pop("_next_airing_at", None)

                new_count = media.get("episodes")
                if new_count is not None and new_count != show.get("_anilist_episode_count"):
                    show["_anilist_episode_count"] = new_count

                new_status = media.get("status")
                if new_status and show.get("_anilist_status") != new_status:
                    show["_anilist_status"] = new_status
                    changed = True

                if new_status in ("RELEASING", "NOT_YET_RELEASED") or (old_next_airing_at and old_next_airing_at <= now):
                    if _provider_catalog_refresh_fn and _provider_catalog_refresh_fn(entry):
                        changed = True

    # Second pass: background-enrich shows missing AniList IDs via fallback title search
    for entry in history_entries:
        show = entry.get("show")
        if not show:
            continue
        if not get_show_anilist_id(show) and (not show.get("description") or not show.get("genres")):
            title_name = show.get("name") or show.get("englishName")
            if title_name and not show.get("_anilist_search_checked"):
                show["_anilist_search_checked"] = True
                try:
                    candidates = search_anilist("", title_name)
                    if not candidates:
                        import re
                        clean_name = re.sub(r'\s*[\(\[]\s*\d{4}\s*[\)\]]', '', title_name).strip()
                        if clean_name and clean_name != title_name:
                            candidates = search_anilist("", clean_name)
                    if candidates:
                        from ..domain.matching import choose_confident_match
                        from .enrichment import _merge_anilist_into_provider
                        best = choose_confident_match(show, candidates) or candidates[0]
                        _merge_anilist_into_provider(show, best)
                        show["_title_enriched"] = True
                        changed = True
                except Exception as e:
                    debug_warn("Background title search fallback failed", e)

    if changed:
        storage._atomic_write_json(paths.HISTORY_PATH, storage.sanitize_history_list(history_entries), indent=2)
        storage._history_cache = history_entries
        try:
            from ..ui.info_panel import invalidate_panel_cache
            invalidate_panel_cache()
        except Exception:
            pass
    return changed
