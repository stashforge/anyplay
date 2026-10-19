"""Player presentation adapter for the shared main-thread preparation pump."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import TypeVar

from ..core.reporting import status_sink_scope
from .loading_model import LoadingPlacement, LoadingSnapshot, LoadingTaskSpec
from .loading_preparation import LoadingPreparation
from .message_choices import MessageInput

T = TypeVar("T")


def run_player_preparation(
    spec: LoadingTaskSpec,
    prepare: Callable[[Callable[[MessageInput], None]], T],
    *,
    fd: int,
    render: Callable[[LoadingSnapshot], None],
    report: Callable[[str, str], None],
    active: Callable[[], bool],
    clock: Callable[[], float] = time.monotonic,
) -> T:
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Player preparation requires the main UI thread")
    if not isinstance(spec, LoadingTaskSpec):
        raise TypeError("Player preparation requires LoadingTaskSpec")
    if spec.placement is not LoadingPlacement.PLAYER_STATUS:
        raise ValueError("Player preparation requires a player status task")

    pending: deque[tuple[str, str]] = deque(maxlen=8)
    lock = threading.Lock()
    accepting = True

    def sink(message: str, color: str) -> bool:
        with lock:
            if accepting:
                pending.append((message, color))
        # Abandoned workers must not fall through to a newer UI or the shell.
        return True

    def drain_reports() -> None:
        with lock:
            messages = tuple(pending)
            pending.clear()
        for message, color in messages:
            if active():
                report(message, color)

    def paint(snapshot: LoadingSnapshot) -> None:
        drain_reports()
        if active():
            render(snapshot)

    driver = LoadingPreparation(
        spec, fd=fd, render=paint, active=active,
        bindings=lambda: spec.actions, clock=clock,
    )

    def work(status: Callable[[MessageInput], None]) -> T:
        with status_sink_scope(sink):
            return prepare(status)

    try:
        result = driver.run(work)
        drain_reports()
        return result
    finally:
        with lock:
            accepting = False
            pending.clear()
