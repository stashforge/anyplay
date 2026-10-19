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
    for key, value in prepared.show.items():
        if key not in prepared.original or value != prepared.original[key]:
            show[key] = value
    for key in prepared.original.keys() - prepared.show.keys():
        show.pop(key, None)
