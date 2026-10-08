"""Episode catalog fetching, provider source management, and search dispatching."""

from __future__ import annotations

import logging
from typing import Callable

from ..core.api import (
    ProviderDependencyError,
    SearchFailure,
    search_failure_message,
)
from ..core.enrichment import enrich_show_if_missing
from ..core.reporting import debug_warn, err
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


import concurrent.futures
import threading

_in_flight_catalog_tasks: dict[tuple[str, str], concurrent.futures.Future] = {}
_catalog_task_lock = threading.Lock()
_catalog_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="CatalogFetch")


def _perform_catalog_fetch(show: dict, ttype: str, status_cb: Callable[[str], None] | None = None, status_msg: str | None = None) -> list[str]:
    show_id = title_provider_id(show)
    p_key = title_provider_key(show)
    task_key = (f"{p_key}:{show_id}" if show_id else str(id(show)), ttype)
    try:
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
            return catalog["ids"]


        show["_episode_ids_ttype"] = ttype
        show["_episode_catalog_error"] = catalog.get("error", "No episodes available.")
        show.pop("_episode_ids", None)
        show["_episode_catalog_state"] = "unavailable"
        return []
    except ProviderDependencyError as exc:
        fatal_terminal_exit(str(exc))
    finally:
        with _catalog_task_lock:
            _in_flight_catalog_tasks.pop(task_key, None)


def ensure_episode_ids(show: dict, ttype: str, status_cb: Callable[[str], None] | None = None, status_msg: str | None = None) -> list[str]:
    if not show:
        return []
    cached_ttype = show.get("_episode_ids_ttype")
    cached_ids = (
        _normalize_episode_ids(show.get("_episode_ids"))
        if cached_ttype == ttype else []
    )
    if not cached_ids and (show.get("_downloaded_episodes") or show.get("_folder_name")):
        eps = show.get("_episode_ids") or show.get("_downloaded_episodes") or []
        cached_ids = _normalize_episode_ids(eps)
        if cached_ids:
            show["_episode_ids_ttype"] = ttype

    if not cached_ids:
        by_ttype = show.get("_episode_ids_by_ttype") or {}
        if ttype in by_ttype:
            cached_ids = _normalize_episode_ids(by_ttype[ttype])
            if cached_ids:
                show["_episode_ids_ttype"] = ttype
                show["_episode_ids"] = list(cached_ids)

    if cached_ids:
        show["_episode_catalog_state"] = "loaded"
        update_available_count_from_episode_ids(show, ttype, cached_ids)
        return cached_ids

    show_id = title_provider_id(show)
    if show_id and str(show_id).startswith("local:"):
        eps = show.get("availableEpisodesDetail", {}).get(ttype, [])
        return _normalize_episode_ids(eps)

    p_key = title_provider_key(show)
    task_key = (f"{p_key}:{show_id}" if show_id else str(id(show)), ttype)

    with _catalog_task_lock:
        fut = _in_flight_catalog_tasks.get(task_key)
        if fut is None:
            fut = _catalog_executor.submit(_perform_catalog_fetch, show, ttype, status_cb, status_msg)
            _in_flight_catalog_tasks[task_key] = fut

    try:
        return fut.result(timeout=30)
    except concurrent.futures.TimeoutError as exc:
        debug_warn(f"Catalog fetch timed out for {task_key}", exc)
        show["_episode_catalog_state"] = "unavailable"
        show["_episode_catalog_error"] = "Episode catalog lookup timed out."
        return []
    except ProviderDependencyError as exc:
        fatal_terminal_exit(str(exc))
    except Exception as e:
        debug_warn(f"Catalog fetch failed for {task_key}", e)
        show["_episode_catalog_state"] = "unavailable"
        show["_episode_catalog_error"] = str(e)
        return []


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
