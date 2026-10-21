"""Episode catalog fetching, provider source management, and search dispatching."""

from __future__ import annotations

import concurrent.futures
import logging
import threading
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field

from ..core.api import (
    ProviderDependencyError,
    SearchFailure,
    search_failure_message,
)
from ..core.enrichment import enrich_show_if_missing
from ..core.reporting import debug_warn, err
from ..core.workers import DaemonExecutor
from ..domain.episodes import (
    is_contiguous_legacy_catalog as _is_contiguous_legacy_catalog,
)
from ..domain.episodes import (
    normalize_episode_ids as _normalize_episode_ids,
)
from ..providers import allanime as allanime_service
from ..providers import (
    get_provider,
    provider_display_name,
    provider_key,
)
from ..providers.shared.models import (
    title_provider_id,
    title_provider_key,
)
from ..services.http import request_json as _req
from ..ui.display import fatal_terminal_exit, with_loading

_logger = logging.getLogger(__name__)


def _current_provider():
    return get_provider(provider_key(), _req)


_allanime_provider = _current_provider


def _provider_for_title(show: dict):
    return get_provider(title_provider_key(show), _req)


def search_anime(query: str, ttype: str = "sub", raise_errors: bool = False, provider_id: str | None = None) -> list[dict]:
    provider_id = provider_key(provider_id)
    provider_name = provider_display_name(provider_id)
    try:
        return get_provider(provider_id, _req).search(query, ttype)
    except SearchFailure as e:
        if not raise_errors:
            debug_warn(f"{provider_name} search failed", e)
        if raise_errors:
            raise
        return []
    except Exception as e:
        if not raise_errors:
            debug_warn(f"{provider_name} search failed", e)
        if raise_errors:
            failure = SearchFailure(search_failure_message(provider_name, e))
            raise failure from e
        return []


def get_show_by_id(show_id: str, provider_id: str | None = None) -> dict | None:
    provider_id = provider_key(provider_id)
    try:
        return get_provider(provider_id, _req).get_title(show_id)
    except Exception as e:
        debug_warn(f"{provider_id} show fetch failed", e)
        return None


def get_allanime_show(show_id: str) -> dict | None:
    return get_show_by_id(show_id)


def fetch_episode_catalog(show_id: str, ttype: str = "sub", provider_id: str | None = None) -> dict:
    return get_provider(provider_id, _req).episode_catalog(show_id, ttype)


def fetch_episode_ids(show_id: str, ttype: str = "sub") -> list[str]:
    """Compatibility wrapper; use fetch_episode_catalog() for state details."""
    return fetch_episode_catalog(show_id, ttype)["ids"]


def update_available_count_from_episode_ids(
    show: dict,
    ttype: str,
    episode_ids: list,
    detail: dict | None = None,
    labels: dict | None = None,
    episodes_map: dict | None = None,
    by_number: dict | None = None,
) -> bool:
    from media_cli.domain.episodes import highest_episode_number
    if not show or not ttype or episode_ids is None:
        return False
    available = dict(show.get("availableEpisodes") or {})
    changed = False

    if detail:
        for key, ids in detail.items():
            if key in ("sub", "dub") and isinstance(ids, list):
                existing_count = available.get(key, 0)
                new_count = highest_episode_number(ids)
                if str(new_count) != str(existing_count):
                    available[key] = new_count
                    changed = True
    else:
        existing_count = available.get(ttype, 0)
        new_count = highest_episode_number(episode_ids)
        if str(new_count) != str(existing_count):
            available[ttype] = new_count
            changed = True

    old_ids = show.get("_episode_ids") or []
    old_ttype = show.get("_episode_ids_ttype")
    if old_ids != list(episode_ids) or old_ttype != ttype:
        changed = True

    show["availableEpisodes"] = available
    show["_episode_ids"] = list(episode_ids)
    show["_episode_ids_ttype"] = ttype
    if "_episode_ids_by_ttype" not in show or not isinstance(show["_episode_ids_by_ttype"], dict):
        show["_episode_ids_by_ttype"] = {}
    show["_episode_ids_by_ttype"][ttype] = list(episode_ids)
    if labels is not None:
        show["_episode_labels"] = {
            str(key): str(value)
            for key, value in dict(labels or {}).items()
            if str(key) and str(value)
        }
    if episodes_map is not None:
        show["_episodes_map"] = episodes_map
        if "_episodes_map_by_ttype" not in show or not isinstance(show["_episodes_map_by_ttype"], dict):
            show["_episodes_map_by_ttype"] = {}
        show["_episodes_map_by_ttype"][ttype] = episodes_map
    if by_number is not None:
        show["_episodes_by_number"] = by_number
        if "_episodes_by_number_by_ttype" not in show or not isinstance(show["_episodes_by_number_by_ttype"], dict):
            show["_episodes_by_number_by_ttype"] = {}
        show["_episodes_by_number_by_ttype"][ttype] = by_number
    return changed


def _download_catalog_for_show(
    show: dict,
    ttype: str,
) -> tuple[list[str], dict, dict | None, dict | None]:
    """Return a fuller saved download catalog for this provider title, if one exists."""
    if not show:
        return [], {}, None, None
    show_id = str(title_provider_id(show) or "")
    provider = title_provider_key(show, default="")
    try:
        from ..core.storage import load_downloads_db
    except Exception:
        return [], {}, None, None

    best: tuple[list[str], dict, dict | None, dict | None] = ([], {}, None, None)
    db = load_downloads_db() or {}
    for item in (db.get("shows") or {}).values():
        if not isinstance(item, dict):
            continue
        meta = item.get("metadata") or {}
        if not isinstance(meta, dict):
            continue

        meta_id = str(title_provider_id(meta) or "")
        meta_provider = title_provider_key(meta, default="")
        same_provider_id = bool(
            show_id
            and meta_id
            and show_id == meta_id
            and provider
            and provider == meta_provider
        )
        if not same_provider_id:
            continue

        by_ttype = meta.get("_episode_ids_by_ttype") or {}
        ids = by_ttype.get(ttype) or (
            meta.get("_episode_ids")
            if meta.get("_episode_ids_ttype") in (None, ttype)
            else []
        )
        ids = _normalize_episode_ids(ids)
        if len(ids) <= len(best[0]):
            continue

        labels = dict(meta.get("_episode_labels") or {})
        episodes_map = (meta.get("_episodes_map_by_ttype") or {}).get(ttype) or meta.get(
            "_episodes_map"
        )
        by_number = (meta.get("_episodes_by_number_by_ttype") or {}).get(ttype) or meta.get(
            "_episodes_by_number"
        )
        best = (ids, labels, episodes_map, by_number)
    return best


def _use_download_catalog_if_better(
    show: dict,
    ttype: str,
    episode_ids: list,
    catalog: dict | None = None,
) -> list[str]:
    saved_ids, labels, episodes_map, by_number = _download_catalog_for_show(show, ttype)
    if len(saved_ids) <= len(episode_ids or []):
        return list(episode_ids or [])
    # Saved metadata can already be mislabeled. A fuller catalogue must contain
    # the provider's current entries, not merely have a matching display title.
    if not episode_ids or not {str(ep) for ep in episode_ids} <= set(saved_ids):
        _logger.debug("Ignoring incompatible saved episode catalog: provider=%s title=%s translation=%s",
                      title_provider_key(show), title_provider_id(show), ttype)
        return list(episode_ids or [])
    detail = None
    if catalog:
        detail = catalog.get("detail")
    if not detail:
        detail = {ttype: saved_ids}
    update_available_count_from_episode_ids(
        show,
        ttype,
        saved_ids,
        detail,
        labels,
        episodes_map=episodes_map,
        by_number=by_number,
    )
    show["_episode_catalog_state"] = "loaded"
    show.pop("_episode_catalog_error", None)
    return saved_ids


@dataclass(frozen=True)
class PreparedEpisodeSelection:
    """Detached selection data; discard it to cancel, or explicitly commit it."""

    show: dict
    episode_ids: tuple[str, ...]
    _before: dict = field(repr=False, compare=False)
    _ttype: str = field(repr=False)


def _apply_snapshot_delta(show: dict, before: dict, after: dict) -> None:
    """Merge changed leaves so unrelated concurrent metadata survives."""
    for key in before.keys() - after.keys():
        show.pop(key, None)
    for key, value in after.items():
        if key in before and before[key] == value:
            continue
        if isinstance(value, dict):
            if not isinstance(show.get(key), dict):
                show[key] = {}
            previous = before.get(key)
            _apply_snapshot_delta(show[key], previous if isinstance(previous, dict) else {}, value)
        else:
            show[key] = deepcopy(value)


def commit_episode_selection(show: dict, result: PreparedEpisodeSelection) -> None:
    """Apply an accepted result on the caller/UI thread, copying nested data."""
    _apply_snapshot_delta(show, result._before, result.show)
    snapshot = result.show
    ttype = result._ttype
    # A shared fetch may have started with a different caller's catalog. Apply
    # the active catalog even when some values already matched that baseline.
    for key in (
        "_episode_ids", "_episode_ids_ttype", "_episode_catalog_state",
        "_episode_catalog_error", "_episode_labels", "_episodes_map", "_episodes_by_number",
    ):
        if key in snapshot:
            show[key] = deepcopy(snapshot[key])
    if snapshot.get("_episode_catalog_state") == "loaded":
        show.pop("_episode_catalog_error", None)
    elif snapshot.get("_episode_catalog_state") == "unavailable" and "_episode_ids" not in snapshot:
        show.pop("_episode_ids", None)
    for key in (
        "availableEpisodes", "_episode_ids_by_ttype",
        "_episodes_map_by_ttype", "_episodes_by_number_by_ttype",
    ):
        values = snapshot.get(key)
        if isinstance(values, dict) and ttype in values:
            if not isinstance(show.get(key), dict):
                show[key] = {}
            show[key][ttype] = deepcopy(values[ttype])


def prepare_episode_selection(show: dict, ttype: str) -> PreparedEpisodeSelection:
    """Prepare without live mutation or terminal exit; commit only if accepted.

    The frozen result owns a mutable, deeply detached show snapshot and immutable
    episode IDs. Provider dependency errors propagate to the UI caller.
    """
    snapshot = deepcopy(show)
    before = deepcopy(snapshot)
    ids = _ensure_episode_ids(snapshot, ttype)
    return PreparedEpisodeSelection(snapshot, tuple(ids), before, ttype)


_in_flight_catalog_tasks: dict[tuple[str, str], concurrent.futures.Future] = {}
_catalog_task_lock = threading.Lock()
_catalog_executor = DaemonExecutor(max_workers=4)


def _perform_catalog_fetch(show: dict, ttype: str, status_cb: Callable[[str], None] | None = None, status_msg: str | None = None) -> list[str]:
    show_id = title_provider_id(show)
    if show_id:
        if not show.get("_title_enriched") and not show.get("aniListId") and not show.get("_anilist_score"):
            if callable(status_cb):
                status_cb("Loading title info…")
            enrich_show_if_missing(show)
        if callable(status_cb):
            status_cb(status_msg or f"Loading {ttype.upper()} episodes…")
        catalog = _provider_for_title(show).episode_catalog(show_id, ttype)
    else:
        catalog = {
            "state": "unavailable",
            "ids": [],
            "error": "This title has no provider ID for episode lookup.",
        }

    if catalog.get("state") == "loaded":
        show["_episode_catalog_state"] = "loaded"
        show.pop("_episode_catalog_error", None)
        ep_map = catalog.get("episodes_map")
        if not ep_map and catalog.get("episodes_map_by_ttype"):
            ep_map = catalog["episodes_map_by_ttype"].get(ttype)
        num_map = catalog.get("by_number")
        if not num_map and catalog.get("by_number_by_ttype"):
            num_map = catalog["by_number_by_ttype"].get(ttype)
        update_available_count_from_episode_ids(
            show,
            ttype,
            catalog["ids"],
            catalog.get("detail"),
            catalog.get("labels"),
            episodes_map=ep_map,
            by_number=num_map,
        )
        return _use_download_catalog_if_better(show, ttype, catalog["ids"], catalog)

    show["_episode_ids_ttype"] = ttype
    show["_episode_catalog_error"] = catalog.get("error", "No episodes available.")
    show.pop("_episode_ids", None)
    show["_episode_catalog_state"] = "unavailable"
    return []


def _fetch_catalog_snapshot(show: dict, ttype: str, status_cb: Callable[[str], None] | None, status_msg: str | None) -> PreparedEpisodeSelection:
    before = deepcopy(show)
    ids = _perform_catalog_fetch(show, ttype, status_cb, status_msg)
    return PreparedEpisodeSelection(show, tuple(ids), before, ttype)


def _forget_catalog_task(task_key: tuple[str, str], future: concurrent.futures.Future) -> None:
    with _catalog_task_lock:
        if _in_flight_catalog_tasks.get(task_key) is future:
            _in_flight_catalog_tasks.pop(task_key, None)


def ensure_episode_ids(show: dict, ttype: str, status_cb: Callable[[str], None] | None = None, status_msg: str | None = None) -> list[str]:
    """Fetch and apply on this caller, retaining noninteractive terminal errors."""
    try:
        return _ensure_episode_ids(show, ttype, status_cb, status_msg)
    except ProviderDependencyError as exc:
        from ..ui.transition import in_preparation
        if in_preparation():
            raise
        fatal_terminal_exit(str(exc))
        raise


def _ensure_episode_ids(show: dict, ttype: str, status_cb: Callable[[str], None] | None = None, status_msg: str | None = None) -> list[str]:
    if not show:
        return []
    cached_ttype = show.get("_episode_ids_ttype")
    cached_ids = (
        _normalize_episode_ids(show.get("_episode_ids"))
        if cached_ttype == ttype else []
    )
    if not cached_ids:
        by_ttype = show.get("_episode_ids_by_ttype") or {}
        if ttype in by_ttype:
            cached_ids = _normalize_episode_ids(by_ttype[ttype])
            if cached_ids:
                show["_episode_ids_ttype"] = ttype
                show["_episode_ids"] = list(cached_ids)

    # File labels identify local downloads, not the provider's playable entries.
    show_id = title_provider_id(show)
    local_title = not show_id or show_id.startswith("local:") or not title_provider_key(show, default="")
    if not cached_ids and local_title and (show.get("_downloaded_episodes") or show.get("_folder_name")):
        eps = show.get("_episode_ids") or show.get("_downloaded_episodes") or []
        cached_ids = _normalize_episode_ids(eps)
        if cached_ids:
            show["_episode_ids_ttype"] = ttype

    if cached_ids:
        _logger.debug("Episode catalog reused: provider=%s title=%s translation=%s episodes=%d",
                      title_provider_key(show), title_provider_id(show), ttype, len(cached_ids))
        show["_episode_catalog_state"] = "loaded"
        update_available_count_from_episode_ids(show, ttype, cached_ids)
        return _use_download_catalog_if_better(show, ttype, cached_ids)

    if show_id and str(show_id).startswith("local:"):
        eps = show.get("availableEpisodesDetail", {}).get(ttype, [])
        return _normalize_episode_ids(eps)

    p_key = title_provider_key(show)
    task_key = (f"{p_key}:{show_id}" if show_id else str(id(show)), ttype)

    created = False
    with _catalog_task_lock:
        fut = _in_flight_catalog_tasks.get(task_key)
        if fut is None or fut.done():
            snapshot = deepcopy(show)
            fut = _catalog_executor.submit(_fetch_catalog_snapshot, snapshot, ttype, status_cb, status_msg)
            _in_flight_catalog_tasks[task_key] = fut
            created = True
    # Register outside the lock: an already-finished future runs this inline.
    if created:
        fut.add_done_callback(lambda completed: _forget_catalog_task(task_key, completed))

    try:
        result = fut.result(timeout=30)
    except concurrent.futures.TimeoutError as exc:
        debug_warn(f"Catalog fetch timed out for {task_key}", exc)
        show["_episode_catalog_state"] = "unavailable"
        show["_episode_catalog_error"] = "Episode catalog lookup timed out."
        return []
    except ProviderDependencyError:
        raise
    except Exception as e:
        debug_warn(f"Catalog fetch failed for {task_key}", e)
        show["_episode_catalog_state"] = "unavailable"
        show["_episode_catalog_error"] = str(e)
        return []
    commit_episode_selection(show, result)
    return list(result.episode_ids)


def check_translation_switch_capability(show: dict, current_ttype: str, target_ttype: str) -> tuple[bool, str]:
    p_id = (show or {}).get("_provider") or (show or {}).get("_provider_name")
    from media_cli.providers import provider_translation_capability
    return provider_translation_capability(p_id, show, current_ttype, target_ttype)


def episode_catalog_needs_fetch(show: dict, ttype: str) -> bool:
    if not show:
        return False
    if show.get("_episode_ids_ttype") != ttype:
        return True
    state = str(show.get("_episode_catalog_state") or "")
    if state == "loaded":
        return False
    ids = _normalize_episode_ids(show.get("_episode_ids"))
    return not ids or _is_contiguous_legacy_catalog(ids)


def load_episode_ids_for_selection(show: dict, ttype: str) -> list[str]:
    if episode_catalog_needs_fetch(show, ttype):
        # If enrichment was attempted (even if failed), don't say "loading title info"
        # since there's no more title metadata to fetch
        initial_msg = "Loading episodes…" if (show.get("_title_enriched") or show.get("aniListId") or show.get("_enrichment_attempted")) else "Loading title info…"
        return with_loading(
            initial_msg,
            ensure_episode_ids,
            show,
            ttype,
        )
    return ensure_episode_ids(show, ttype)


def episode_catalog_error(show: dict) -> str:
    p_name = (show.get("_provider_name") or show.get("_provider") or "").title() if show else "this provider"
    raw = str((show or {}).get("_episode_catalog_error") or "")
    if "\n" in raw or len(raw) > 80:
        raw = raw.strip().splitlines()[0]
        if len(raw) > 60:
            raw = raw[:60] + "…"
    return raw or f"No episodes available on {p_name}. Try another provider."


def get_episode_data(show_id: str, ep: str, ttype: str = "sub", provider_id: str | None = None) -> dict | None:
    provider_id = provider_key(provider_id)
    try:
        return get_provider(provider_id, _req).episode_sources(show_id, ep, ttype)
    except ProviderDependencyError as exc:
        fatal_terminal_exit(str(exc))
    except Exception as e:
        _logger.debug(
            "Episode fetch failed for %s ep %s (%s)",
            show_id,
            ep,
            provider_id,
            exc_info=True,
        )
        err(f"Episode fetch failed: {e}")
        return None


def get_clock_links(path: str) -> list[str]:
    try:
        return allanime_service.get_clock_links(_req, path)
    except Exception:
        return []
