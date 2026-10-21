"""Controlled setting rows; application callbacks own state and side effects."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generic, TypeVar

from .row_adjustment import cycle_value
from .view_model import Inherit, ViewItem

if TYPE_CHECKING:
    from .view_session import UiEvent

_T = TypeVar("_T")


@dataclass(frozen=True)
class ChoiceSetting(Generic[_T]):
    id: str
    label: str
    value: _T
    choices: Sequence[tuple[_T, str]]
    on_change: Callable[[_T], object] = field(compare=False, repr=False)
    hint: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        if not callable(self.on_change):
            raise TypeError("Setting on_change requires a callable")
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("Setting label requires a nonblank string")
        choices = tuple(self.choices)
        if not choices:
            raise ValueError("Setting requires at least one choice")
        values = []
        for value, label in choices:
            if value in values:
                raise ValueError("Setting choice values must be unique")
            if not isinstance(label, str) or not label.strip():
                raise ValueError("Setting choice labels require nonblank strings")
            ViewItem(self.id, label, details=Inherit)
            values.append(value)
        if self.value not in values:
            raise ValueError("Setting value must belong to its choices")
        object.__setattr__(self, "choices", tuple((value, label) for value, label in choices))
        self.item()

    def item(self) -> ViewItem:
        label = next(label for value, label in self.choices if value == self.value)
        return ViewItem(self.id, f"{self.label}: {label}", hint=self.hint, enabled=self.enabled,
                        adjustable=self.enabled, details=Inherit, reference=self.id)

    def handle_event(self, event: UiEvent) -> bool:
        """Consume matching events already validated by the owning ViewSession."""
        if not self.enabled or event.item_id != self.id:
            return False
        if event.kind == "select":
            direction = 1
        elif event.kind == "action" and event.value in ("increase", "decrease"):
            direction = 1 if event.value == "increase" else -1
        else:
            return False
        return self.change(direction)

    def change(self, direction: int = 1) -> bool:
        """Request a change from adapters that already supply a semantic direction."""
        if not self.enabled:
            return False
        value = cycle_value(self.value, tuple(value for value, _ in self.choices), direction)
        if value == self.value:
            return False
        self.on_change(value)
        return True


def toggle_setting(
    id: str, label: str, value: bool, on_change: Callable[[bool], object], *,
    hint: str = "", enabled: bool = True, off_label: str = "Off", on_label: str = "On",
) -> ChoiceSetting[bool]:
    if type(value) is not bool:
        raise TypeError("Toggle value requires a boolean")
    return ChoiceSetting(id, label, value, ((False, off_label), (True, on_label)),
                         on_change, hint=hint, enabled=enabled)
