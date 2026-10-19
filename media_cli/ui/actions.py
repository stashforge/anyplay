"""Immutable key bindings and explicit action availability, without input ownership."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .styled_text import TextRun
from .view_model import ViewAction


def normalize_key(key: str) -> str:
    """Canonicalize symbolic decoder names, not raw terminal escape sequences."""
    if not isinstance(key, str):
        raise TypeError("Key requires str")
    TextRun(key, "actions")
    if not key or any(character.isspace() for character in key):
        raise ValueError("Key requires a nonblank symbolic name without whitespace")
    canonical = key.upper()
    return {"SHIFT_TAB": "BTAB", "PAGEUP": "PAGE_UP", "PAGEDOWN": "PAGE_DOWN"}.get(canonical, canonical)


def key_label(key: str) -> str:
    canonical = normalize_key(key)
    if len(key) == 1:
        return key
    labels = {
        "BTAB": "Shift+Tab",
        "ENTER": "Enter",
        "RIGHT": "Right",
        "LEFT": "Left",
        "UP": "Up",
        "DOWN": "Down",
        "ESC": "Esc",
        "TAB": "Tab",
        "SPACE": "Space",
        "BACKSPACE": "Backspace",
        "DELETE": "Delete",
        "HOME": "Home",
        "END": "End",
        "PAGE_UP": "Page Up",
        "PAGE_DOWN": "Page Down",
    }
    if canonical in labels:
        return labels[canonical]
    modifier, separator, rest = canonical.partition("_")
    if separator and rest and modifier in {"CTRL", "ALT", "SHIFT"}:
        return f"{modifier.title()}+{key_label(rest)}"
    return canonical


class ActionAvailability(Enum):
    AVAILABLE = "available"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ResolvedAction:
    action: ViewAction
    availability: ActionAvailability = ActionAvailability.AVAILABLE
    blocked_message: str | None = None
    keys: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.action, ViewAction):
            raise TypeError("ResolvedAction.action requires ViewAction")
        if not isinstance(self.availability, ActionAvailability):
            raise TypeError("ResolvedAction.availability requires ActionAvailability")
        TextRun(self.action.label, "actions")
        if self.availability is ActionAvailability.BLOCKED:
            if not isinstance(self.blocked_message, str) or not self.blocked_message.strip():
                raise ValueError("Blocked action requires a nonblank blocked_message")
            TextRun(self.blocked_message, "actions")
        elif self.blocked_message is not None:
            raise ValueError("Available action cannot carry a blocked_message")
        object.__setattr__(self, "keys", tuple(dict.fromkeys(normalize_key(key) for key in self.action.keys)))

    @property
    def key_labels(self) -> tuple[str, ...]:
        labels: dict[str, str] = {}
        for key in self.action.keys:
            labels.setdefault(normalize_key(key), key_label(key))
        return tuple(labels.values())


@dataclass(frozen=True)
class ActionBindings:
    items: tuple[ResolvedAction, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple) or any(not isinstance(item, ResolvedAction) for item in self.items):
            raise TypeError("ActionBindings.items requires an immutable tuple of ResolvedAction values")
        keys: set[str] = set()
        groups: dict[str, bool] = {}
        for item in self.items:
            action_id = item.action.id
            if action_id in groups and (not groups[action_id] or not item.keys):
                raise ValueError(f"Repeated action ID {action_id!r} requires separate key groups")
            groups[action_id] = bool(item.keys)
            for key in item.keys:
                if key in keys:
                    raise ValueError(f"Conflicting action key: {key!r}")
                keys.add(key)

    def resolve(self, key: str) -> ResolvedAction | None:
        canonical = normalize_key(key)
        return next((item for item in self.items if canonical in item.keys), None)
