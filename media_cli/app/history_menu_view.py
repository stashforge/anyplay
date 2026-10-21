"""Project prepared history entries into immutable shared UI rows."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..core.terminal import sanitize_terminal_text
from ..domain.history import format_history_entry, format_relative_time
from ..providers.shared.models import title_provider_key
from ..ui.footer import FooterStatus
from ..ui.help import picker_help
from ..ui.poster import PosterManager
from ..ui.styled_text import StyledText, TextRun
from ..ui.view_model import Blank, DetailLines, ImageSpec, ViewAction, ViewItem, ViewSpec, ViewTab
from .footer import provider_label
from .show_view_data import build_show_detail_lines


@dataclass(frozen=True)
class HistoryEntryReference:
    provider_id: str
    show_id: str
    translation_type: str

    def __post_init__(self) -> None:
        for name in ("provider_id", "show_id", "translation_type"):
            value = getattr(self, name)
            TextRun(value, "details")
            if not value.strip():
                raise ValueError(f"HistoryEntryReference.{name} requires nonblank text")

    @classmethod
    def from_entry(cls, entry: dict[str, Any]) -> HistoryEntryReference:
        if not isinstance(entry, dict) or not isinstance(entry.get("show"), dict):
            raise TypeError("History entries require a show dictionary")
        show = entry["show"]
        identity = show.get("_id") if show.get("_id") is not None else show.get("id")
        if identity is None:
            raise ValueError("History entry requires a title identity")
        return cls(title_provider_key(show), str(identity), entry.get("translation_type", "sub"))


def build_history_items(
    entries: Sequence[dict[str, Any]], *, show_cover: bool = True, now: float | None = None,
) -> tuple[ViewItem, ...]:
    """Caller owns filtering, progress preparation, refreshes and persistence."""
    if type(show_cover) is not bool:
        raise TypeError("History show_cover requires a boolean")
    rows = []
    identities: set[HistoryEntryReference] = set()
    for entry in tuple(entries):
        reference = HistoryEntryReference.from_entry(entry)
        show = entry["show"]
        if reference in identities:
            raise ValueError(f"Duplicate history entry identity: {reference!r}")
        identities.add(reference)
        new_release = entry.get("has_new_release", False)
        if type(new_release) is not bool:
            raise TypeError("History has_new_release requires a boolean")
        image_key = f"{reference.provider_id}:{reference.show_id}"
        url = PosterManager._get_cover_url(show) if show_cover else None
        image = (ImageSpec(image_key, "available", url) if url else ImageSpec(image_key, "unavailable")) if show_cover else None
        updated = format_relative_time(entry.get("timestamp"), now=now)
        rows.append(ViewItem(
            f"history:{image_key}:{reference.translation_type}",
            sanitize_terminal_text(format_history_entry(entry)), kind="submenu",
            details=build_show_detail_lines(
                show, reference.translation_type, primary=show.get("name") or None, local_only=True,
            ),
            image=image, reference=reference,
            prefix=StyledText((TextRun("\u25c6 ", "history.new_release"),)) if new_release else "",
            info=sanitize_terminal_text(show.get("description") or ""),
            footer_context=tuple(sanitize_terminal_text(part) for part in (
                provider_label(show), f"Updated {updated}" if updated else "",
            ) if part),
        ))
    return tuple(rows)


HISTORY_MODES = ("Active", "Up to date", "Completed", "All")
HISTORY_FOCUS_DELAY = 1.2
_EMPTY_MESSAGES = (
    "No active history entries.", "No up-to-date titles.", "No completed titles.", "Watch history is empty.",
)


def build_history_view(
    entries: Sequence[dict[str, Any]], *, mode: str, total_count: int,
    incognito: bool = False, show_cover: bool = True, now: float | None = None,
    footer_statuses: Sequence[FooterStatus] = (),
) -> ViewSpec:
    """Assemble caller-filtered, prepared history; no categorization or refresh."""
    if mode not in HISTORY_MODES:
        raise ValueError(f"Unknown history mode: {mode!r}")
    if type(incognito) is not bool:
        raise TypeError("History incognito requires a boolean")
    if type(total_count) is not int:
        raise TypeError("History total_count requires an integer")
    items = build_history_items(entries, show_cover=show_cover, now=now)
    actions: tuple[ViewAction, ...] = (
        ViewAction("select", "open", ("ENTER", "RIGHT")),
        ViewAction("tab", "next", ("TAB", "CTRL_N")),
        ViewAction("tab", "previous", ("BTAB", "CTRL_P"), footer=False),
        ViewAction("refresh_title", "refresh title", ("CTRL_R",)),
        ViewAction("refresh_view", "refresh view", ("F5", "ALT_R")),
    )
    if not incognito:
        actions += (ViewAction("delete", "delete", ("DELETE", "CTRL_D")),)
    actions += (
        ViewAction("help", "Help", ("?",), footer=False),
        ViewAction("information", "Information", ("CTRL_O",), footer=False),
        ViewAction("back", "search", ("LEFT",), footer=False),
        ViewAction("quit", "quit", ("ESC",)),
    )
    help_data = picker_help(
        "Open details", "Search", "Quit", "Next filter", "Previous filter",
        delete_label=None if incognito else "Remove from history", reverse_label="Refresh selected title",
        info_label="Title information",
    )
    help_data.update({"F5": "Refresh visible titles", "Alt+R": "Refresh visible titles"})
    return ViewSpec(
        "history", "Watch History", items, details=DetailLines(Blank, Blank, Blank),
        tabs=tuple(ViewTab(name, name) for name in HISTORY_MODES), active_tab_id=mode,
        total_count=total_count, state="ready" if items else "empty",
        message="" if items else _EMPTY_MESSAGES[HISTORY_MODES.index(mode)],
        context=("History", "INCOGNITO (READ-ONLY)") if incognito else ("History",),
        actions=actions, help=tuple(help_data.items()),
        footer_statuses=footer_statuses,
    )
