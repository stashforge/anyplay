"""Apply the shared feedback policy to capabilities of the source picker."""

import time
from collections.abc import Callable

from .actions import ActionAvailability, ActionBindings
from .loading_key_feedback import LoadingKeyFeedback
from .message_choices import MessageInput
from .picker_actions import loading_bindings


class LoadingControls:
    def __init__(self, bindings: ActionBindings, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.reset(bindings)

    def reset(self, bindings: ActionBindings) -> None:
        self._bindings = loading_bindings(bindings)
        self._feedback = LoadingKeyFeedback()

    def on_key(self, key: str, *, messages: MessageInput | None = None) -> bool:
        if not key:
            return False
        action = self._bindings.resolve("SPACE" if key == " " else key)
        if action is None or action.availability is not ActionAvailability.BLOCKED:
            return False
        self._feedback.show(action, self, self._clock(), messages=messages)
        return True

    def message(self, original: str) -> str:
        return self._feedback.message(self, self._clock()) or original
