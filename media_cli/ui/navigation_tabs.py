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

    def content(self, *, separator=" · ") -> StyledText:
        """Supply all labels in declared order; layout and clipping belong to the composer."""
        runs: list[TextRun] = []
        for item in self.items:
            if runs:
                runs.append(TextRun(separator, "separator"))
            role = "tab_active" if item.id == self.active_id else "tab_inactive"
            runs.append(TextRun(item.label, role))
        return StyledText(tuple(runs))

    def window_content(self, width, *, separator="    ", viewport=None):
        """Keep the active tab and nearby tabs visible; indicate hidden neighbors."""
        from ..core.terminal import display_width, truncate_display
        width = max(1, width)
        viewport = viewport if viewport is not None else {}
        signature = tuple((item.id, item.label) for item in self.items)
        if display_width(self.content(separator=separator).text) <= width:
            return self.content(separator=separator)
        active = next((i for i, item in enumerate(self.items) if item.id == self.active_id), 0)
        def render(start, end, cap=None):
            runs = [TextRun('… ', 'separator')] if start else []
            for i in range(start, end):
                if i > start:
                    runs.append(TextRun(separator, 'separator'))
                label = self.items[i].label
                runs.append(TextRun(truncate_display(label, cap) if cap else label,
                                    'tab_active' if i == active else 'tab_inactive'))
            if end < len(self.items):
                runs.append(TextRun(' …', 'separator'))
            return StyledText(tuple(runs))
        saved = viewport.get('window')
        if saved and saved[3:] == (width, signature):
            start, end, cap = saved[:3]
            if start <= active < end:
                return render(start, end, cap)
        previous_start = saved[0] if saved else 0
        def remember(start, end, cap=None):
            viewport['window'] = (start, end, cap, width, signature)
            return render(start, end, cap)
        candidates = []
        for start in range(active + 1):
            for end in range(active + 1, len(self.items) + 1):
                value = render(start, end)
                if display_width(value.text) <= width:
                    candidates.append((abs(start-previous_start), -(end-start), start, end, value))
        best = min(candidates, key=lambda item: item[:3]) if candidates else None
        count = min(3, len(self.items))
        if (best is None or -best[1] < count) and count:
            start = max(0, min(previous_start, active, len(self.items)-count))
            if active >= start + count:
                start = active-count+1
            end = start + count
            overhead = display_width(separator)*(count-1) + (2 if start else 0) + (2 if end < len(self.items) else 0)
            cap = (width-overhead)//count
            if cap >= 4:
                return remember(start,end,cap)
        if best:
            return remember(best[2], best[3])
        return StyledText((TextRun(truncate_display(self.active.label, width), 'tab_active'),)) if self.active else StyledText(())


def tab_windows(owner):
    """Session-owned viewport memory, reused when a list screen is rebuilt."""
    windows = getattr(owner, '_tab_windows', None)
    if windows is None:
        windows = owner._tab_windows = {}
    return windows
