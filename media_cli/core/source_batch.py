"""Bounded, reusable source jobs shared by foreground and background owners."""

import json
import threading
from concurrent.futures import FIRST_COMPLETED, CancelledError, wait

from . import reporting
from .workers import DaemonExecutor

_pool = None
_pool_lock = threading.Lock()


def _executor():
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = DaemonExecutor(max_workers=3, max_pending=128)
        return _pool


def source_identity(source):
    return json.dumps(source, sort_keys=True, default=str)


class SourceBatch:
    def __init__(self, resolve, *, cancel_check=None):
        self.resolve = resolve
        self.futures = {}
        self.closed = threading.Event()
        self._cancel_check = cancel_check

    def _cancelled(self):
        check = self._cancel_check
        return self.closed.is_set() or bool(check and check())

    def detach_cancel_check(self):
        """An accepted batch is now owned by background extraction, not its caller."""
        self._cancel_check = None

    def _run(self, source):
        if self._cancelled():
            raise CancelledError
        # A retired job must never print or report into a later screen. The
        # coordinator owns visible progress; normal diagnostic logging remains.
        with reporting.status_sink_scope(lambda message, color: True):
            return self.resolve(source)

    def _fill(self, sources):
        active = sum(not future.done() for future in self.futures.values())
        for source in sources:
            if self._cancelled() or active >= 3:
                break
            key = source_identity(source)
            if key not in self.futures:
                self.futures[key] = _executor().submit(self._run, source)
                active += 1

    def poll(self, sources, seen, timeout=.05):
        ready = [(source, self.futures[source_identity(source)]) for source in sources
                 if source_identity(source) not in seen and source_identity(source) in self.futures
                 and self.futures[source_identity(source)].done()]
        if not ready:
            self._fill(sources)
            pending = [future for future in self.futures.values() if not future.done()]
            if pending:
                wait(pending, timeout=timeout, return_when=FIRST_COMPLETED)
        return ready

    def get(self, source, *, retry=False, cancel_check=None):
        key = source_identity(source)
        if retry:
            self.futures.pop(key, None)
        while not self._cancelled() and not (cancel_check and cancel_check()):
            self._fill([source])
            future = self.futures.get(key)
            if future is not None and future.done():
                return future.result()
            pending = [f for f in self.futures.values() if not f.done()]
            if pending:
                wait(pending, timeout=.05, return_when=FIRST_COMPLETED)
        raise CancelledError

    def close(self):
        self.closed.set()
        for future in self.futures.values():
            future.cancel()
