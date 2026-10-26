"""Prepare a title selection without mutating the visible search result."""

from __future__ import annotations

import logging
import time
from copy import deepcopy
from dataclasses import dataclass

from media_cli import app_core

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreparedDetails:
    show: dict
    episode_ids: tuple[str, ...]
    original: dict


def prepare_details_transition(show: dict, ttype: str, status_cb=None) -> PreparedDetails:
    started = time.monotonic()
    original = deepcopy(show)
    prepared = deepcopy(original)
    ids = app_core.ensure_episode_ids(prepared, ttype, status_cb=status_cb)
    catalog_ready = time.monotonic()
    if ids:
        if status_cb:
            status_cb("Preparing title details…")
        if not prepared.get("description") or not prepared.get("genres"):
            app_core.enrich_show_if_missing(prepared)
        app_core.prepare_show_display_state(prepared, ttype)
    _log.debug(
        "Title preparation: provider=%s title=%s translation=%s catalog=%.3fs details=%.3fs episodes=%d",
        prepared.get("_provider"),
        prepared.get("_id"),
        ttype,
        catalog_ready - started,
        time.monotonic() - catalog_ready,
        len(ids),
    )
    return PreparedDetails(prepared, tuple(str(value) for value in ids), original)


def commit_details_transition(show: dict, prepared: PreparedDetails) -> None:
    # Preserve concurrent cover/metadata updates untouched by preparation.
    from ..core.snapshots import apply_snapshot_delta
    apply_snapshot_delta(show, prepared.original, prepared.show)


def prepare_title_refresh(show: dict, ttype: str) -> PreparedDetails:
    """Force a provider check without persisting from a cancellable worker."""
    original = deepcopy(show)
    prepared = deepcopy(original)
    # Optional enrichment can write history; only the accepted result may do so.
    app_core.refresh_history_entry_provider_catalog(
        {"show": prepared, "translation_type": ttype}, enrich=False,
    )
    ids = prepared.get("_episode_ids") or ()
    return PreparedDetails(prepared, tuple(str(value) for value in ids), original)
