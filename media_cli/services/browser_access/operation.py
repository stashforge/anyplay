"""Caller-owned browser request context, isolated across operations and workers."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import Event

shutdown_event = Event()


@dataclass(frozen=True)
class BrowserOperation:
    cancel: Event | None = None
    status: Callable[[str], None] | None = None
    incognito: bool | None = None


_DEFAULT_OPERATION = BrowserOperation()
_operation = ContextVar("browser_operation", default=_DEFAULT_OPERATION)


def current_operation() -> BrowserOperation:
    return _operation.get()


@contextmanager
def browser_operation(*, cancel: Event | None = None, status: Callable[[str], None] | None = None,
                      incognito: bool | None = None) -> Iterator[BrowserOperation]:
    parent = current_operation()
    if parent.cancel is not None and cancel is not None and parent.cancel is not cancel:
        from .process import _RequestCancellation
        cancel = _RequestCancellation(parent.cancel, cancel)
    value = BrowserOperation(cancel or parent.cancel, status or parent.status,
                             parent.incognito if incognito is None else incognito)
    token = _operation.set(value)
    try:
        yield value
    finally:
        _operation.reset(token)
