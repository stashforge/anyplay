"""Owner-controlled player loading with immutable cross-thread snapshots."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import replace

from .loading_lifecycle import LoadingLifecycle
from .loading_model import (
    CancelLoading,
    FinishLoading,
    LoadingInteraction,
    LoadingPlacement,
    LoadingSnapshot,
    LoadingTaskSpec,
    LoadingToken,
    UpdateLoading,
    _duration,
)
from .message_choices import MessageInput


class PlayerLoadingState:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._owner = threading.current_thread()
        self._lifecycle = LoadingLifecycle(clock=clock)
        self._token: LoadingToken | None = None
        self._elapsed_offset = 0.0
        self._snapshot: LoadingSnapshot | None = None
        self._snapshot_lock = threading.Lock()

    @property
    def snapshot(self) -> LoadingSnapshot | None:
        with self._snapshot_lock:
            return self._snapshot

    def _assert_owner(self) -> None:
        if threading.current_thread() is not self._owner:
            raise RuntimeError("Player loading state requires its owner thread")

    def _publish(self, snapshot: LoadingSnapshot | None) -> None:
        with self._snapshot_lock:
            self._snapshot = snapshot

    def start(self, spec: LoadingTaskSpec, *, elapsed_offset: float = 0.0) -> None:
        self._assert_owner()
        _duration(elapsed_offset, "player.elapsed_offset")
        if not isinstance(spec, LoadingTaskSpec):
            raise TypeError("Player loading requires LoadingTaskSpec")
        if (
            spec.placement is not LoadingPlacement.PLAYER_STATUS
            or spec.interaction is not LoadingInteraction.BLOCKING
            or spec.transition
        ):
            raise ValueError("Player loading requires a blocking player status task")
        if self._token is not None:
            raise RuntimeError("Player loading task is already active")
        self._token = self._lifecycle.start(spec)
        self._elapsed_offset = elapsed_offset
        self.tick()

    def update(self, message: MessageInput) -> None:
        self._assert_owner()
        if self._token is None:
            raise RuntimeError("Player loading update requires an active task")
        self._lifecycle.apply(UpdateLoading(self._token, message))
        self.tick()

    def tick(self) -> None:
        self._assert_owner()
        if self._token is not None:
            snapshot = self._lifecycle.snapshots(self._token.owner)[0]
            self._publish(replace(snapshot, elapsed=snapshot.elapsed + self._elapsed_offset))

    def finish(self, *, cancelled: bool = False) -> None:
        self._assert_owner()
        if not isinstance(cancelled, bool):
            raise TypeError("Player loading cancellation requires bool")
        if self._token is not None:
            event = CancelLoading(self._token) if cancelled else FinishLoading(self._token)
            self._lifecycle.apply(event)
            self._token = None
        self._publish(None)
