"""Visual hidden-row hints; viewport geometry and list ordering stay outside."""

from __future__ import annotations

from dataclasses import dataclass

from .styled_text import StyledText, TextRun


@dataclass(frozen=True)
class ScrollIndicators:
    hidden_above: int
    hidden_below: int

    def __post_init__(self) -> None:
        for name, value in (("hidden_above", self.hidden_above), ("hidden_below", self.hidden_below)):
            if type(value) is not int:
                raise TypeError(f"ScrollIndicators.{name} requires an integer row count")
            if value < 0:
                raise ValueError(f"ScrollIndicators.{name} must be nonnegative")

    def content(self) -> StyledText:
        runs: list[TextRun] = []
        for count, symbol in ((self.hidden_above, "\u25b2"), (self.hidden_below, "\u25bc")):
            if count:
                if runs:
                    runs.append(TextRun(" ", "separator"))
                runs.append(TextRun(f"{symbol}{count}", "scroll_indicator"))
        return StyledText(tuple(runs))
