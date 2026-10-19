"""Short, rate-limited loading feedback with no timers or terminal ownership."""

from collections.abc import Hashable

from .actions import ActionAvailability, ResolvedAction
from .message_choices import MessageInput, MessagePicker, normalize_messages

_MESSAGES = (
    "Hang on, still loading.",
    "Still working. One moment.",
    "Not ready just yet. Still loading.",
)


class LoadingKeyFeedback:
    def __init__(self, *, picker: MessagePicker | None = None) -> None:
        self._scope: Hashable | None = None
        self._text: str | None = None
        self._expires = 0.0
        self._next_allowed = 0.0
        self._index = 0
        self._picker = picker if picker is not None else MessagePicker()

    def show(
        self, action: ResolvedAction, scope: Hashable, now: float,
        *, messages: MessageInput | None = None,
    ) -> bool:
        choices = normalize_messages(messages, field="feedback.messages") if messages is not None else None
        if action.availability is not ActionAvailability.BLOCKED:
            return False
        if scope != self._scope:
            self._scope = scope
            self._next_allowed = 0.0
        if now < self._next_allowed:
            return False
        self._text = (
            self._picker.pick(choices, previous=self._text) if choices is not None
            else _MESSAGES[self._index % len(_MESSAGES)]
        )
        self._index += 1
        self._expires = now + 1.0
        self._next_allowed = now + 1.5
        return True

    def message(self, scope: Hashable | None, now: float) -> str | None:
        if scope is None or scope != self._scope:
            self._scope, self._text = None, None
            return None
        if now >= self._expires:
            self._text = None
        return self._text
