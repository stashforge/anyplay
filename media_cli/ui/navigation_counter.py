"""Semantic NavigationBar counters; counts are supplied, never inferred."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .styled_text import StyledText, TextRun


class CounterMode(Enum):
    FILTERED = "filtered"
    POSITION = "position"
    SELECTED = "selected"


@dataclass(frozen=True)
class NavigationCounter:
    mode: CounterMode
    value: int
    total: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, CounterMode):
            raise TypeError("NavigationCounter.mode requires CounterMode")
        if type(self.value) is not int:
            raise TypeError("NavigationCounter.value requires an integer")
        if self.value < 0:
            raise ValueError("NavigationCounter.value must be nonnegative")
        if self.mode is CounterMode.SELECTED:
            if self.total is not None:
                raise ValueError("Selected count must not supply a total")
            return
        if type(self.total) is not int:
            raise TypeError("Filtered/position counters require an integer total")
        if self.total < 0 or self.value > self.total:
            raise ValueError("Counter value must be within its nonnegative total")
        if self.mode is CounterMode.POSITION and self.total > 0 and self.value == 0:
            raise ValueError("Position is one-based; only an empty total permits position zero")

    def content(self) -> StyledText:
        """Produce composable text; the theme owns both count styles."""
        if self.mode is CounterMode.SELECTED:
            return StyledText((TextRun(f"{self.value} selected", "selected_count"),))
        return StyledText((TextRun(f"{self.value}/{self.total}", "counter"),))
