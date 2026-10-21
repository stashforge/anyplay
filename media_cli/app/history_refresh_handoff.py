"""Detached History refresh work with owner-thread-only completion acceptance."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from queue import Empty, SimpleQueue
from threading import Lock, get_ident
from typing import Any

from .history_menu_view import HistoryEntryReference

_LOG = logging.getLogger(__name__)

_PROTECTED_SHOW_FIELDS = frozenset({
    "_local_progress", "_local_episode_label", "watched_episodes", "_anilist_progress", "_anilist_list",
    "_sync_enabled", "_sync_conflict", "_progress_authority", "_action_feedback", "_action_feedback_time",
})


def merge_history_refresh(target: dict[str, Any], update: HistoryRefreshUpdate, latest: dict[str, Any]) -> None:
    """Apply refreshed metadata while retaining current progress/presentation state."""
    from ..domain.history import history_entry_category

    if update.entry is None or HistoryEntryReference.from_entry(target) != update.reference:
        return

    def apply_delta(current: dict, before: dict, after: dict) -> None:
        for key in before.keys() | after.keys():
            if key in _PROTECTED_SHOW_FIELDS or key.startswith("_poster"):
                continue
            if key not in after:
                current.pop(key, None)
            elif key not in before or before[key] != after[key]:
                if isinstance(after[key], dict) and isinstance(before.get(key), dict) and isinstance(current.get(key), dict):
                    apply_delta(current[key], before[key], after[key])
                else:
                    current[key] = deepcopy(after[key])

    for key in ("episode", "progress", "timestamp"):
        if key in latest:
            target[key] = deepcopy(latest[key])
        else:
            target.pop(key, None)
    for key in ("was_caught_up", "has_new_release"):
        target[key] = bool(latest.get(key) or target.get(key) or update.entry.get(key))
    show = target["show"]
    apply_delta(show, update.before.get("show", {}), update.entry["show"])
    show.pop("_local_progress", None)
    show.pop("_local_episode_label", None)
    history_entry_category(target)


@dataclass(frozen=True)
class HistoryRefreshRequest:
    reference: HistoryEntryReference
    revision: int
    _entry: dict[str, Any] = field(repr=False, compare=False)
    _scope: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class HistoryRefreshUpdate:
    reference: HistoryEntryReference
    entry: dict[str, Any] | None
    changed: bool
    error: str = ""
    before: dict[str, Any] = field(default_factory=dict, repr=False, compare=False, kw_only=True)


@dataclass(frozen=True)
class _Completion:
    request: HistoryRefreshRequest
    update: HistoryRefreshUpdate


class HistoryRefreshInbox:
    def __init__(self) -> None:
        self._owner = get_ident()
        self._scope = object()
        self._serial = 0
        self._latest: dict[HistoryEntryReference, int] = {}
        self._ready: SimpleQueue[_Completion] = SimpleQueue()
        self._closed = False
        self._lock = Lock()

    def _require_owner(self) -> None:
        if get_ident() != self._owner:
            raise RuntimeError("History refresh state belongs to its UI owner thread")

    def begin(self, entries: Sequence[dict[str, Any]]) -> tuple[HistoryRefreshRequest, ...]:
        self._require_owner()
        if self._closed:
            raise RuntimeError("Cannot start work on a closed History inbox")
        snapshots = tuple(deepcopy(entry) for entry in entries)
        references = tuple(HistoryEntryReference.from_entry(entry) for entry in snapshots)
        if len(set(references)) != len(references):
            raise ValueError("Duplicate History refresh references")
        requests = []
        for reference, entry in zip(references, snapshots, strict=True):
            self._serial += 1
            self._latest[reference] = self._serial
            requests.append(HistoryRefreshRequest(reference, self._serial, entry, self._scope))
        return tuple(requests)

    def prepare(
        self, requests: Sequence[HistoryRefreshRequest], refresh: Callable[[list[dict[str, Any]]], bool],
    ) -> None:
        """Run in a caller-owned worker; no live records, UI or persistence here."""
        requests = tuple(requests)
        if any(not isinstance(request, HistoryRefreshRequest) or request._scope is not self._scope for request in requests):
            raise ValueError("History refresh requests belong to a different inbox")
        with self._lock:
            if self._closed or not requests:
                return
        prepared = [deepcopy(request._entry) for request in requests]
        error = ""
        try:
            changed = refresh(prepared)
        except Exception as exc:
            _LOG.debug("History refresh preparation failed", exc_info=True)
            error, changed = str(exc) or type(exc).__name__, False
        if type(changed) is not bool:
            raise TypeError("History refresh callback must return a boolean")
        if error:
            # A failed batch may have changed its work list; complete every ticket.
            prepared = [request._entry for request in requests]
        if len(prepared) != len(requests):
            raise ValueError("History refresh cannot add or remove entries")
        completions = []
        for request, entry in zip(requests, prepared, strict=True):
            if not error and HistoryEntryReference.from_entry(entry) != request.reference:
                raise ValueError("History refresh cannot change entry identity")
            update = HistoryRefreshUpdate(request.reference, None if error else deepcopy(entry), changed, error,
                                          before=deepcopy(request._entry))
            completions.append(_Completion(request, update))
        with self._lock:
            if not self._closed:
                for completion in completions:
                    self._ready.put(completion)

    def drain(self, accept: Callable[[HistoryRefreshUpdate], object]) -> int:
        """Owner decides how to merge/persist accepted data against current progress."""
        self._require_owner()
        accepted = 0
        while True:
            try:
                completion = self._ready.get_nowait()
            except Empty:
                return accepted
            request = completion.request
            if self._closed or self._latest.get(request.reference) != request.revision:
                continue
            self._latest.pop(request.reference)
            accept(completion.update)
            accepted += 1

    def invalidate(self, reference: HistoryEntryReference) -> None:
        self._require_owner()
        self._latest.pop(reference, None)

    def is_pending(self, request: HistoryRefreshRequest) -> bool:
        self._require_owner()
        return self._latest.get(request.reference) == request.revision

    def close(self) -> None:
        self._require_owner()
        with self._lock:
            self._closed = True
            self._latest.clear()
            while True:
                try:
                    self._ready.get_nowait()
                except Empty:
                    return
