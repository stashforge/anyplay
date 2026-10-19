"""Owner-thread loading state, independent of execution and terminal rendering."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from itertools import count

from .actions import ActionAvailability, ActionBindings, ResolvedAction
from .loading_key_feedback import LoadingKeyFeedback
from .loading_model import (
    CancelLoading,
    FailLoading,
    FinishLoading,
    LoadingEvent,
    LoadingInteraction,
    LoadingOwner,
    LoadingPhase,
    LoadingSnapshot,
    LoadingTaskSpec,
    LoadingToken,
    UpdateLoading,
    _typed,
)
from .message_choices import MessageInput, MessagePicker, MessageRotation
from .picker_actions import loading_bindings

_LOG = logging.getLogger(__name__)
_INCARNATIONS = count(1)
_EVENT_TYPES = (UpdateLoading, FinishLoading, FailLoading, CancelLoading)


def _task_actions(spec: LoadingTaskSpec) -> ActionBindings:
    if spec.interaction is LoadingInteraction.BACKGROUND:
        return spec.actions
    blocked = loading_bindings(spec.actions)
    # Never let compatibility policy re-enable an explicitly blocked action.
    return ActionBindings(tuple(
        source if source.availability is ActionAvailability.BLOCKED else loading
        for source, loading in zip(spec.actions.items, blocked.items, strict=True)
    ))


@dataclass
class _Task:
    spec: LoadingTaskSpec
    started: float
    visible_since: float
    parent: LoadingToken | None
    actions: ActionBindings
    wording: MessageRotation
    phase: LoadingPhase = LoadingPhase.RUNNING
    suspended: bool = False
    feedback: LoadingKeyFeedback = field(default_factory=LoadingKeyFeedback)


class LoadingLifecycle:
    def __init__(
        self, *, clock: Callable[[], float] = time.monotonic,
        message_picker: MessagePicker | None = None,
        rotation_interval: Callable[[], float] | None = None,
    ) -> None:
        self._owner_thread = threading.current_thread()
        self._clock = clock
        self._message_picker = message_picker
        self._rotation_interval = rotation_interval
        self._tasks: dict[LoadingToken, _Task] = {}
        self._inbox: queue.SimpleQueue[LoadingEvent] = queue.SimpleQueue()

    def post(self, event: LoadingEvent) -> None:
        """Publish from a worker; only drain on the owner thread changes state."""
        if not isinstance(event, _EVENT_TYPES):
            raise TypeError("Loading event requires an addressed loading record")
        self._inbox.put(event)

    def drain(self) -> int:
        """Bound update work so an input/render pump can run between batches."""
        self._assert_owner()
        consumed = 0
        for _ in range(256):
            try:
                event = self._inbox.get_nowait()
            except queue.Empty:
                break
            self.apply(event)
            consumed += 1
        return consumed

    def _assert_owner(self) -> None:
        if threading.current_thread() is not self._owner_thread:
            raise RuntimeError("Loading lifecycle state requires its owner thread")

    @staticmethod
    def _log(token: LoadingToken, phase: str, elapsed: float = 0.0) -> None:
        _LOG.debug(
            "Loading %s: owner=%s activation=%s task=%s incarnation=%s elapsed=%.3fs",
            phase, token.owner.id, token.owner.activation, token.task_id,
            token.incarnation, elapsed,
        )

    def _find(self, token: LoadingToken) -> _Task | None:
        _typed(token, LoadingToken, "loading.token")
        task = self._tasks.get(token)
        if task is None:
            self._log(token, "stale event ignored")
        return task

    def start(self, spec: LoadingTaskSpec, *, parent: LoadingToken | None = None) -> LoadingToken:
        self._assert_owner()
        _typed(spec, LoadingTaskSpec, "loading.spec")
        if any(t.owner == spec.owner and t.task_id == spec.task_id for t in self._tasks):
            raise ValueError("Loading task ID is already active for this owner")
        if parent is not None:
            parent_task = self._find(parent)
            if parent_task is None or parent.owner != spec.owner:
                raise ValueError("Loading parent must be active with the same owner")
            if parent_task.phase is not LoadingPhase.RUNNING:
                raise ValueError("Loading parent must still be running")
        now = self._clock()
        token = LoadingToken(spec.owner, spec.task_id, next(_INCARNATIONS))
        wording = MessageRotation(
            spec.message, clock=self._clock, picker=self._message_picker, interval=self._rotation_interval,
        )
        self._tasks[token] = _Task(spec, now, now, parent, _task_actions(spec), wording)
        self._log(token, "started")
        return token

    def _retire(self, token: LoadingToken, phase: LoadingPhase) -> None:
        pending = [(token, phase)]
        while pending:
            current, outcome = pending.pop()
            task = self._tasks.pop(current)
            self._log(current, outcome.value, self._clock() - task.started)
            pending.extend(
                (child, LoadingPhase.DETACHED)
                for child, state in self._tasks.items() if state.parent == current
            )

    def apply(self, event: LoadingEvent) -> bool:
        self._assert_owner()
        if not isinstance(event, _EVENT_TYPES):
            raise TypeError("Loading event requires an addressed loading record")
        task = self._find(event.token)
        if task is None:
            return False
        if isinstance(event, (UpdateLoading, FinishLoading)) and task.phase is not LoadingPhase.RUNNING:
            self._log(event.token, "late work event ignored")
            return False
        if isinstance(event, UpdateLoading):
            task.wording.update(event.message, progress_changed=event.progress != task.spec.progress)
            task.spec = replace(task.spec, message=event.message, progress=event.progress)
        elif isinstance(event, FinishLoading):
            if task.spec.transition:
                task.phase = LoadingPhase.DATA_READY
                task.feedback.message(None, self._clock())
                self._log(event.token, task.phase.value, self._clock() - task.started)
            else:
                self._retire(event.token, LoadingPhase.SUCCEEDED)
        elif isinstance(event, FailLoading):
            self._retire(event.token, LoadingPhase.FAILED)
        else:
            self._retire(event.token, LoadingPhase.CANCELLED)
        return True

    def acknowledge_frame(self, token: LoadingToken) -> bool:
        self._assert_owner()
        task = self._find(token)
        if task is None:
            return False
        if not task.spec.transition or task.phase is not LoadingPhase.DATA_READY:
            raise ValueError("Frame acknowledgement requires a data-ready transition")
        self._retire(token, LoadingPhase.SUCCEEDED)
        return True

    def suspend(self, token: LoadingToken) -> bool:
        self._assert_owner()
        task = self._find(token)
        if task is None or task.suspended:
            return False
        task.suspended = True
        task.feedback.message(None, self._clock())
        self._log(token, "suspended", self._clock() - task.started)
        return True

    def resume(self, token: LoadingToken) -> bool:
        self._assert_owner()
        task = self._find(token)
        if task is None or not task.suspended:
            return False
        task.suspended = False
        task.visible_since = self._clock()
        task.wording.restart()
        self._log(token, "resumed", task.visible_since - task.started)
        return True

    def rebind(self, token: LoadingToken, actions: ActionBindings) -> bool:
        """Refresh source UI capabilities after a child screen returns."""
        self._assert_owner()
        _typed(actions, ActionBindings, "loading.actions")
        task = self._find(token)
        if task is None:
            return False
        task.spec = replace(task.spec, actions=actions)
        task.actions = _task_actions(task.spec)
        return True

    def detach(self, owner: LoadingOwner) -> None:
        self._assert_owner()
        _typed(owner, LoadingOwner, "loading.owner")
        for token in tuple(self._tasks):
            if token.owner == owner and token in self._tasks:
                self._retire(token, LoadingPhase.DETACHED)

    def feedback(self, token: LoadingToken, action: ResolvedAction, *, messages: MessageInput | None = None) -> bool:
        self._assert_owner()
        _typed(action, ResolvedAction, "loading.feedback.action")
        task = self._find(token)
        if (task is None or task.suspended or task.phase is not LoadingPhase.RUNNING
                or task.spec.interaction is not LoadingInteraction.BLOCKING):
            return False
        return task.feedback.show(action, token, self._clock(), messages=messages)

    def _message(self, token: LoadingToken, task: _Task, now: float, feedback: bool) -> str:
        original = task.wording.text(rotate=(
            task.phase is LoadingPhase.RUNNING and now - task.visible_since >= task.spec.delay
        ))
        override = task.feedback.message(token, now) if feedback else None
        return override or original

    def snapshots(self, owner: LoadingOwner, *, feedback: bool = False) -> tuple[LoadingSnapshot, ...]:
        self._assert_owner()
        _typed(owner, LoadingOwner, "loading.owner")
        now = self._clock()
        tasks = sorted(
            ((token, state) for token, state in self._tasks.items()
             if token.owner == owner and not state.suspended),
            key=lambda pair: pair[1].spec.interaction is LoadingInteraction.BACKGROUND,
        )
        return tuple(
            LoadingSnapshot(
                token, self._message(token, state, now, feedback), state.phase, state.spec.interaction,
                state.spec.placement, state.actions,
                now - state.visible_since >= state.spec.delay,
                now - state.started, state.spec.progress,
            )
            for token, state in tasks
        )
