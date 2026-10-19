"""Shared blocking-work driver; callers supply only task data and presentation."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from .actions import ActionAvailability, ActionBindings
from .loading_lifecycle import LoadingLifecycle
from .loading_messages import preparation_feedback_messages
from .loading_model import (
    CancelLoading,
    FailLoading,
    FinishLoading,
    LoadingInteraction,
    LoadingSnapshot,
    LoadingTaskSpec,
    UpdateLoading,
)
from .message_choices import MessageInput
from .transition import LoadingInput, PreparationCancelled, run_preparation

T = TypeVar("T")


class LoadingPreparation:
    def __init__(
        self,
        spec: LoadingTaskSpec,
        *,
        fd: int,
        render: Callable[[LoadingSnapshot], None],
        active: Callable[[], bool],
        bindings: Callable[[], ActionBindings],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if spec.interaction is not LoadingInteraction.BLOCKING or spec.transition:
            raise ValueError("Preparation driver requires a blocking action, not a frame transition")
        self.spec = spec
        self.lifecycle = LoadingLifecycle(clock=clock)
        self.token = self.lifecycle.start(spec)
        self.keys = LoadingInput(fd, on_key=self._on_key, cancel_keys=())
        self._render = render
        self._active = active
        self._bindings = bindings
        self._cancelled = False
        self._used = False

    def update(self, message: MessageInput) -> None:
        """Safe worker callback; lifecycle state changes only on the UI pump."""
        self.lifecycle.post(UpdateLoading(self.token, message))

    def _snapshot(self, *, feedback: bool = False) -> LoadingSnapshot | None:
        snapshots = self.lifecycle.snapshots(self.spec.owner, feedback=feedback)
        return snapshots[0] if snapshots else None

    def _on_key(self, key: str) -> None:
        if not key:
            return
        snapshot = self._snapshot()
        if snapshot is not None:
            action = snapshot.actions.resolve("SPACE" if key == " " else key)
            if action is not None:
                if action.availability is ActionAvailability.AVAILABLE:
                    if action.action.id == "back":
                        self._cancelled = True
                    elif action.action.id == "quit":
                        raise KeyboardInterrupt
                self.lifecycle.feedback(
                    self.token, action, messages=preparation_feedback_messages(action.action.id, snapshot.actions),
                )

    def _tick(self) -> None:
        self.lifecycle.drain()
        action = self.keys.poll()
        if action == "quit":
            raise KeyboardInterrupt
        if action == "cancel":
            snapshot = self._snapshot()
            if snapshot is not None:
                self._cancelled = any(
                    binding.action.id == "back" and binding.availability is ActionAvailability.AVAILABLE
                    for binding in snapshot.actions.items
                )

    def _paint(self) -> None:
        self.lifecycle.drain()
        snapshot = self._snapshot(feedback=True)
        if snapshot is None or not snapshot.visible or not self._active():
            return
        self._render(snapshot)

    def _suspend(self) -> None:
        self.lifecycle.suspend(self.token)

    def _resume(self) -> None:
        self.lifecycle.drain()
        self.lifecycle.rebind(self.token, self._bindings())
        self.lifecycle.resume(self.token)

    def run(self, prepare: Callable[[Callable[[MessageInput], None]], T]) -> T:
        if self._used:
            raise RuntimeError("Loading preparation can only be run once")
        self._used = True
        try:
            result = run_preparation(
                lambda: prepare(self.update), tick=self._tick,
                cancelled=lambda: self._cancelled, status_cb=self.update,
                ready_for_ui=lambda: not self.keys.has_pending_sequence,
                on_wait=self._paint, on_ui_enter=self._suspend, on_ui_return=self._resume,
            )
            self.lifecycle.drain()
            self.lifecycle.apply(FinishLoading(self.token))
            return result
        except (PreparationCancelled, KeyboardInterrupt):
            self.lifecycle.apply(CancelLoading(self.token))
            raise
        except BaseException:
            self.lifecycle.apply(FailLoading(self.token, "Unable to finish task"))
            raise
        finally:
            self.lifecycle.detach(self.spec.owner)
