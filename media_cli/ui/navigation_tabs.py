"""Semantic tab presentation without tab switching or screen ownership."""

from __future__ import annotations

from dataclasses import dataclass

from .styled_text import StyledText, TextRun
from .view_model import ViewTab


@dataclass(frozen=True)
class NavigationTabs:
    items: tuple[ViewTab, ...]
    active_id: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple) or any(not isinstance(item, ViewTab) for item in self.items):
            raise TypeError("NavigationTabs.items requires an immutable tuple of ViewTab values")
        if self.active_id is not None and not isinstance(self.active_id, str):
            raise TypeError("NavigationTabs.active_id requires str or None")
        ids = set()
        for item in self.items:
            if item.id in ids:
                raise ValueError(f"Duplicate navigation tab ID: {item.id!r}")
            ids.add(item.id)
            if not item.label.strip():
                raise ValueError(f"Navigation tab {item.id!r} requires a nonblank label")
            TextRun(item.label, "tab_inactive")
        if self.items and self.active_id is None:
            raise ValueError("Nonempty NavigationTabs requires an active_id")
        if self.active_id is not None and self.active_id not in ids:
            raise ValueError(f"Unknown active navigation tab: {self.active_id!r}")

    @property
    def active(self) -> ViewTab | None:
        return next((item for item in self.items if item.id == self.active_id), None)

    def content(self) -> StyledText:
        """Supply all labels in declared order; layout and clipping belong to the composer."""
        runs: list[TextRun] = []
        for item in self.items:
            if runs:
                runs.append(TextRun(" \u00b7 ", "separator"))
            role = "tab_active" if item.id == self.active_id else "tab_inactive"
            runs.append(TextRun(item.label, role))
        return StyledText(tuple(runs))
