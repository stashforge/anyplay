"""Bounded daemon workers for network jobs which cannot be forcibly cancelled."""

from __future__ import annotations

import queue
import threading
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future
from dataclasses import dataclass
from itertools import islice
from types import TracebackType
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")
U = TypeVar("U")


@dataclass(frozen=True)
class _Work:
    run: Callable[[], None]
    cancel: Callable[[], bool]


class DaemonExecutor:
    """Future-compatible I/O pool with bounded queued work and no exit join."""

    def __init__(self, *, max_workers: int = 4, max_pending: int = 4) -> None:
        if max_workers < 1 or max_pending < 1:
            raise ValueError("Worker and pending limits must be positive")
        self._limit = max_workers
        self._queue: queue.Queue[_Work] = queue.Queue(maxsize=max_pending)
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self._threads: tuple[threading.Thread, ...] = ()

    def submit(self, function: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> Future[T]:
        future: Future[T] = Future()

        def run() -> None:
            if not future.set_running_or_notify_cancel():
                return
            try:
                future.set_result(function(*args, **kwargs))
            except BaseException as exc:
                future.set_exception(exc)

        with self._lock:
            if self._closing.is_set():
                raise RuntimeError("Cannot submit after executor shutdown")
            if not self._threads:
                self._threads = tuple(
                    threading.Thread(target=self._loop, name=f"anyplay-io-{index}", daemon=True)
                    for index in range(self._limit)
                )
                for thread in self._threads:
                    thread.start()
            try:
                self._queue.put_nowait(_Work(run, future.cancel))
            except queue.Full:
                future.set_exception(RuntimeError("Background I/O capacity is full"))
        return future

    def _loop(self) -> None:
        while True:
            try:
                work = self._queue.get(timeout=0.05)
            except queue.Empty:
                if self._closing.is_set():
                    return
                continue
            try:
                work.run()
            finally:
                self._queue.task_done()

    def map(self, function: Callable[[T], U], values: Iterable[T]) -> Iterator[U]:
        iterator = iter(values)
        pending = deque(
            self.submit(function, value) for value in islice(iterator, min(self._limit, self._queue.maxsize))
        )
        while pending:
            yield pending.popleft().result()
            for value in islice(iterator, 1):
                pending.append(self.submit(function, value))

    def __enter__(self) -> DaemonExecutor:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.shutdown()

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        with self._lock:
            self._closing.set()
            if cancel_futures:
                while True:
                    try:
                        work = self._queue.get_nowait()
                    except queue.Empty:
                        break
                    work.cancel()
                    self._queue.task_done()
            threads = self._threads
        if wait:
            for thread in threads:
                if thread is not threading.current_thread():
                    thread.join()
