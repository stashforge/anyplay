"""Validated UI wording and clock-driven selection, without rendering or timers."""

from __future__ import annotations

import math
import random
import time
from collections.abc import Callable

MessageInput = str | tuple[str, ...] | list[str]
MAX_MESSAGE_CHOICES = 10
MAX_MESSAGE_LENGTH = 120


def normalize_messages(value: MessageInput, *, field: str = "message") -> tuple[str, ...]:
    values: tuple[str, ...]
    if isinstance(value, str):
        values = (value,)
    elif isinstance(value, (tuple, list)):
        if not value:
            raise ValueError(f"{field} requires a nonempty message collection")
        if len(value) > MAX_MESSAGE_CHOICES:
            raise ValueError(f"{field} accepts at most {MAX_MESSAGE_CHOICES} alternatives")
        values = tuple(value)
    else:
        raise TypeError(f"{field} requires str or a list/tuple of strings")
    normalized = []
    for index, message in enumerate(values):
        name = f"{field}[{index}]"
        if not isinstance(message, str):
            raise TypeError(f"{name} requires str")
        if len(message) > MAX_MESSAGE_LENGTH:
            raise ValueError(f"{name} accepts at most {MAX_MESSAGE_LENGTH} characters")
        if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in message):
            raise ValueError(f"{name} cannot contain control characters or terminal styling")
        message = message.strip()
        if not message:
            raise ValueError(f"{name} requires nonblank text")
        if message in normalized:
            raise ValueError(f"{name} is a duplicate alternative")
        normalized.append(message)
    return tuple(normalized)


class MessagePicker:
    """Remember only the last choice, not an unbounded history of task messages."""

    def __init__(self, *, choose: Callable[[tuple[str, ...]], str] = random.choice) -> None:
        self._choose = choose
        self._previous: str | None = None

    def pick(self, messages: MessageInput, *, previous: str | None = None) -> str:
        options = normalize_messages(messages)
        candidates = tuple(option for option in options if option not in (previous, self._previous))
        if not candidates:
            candidates = tuple(option for option in options if option != previous) or options
        choice = candidates[0] if len(candidates) == 1 else self._choose(candidates)
        if choice not in candidates:
            raise ValueError("Message choice must be one of the supplied alternatives")
        self._previous = choice
        return choice


_DEFAULT_PICKER = MessagePicker()


def _rotation_interval() -> float:
    return random.uniform(3.0, 5.0)


class MessageRotation:
    def __init__(
        self,
        messages: MessageInput,
        *,
        clock: Callable[[], float] = time.monotonic,
        picker: MessagePicker | None = None,
        interval: Callable[[], float] | None = None,
    ) -> None:
        self._clock = clock
        self._picker = picker if picker is not None else _DEFAULT_PICKER
        self._interval = interval if interval is not None else _rotation_interval
        self._options = normalize_messages(messages)
        self.current = self._picker.pick(self._options)
        self._next_at = math.inf
        self.restart()

    def restart(self) -> None:
        if len(self._options) == 1:
            self._next_at = math.inf
            return
        delay = self._interval()
        if isinstance(delay, bool) or not isinstance(delay, (int, float)):
            raise TypeError("Message rotation interval requires a number")
        if not math.isfinite(delay) or not 3.0 <= delay <= 5.0:
            raise ValueError("Message rotation interval must be between 3 and 5 seconds")
        self._next_at = self._clock() + delay

    def update(self, messages: MessageInput, *, progress_changed: bool = False) -> None:
        options = normalize_messages(messages)
        if options != self._options:
            self._options = options
            self.current = self._picker.pick(options, previous=self.current)
            self.restart()
        elif progress_changed:
            self.restart()

    def text(self, *, rotate: bool = True) -> str:
        if rotate and self._clock() >= self._next_at:
            self.current = self._picker.pick(self._options, previous=self.current)
            self.restart()
        return self.current
