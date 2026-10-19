"""Type-level row layout, independent of labels, providers and key handling."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .view_model import ViewItem


class RowKind(Enum):
    OPTION = "option"
    DISABLED = "disabled"
    HEADING = "heading"


def row_kind(item: ViewItem) -> RowKind:
    """Headings retain their presentation type regardless of enabled state."""
    from .view_model import ViewItem

    if not isinstance(item, ViewItem):
        raise TypeError("row_kind requires ViewItem")
    if item.kind == "heading":
        return RowKind.HEADING
    return RowKind.OPTION if item.selectable else RowKind.DISABLED


@dataclass(frozen=True)
class RowIndentation:
    """Extra leading columns before the renderer's existing marker/content gutter.

    Supply each type explicitly. Every row of a type uses the same value;
    individual item prefixes remain content, not indentation overrides.
    """

    option: int
    disabled: int
    heading: int

    def __post_init__(self) -> None:
        for name, value in (("option", self.option), ("disabled", self.disabled), ("heading", self.heading)):
            if type(value) is not int:
                raise TypeError(f"RowIndentation.{name} requires integer columns")
            if value < 0:
                raise ValueError(f"RowIndentation.{name} must be nonnegative")

    def columns_for(self, kind: RowKind) -> int:
        if not isinstance(kind, RowKind):
            raise TypeError("RowIndentation.columns_for requires RowKind")
        if kind is RowKind.OPTION:
            return self.option
        return self.disabled if kind is RowKind.DISABLED else self.heading


DEFAULT_ROW_INDENTATION = RowIndentation(option=0, disabled=0, heading=0)
