"""Bounded background preparation with a responsive, caller-owned UI pump."""

from __future__ import annotations

import logging
import os
import queue
import select
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from contextvars import copy_context
from dataclasses import dataclass
from types import TracebackType
from typing import Generic, TypeVar, cast

from .key_codes import decode_key_bytes
from .message_choices import MessageInput

T = TypeVar("T")
_log = logging.getLogger(__name__)
_slots = threading.BoundedSemaphore(4)
_worker = threading.local()


class PreparationCancelled(Exception):
    """The caller abandoned this preparation; its result must not be applied."""


@dataclass(frozen=True)
class PreparedResult(Generic[T]):
    value: T | None = None
    error: BaseException | None = None
    traceback: TracebackType | None = None


def in_preparation() -> bool:
    return bool(getattr(_worker, "active", False))


class LoadingDelay:
    """Delay status painting, never input handling or preparation itself."""

    def __init__(self, *, delay: float = 0.15, clock: Callable[[], float] = time.monotonic) -> None:
        self._delay = delay
        self._clock = clock
        self.reset()

    def reset(self) -> None:
        self._ready_at = self._clock() + self._delay

    @property
    def visible(self) -> bool:
        return self._clock() >= self._ready_at


def set_preparation_status(message: MessageInput) -> None:
    callback = getattr(_worker, "status_cb", None)
    if callback is not None:
        callback(message)


def call_on_ui_thread(function: Callable[[], T]) -> T:
    """Explicitly dispatch an internal screen to its owning UI pump."""
    if not in_preparation():
        return function()
    calls: queue.Queue[Callable[[], None]] = _worker.ui_calls
    abandoned: threading.Event = _worker.abandoned
    result: Future[T] = Future()
    done = threading.Event()

    def invoke() -> None:
        try:
            result.set_result(function())
        except BaseException as exc:
            result.set_exception(exc)
        finally:
            done.set()

    if abandoned.is_set():
        raise PreparationCancelled
    calls.put_nowait(invoke)
    while not done.wait(0.01):
        if abandoned.is_set():
            raise PreparationCancelled
    if abandoned.is_set():
        raise PreparationCancelled
    set_preparation_status("Preparing screen…")
    return result.result()


def run_preparation(
    prepare: Callable[[], T],
    *,
    tick: Callable[[], None],
    cancelled: Callable[[], bool] = lambda: False,
    status_cb: Callable[[MessageInput], None] | None = None,
    ready_for_ui: Callable[[], bool] = lambda: True,
    on_wait: Callable[[], None] = lambda: None,
    on_ui_enter: Callable[[], None] = lambda: None,
    on_ui_return: Callable[[], None] = lambda: None,
) -> T:
    """Keep input/render ticks on the caller thread; never queue unbounded jobs."""
    if in_preparation():
        return prepare()
    slots = _slots
    while not slots.acquire(blocking=False):
        tick()
        if cancelled():
            raise PreparationCancelled
        on_wait()
        time.sleep(0.04)
    result: queue.Queue[PreparedResult[T]] = queue.Queue(maxsize=1)
    started = time.monotonic()
    abandoned = threading.Event()
    ui_calls: queue.Queue[Callable[[], None]] = queue.Queue(maxsize=1)

    def work() -> None:
        _worker.active = True
        _worker.ui_calls = ui_calls
        _worker.abandoned = abandoned
        _worker.status_cb = status_cb
        try:
            value = prepare()
            outcome = PreparedResult(value=value)
        except BaseException as exc:
            _log.debug("Preparation failed", exc_info=True)
            outcome = PreparedResult[T](error=exc, traceback=exc.__traceback__)
        finally:
            _worker.active = False
            _worker.status_cb = None
            slots.release()
        if abandoned.is_set():
            _log.debug("Discarded abandoned preparation after %.3fs", time.monotonic() - started)
        else:
            result.put_nowait(outcome)

    context = copy_context()
    thread = threading.Thread(target=lambda: context.run(work), name="anyplay-preparation", daemon=True)
    try:
        thread.start()
    except BaseException:
        slots.release()
        raise
    _log.debug("Preparation started")
    completed: PreparedResult[T] | None = None
    try:
        while True:
            tick()
            if cancelled():
                raise PreparationCancelled
            if not ready_for_ui():
                on_wait()
                time.sleep(0.01)
                continue
            try:
                invoke = ui_calls.get_nowait()
            except queue.Empty:
                pass
            else:
                on_ui_enter()
                try:
                    invoke()
                finally:
                    on_ui_return()
            try:
                if completed is None:
                    completed = result.get(timeout=0.04)
            except queue.Empty:
                on_wait()
                continue
            # Consume any input that arrived at completion before accepting data.
            tick()
            if cancelled():
                raise PreparationCancelled
            if not ready_for_ui():
                continue
            if completed.error is not None:
                raise completed.error.with_traceback(completed.traceback)
            _log.debug("Preparation completed in %.3fs", time.monotonic() - started)
            return cast(T, completed.value)
    except BaseException:
        abandoned.set()
        _log.debug("Preparation abandoned after %.3fs", time.monotonic() - started)
        raise


class LoadingInput:
    """Consume loading keys without blocking on partial terminal sequences."""

    def __init__(
        self, fd: int, *, on_key: Callable[[str], None] | None = None,
        cancel_keys: tuple[str, ...] = ("ESC", "LEFT"),
    ) -> None:
        self.fd = fd
        self._on_key = on_key
        self._cancel_keys = cancel_keys
        self._sequence = bytearray()
        self._since = 0.0

    @property
    def has_pending_sequence(self) -> bool:
        return bool(self._sequence)

    def _dispatch_key(self, key: str) -> str | None:
        if key in self._cancel_keys:
            return "cancel"
        if self._on_key is not None:
            self._on_key(key)
        return None

    def poll(self) -> str | None:
        action = None
        # Bound work per tick even when a terminal continuously sends input.
        for _ in range(16):
            if not select.select([self.fd], [], [], 0)[0]:
                break
            data = os.read(self.fd, 4096)
            if not data:
                return "quit"
            received = self.feed(data)
            if received == "quit" or (received == "cancel" and action != "quit"):
                action = received
        expired = self.feed(b"")
        return action if action == "quit" else expired or action

    def feed(self, data: bytes) -> str | None:
        """Decode caller-read bytes; an empty feed checks the bare-Esc timeout."""
        if not isinstance(data, bytes):
            raise TypeError("Loading input feed requires bytes")
        action = None
        for value in data:
            if value == 3:
                action = "quit"
                self._sequence.clear()
            elif value == 27:
                self._sequence[:] = b"\x1b"
                self._since = time.monotonic()
            elif self._sequence:
                if len(self._sequence) < 64:
                    self._sequence.append(value)
                if len(self._sequence) == 2 and value not in (ord("["), ord("O")):
                    if self._on_key is not None:
                        self._on_key(decode_key_bytes(bytes(self._sequence)))
                    self._sequence.clear()
                elif len(self._sequence) >= 3 and 0x40 <= value <= 0x7E:
                    received = self._dispatch_key(decode_key_bytes(bytes(self._sequence)))
                    if received == "cancel" and action != "quit":
                        action = "cancel"
                    self._sequence.clear()
            elif self._on_key is not None:
                self._on_key(decode_key_bytes(bytes((value,))))
        if self._sequence == b"\x1b" and time.monotonic() - self._since >= 0.1:
            received = self._dispatch_key("ESC")
            if received == "cancel" and action != "quit":
                action = "cancel"
            self._sequence.clear()
        return action
