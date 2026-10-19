"""Immutable loading task data; no rendering, scheduling, or terminal ownership."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from .actions import ActionBindings
from .message_choices import MessageInput, normalize_messages


def _text(value: str, field: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{field} requires str")
    if not value.strip() or any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
        raise ValueError(f"{field} requires nonblank plain text without controls")


def _count(value: int, field: str, *, minimum: int = 0) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{field} requires int, not bool")
    if value < minimum:
        raise ValueError(f"{field} must be >= {minimum}")


def _duration(value: float, field: str) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{field} requires a finite number")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{field} requires a finite nonnegative number")


def _typed(value: object, expected: type, field: str) -> None:
    if not isinstance(value, expected):
        raise TypeError(f"{field} requires {expected.__name__}")


class LoadingInteraction(Enum):
    BACKGROUND = "background"
    BLOCKING = "blocking"


class LoadingPlacement(Enum):
    FOOTER = "footer"
    PLAYER_STATUS = "player_status"


class LoadingPhase(Enum):
    RUNNING = "running"
    DATA_READY = "data_ready"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DETACHED = "detached"


@dataclass(frozen=True)
class LoadingOwner:
    id: str
    activation: int

    def __post_init__(self) -> None:
        _text(self.id, "owner.id")
        _count(self.activation, "owner.activation")


@dataclass(frozen=True)
class LoadingProgress:
    completed: int
    total: int | None = None

    def __post_init__(self) -> None:
        _count(self.completed, "progress.completed")
        if self.total is not None:
            _count(self.total, "progress.total")
            if self.completed > self.total:
                raise ValueError("progress.completed cannot exceed total")


@dataclass(frozen=True)
class LoadingTaskSpec:
    task_id: str
    owner: LoadingOwner
    message: MessageInput
    interaction: LoadingInteraction
    placement: LoadingPlacement
    actions: ActionBindings
    delay: float = 0.15
    transition: bool = False
    progress: LoadingProgress | None = None

    def __post_init__(self) -> None:
        _text(self.task_id, "task.task_id")
        _typed(self.owner, LoadingOwner, "task.owner")
        choices = normalize_messages(self.message, field="task.message")
        object.__setattr__(self, "message", choices[0] if isinstance(self.message, str) else choices)
        _typed(self.interaction, LoadingInteraction, "task.interaction")
        _typed(self.placement, LoadingPlacement, "task.placement")
        _typed(self.actions, ActionBindings, "task.actions")
        _duration(self.delay, "task.delay")
        _typed(self.transition, bool, "task.transition")
        if self.progress is not None:
            _typed(self.progress, LoadingProgress, "task.progress")


@dataclass(frozen=True)
class LoadingToken:
    owner: LoadingOwner
    task_id: str
    incarnation: int

    def __post_init__(self) -> None:
        _typed(self.owner, LoadingOwner, "token.owner")
        _text(self.task_id, "token.task_id")
        _count(self.incarnation, "token.incarnation", minimum=1)


@dataclass(frozen=True)
class UpdateLoading:
    token: LoadingToken
    message: MessageInput
    progress: LoadingProgress | None = None

    def __post_init__(self) -> None:
        _typed(self.token, LoadingToken, "update.token")
        choices = normalize_messages(self.message, field="update.message")
        object.__setattr__(self, "message", choices[0] if isinstance(self.message, str) else choices)
        if self.progress is not None:
            _typed(self.progress, LoadingProgress, "update.progress")


@dataclass(frozen=True)
class FinishLoading:
    token: LoadingToken

    def __post_init__(self) -> None:
        _typed(self.token, LoadingToken, "finish.token")


@dataclass(frozen=True)
class FailLoading:
    token: LoadingToken
    message: str

    def __post_init__(self) -> None:
        _typed(self.token, LoadingToken, "fail.token")
        _text(self.message, "fail.message")
        normalize_messages(self.message, field="fail.message")


@dataclass(frozen=True)
class CancelLoading:
    token: LoadingToken

    def __post_init__(self) -> None:
        _typed(self.token, LoadingToken, "cancel.token")


LoadingEvent = UpdateLoading | FinishLoading | FailLoading | CancelLoading


@dataclass(frozen=True)
class LoadingSnapshot:
    token: LoadingToken
    message: str
    phase: LoadingPhase
    interaction: LoadingInteraction
    placement: LoadingPlacement
    actions: ActionBindings
    visible: bool
    elapsed: float
    progress: LoadingProgress | None

    def __post_init__(self) -> None:
        _typed(self.token, LoadingToken, "snapshot.token")
        _text(self.message, "snapshot.message")
        normalize_messages(self.message, field="snapshot.message")
        _typed(self.phase, LoadingPhase, "snapshot.phase")
        _typed(self.interaction, LoadingInteraction, "snapshot.interaction")
        _typed(self.placement, LoadingPlacement, "snapshot.placement")
        _typed(self.actions, ActionBindings, "snapshot.actions")
        _typed(self.visible, bool, "snapshot.visible")
        _duration(self.elapsed, "snapshot.elapsed")
        if self.progress is not None:
            _typed(self.progress, LoadingProgress, "snapshot.progress")
