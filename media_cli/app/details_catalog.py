"""Bounded session-scoped catalogs; jobs only touch detached show snapshots."""

from __future__ import annotations

import logging
import sys
import threading
from collections import OrderedDict
from concurrent.futures import Future
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from ..core.api import ProviderDependencyError
from ..core.reporting import status_sink_scope
from ..core.terminal import sanitize_terminal_text
from ..core.workers import DaemonExecutor
from ..providers import title_provider_id, title_provider_key
from .details_preparation import PreparedDetails, prepare_details_transition

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CatalogStatus:
    state: str
    result: PreparedDetails | None = None
    error: str = ''


class DetailsCatalogJobs:
    def __init__(self, *, executor: Any = None, capacity: int = 64):
        self.executor = executor if executor is not None else DaemonExecutor(max_workers=4, max_pending=4)
        self.capacity = max(1, capacity)
        self._jobs: OrderedDict[tuple[str, str, str, str], Future] = OrderedDict()
        self._lock = threading.Lock()

    def _key(self, session: str, show: dict, ttype: str) -> tuple[str, str, str, str]:
        return session, title_provider_key(show), str(title_provider_id(show)), ttype

    @staticmethod
    def _prepare(show: dict, ttype: str) -> PreparedDetails:
        # Abandoned jobs must never report into another screen's status sink.
        with status_sink_scope(lambda message, color: True):
            return prepare_details_transition(show, ttype)

    def read(self, session: str, show: dict, ttype: str) -> CatalogStatus:
        key = self._key(session, show, ttype)
        with self._lock:
            future = self._jobs.get(key)
            if future is None:
                while len(self._jobs) >= self.capacity:
                    _, discarded = self._jobs.popitem(last=False)
                    discarded.cancel()
                future = self.executor.submit(self._prepare, deepcopy(show), ttype)
                self._jobs[key] = future
            self._jobs.move_to_end(key)
        if not future.done():
            return CatalogStatus('loading')
        try:
            result = future.result()
        except ProviderDependencyError:
            raise  # Required setup failures still belong to the CLI owner.
        except Exception as error:
            _log.debug('Details catalog failed: provider=%s language=%s error=%s', key[1], ttype, type(error).__name__)
            return CatalogStatus('failed', error=sanitize_terminal_text(str(error))[:160] or 'Episode lookup failed.')
        if result.episode_ids:
            return CatalogStatus('loaded', result)
        error_message = sanitize_terminal_text(result.show.get('_episode_catalog_error', '')).strip()[:160]
        return CatalogStatus('failed' if error_message else 'empty', result, error_message)

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=True)

    def retry(self, session: str, show: dict, ttype: str) -> None:
        with self._lock:
            previous = self._jobs.pop(self._key(session, show, ttype), None)
        if previous is not None:
            previous.cancel()

    def publish(self, session: str, show: dict, ttype: str, result: PreparedDetails) -> None:
        """An explicit refresh supersedes any earlier session result."""
        completed: Future = Future()
        completed.set_result(deepcopy(result))
        with self._lock:
            previous = self._jobs.pop(self._key(session, show, ttype), None)
            self._jobs[self._key(session, show, ttype)] = completed
            while len(self._jobs) > self.capacity:
                _, discarded = self._jobs.popitem(last=False)
                discarded.cancel()
        if previous is not None:
            previous.cancel()


catalog_jobs = DetailsCatalogJobs()


def interactive_catalog(flags: Any, args: Any = None) -> bool:
    return bool(not flags.plain_mode and sys.stdin.isatty() and sys.stdout.isatty()
                and not getattr(args, 'episode', None) and not getattr(args, 'download', False))


def ready_episode_ids(show: dict, ttype: str) -> tuple[str, ...]:
    """Read known IDs only; counts never fabricate a playable catalog."""
    by_type = show.get('_episode_ids_by_ttype') or {}
    ids = by_type.get(ttype)
    if ids is None and show.get('_episode_ids_ttype') in (None, ttype):
        ids = show.get('_episode_ids')
    return tuple(str(value) for value in ids or ())


def accept_catalog_status(show: dict, ttype: str, status: CatalogStatus) -> None:
    """Project this language's active catalog, including explicit absence of IDs."""
    from .details_preparation import commit_details_transition

    if status.result is not None:
        commit_details_transition(show, status.result)
    ids = status.result.episode_ids if status.result is not None and status.state == 'loaded' else ()
    show['_episode_ids_ttype'] = ttype
    show['_episode_ids'] = list(ids)
    show.setdefault('_episode_ids_by_ttype', {})[ttype] = list(ids)
    show['_episode_catalog_state'] = 'loaded' if ids else 'unavailable'
    if ids:
        show.pop('_episode_catalog_error', None)
    else:
        show['_episode_catalog_error'] = status.error or 'No episodes available.'
        for key in ('_episode_labels','_episodes_map','_episodes_by_number'):
            show.pop(key, None)
