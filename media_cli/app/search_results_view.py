"""Project prepared provider search records into shared, immutable UI data."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..core.terminal import sanitize_terminal_text, truncate_display
from ..domain.titles import get_show_display_title
from ..ui.footer import StatusKind
from ..ui.help import picker_help
from ..ui.poster import PosterManager
from ..ui.view_model import (
    Blank,
    Content,
    DetailLines,
    ImageSpec,
    LabelValue,
    SelectionPolicy,
    ViewAction,
    ViewItem,
    ViewSpec,
    ViewState,
)
from .show_view_data import build_show_detail_lines


def _result_item(show: dict[str, Any], provider_id: str, ttype: str) -> ViewItem:
    identity = show.get("_id") if show.get("_id") is not None else show.get("id")
    if identity is None or not str(identity).strip():
        raise ValueError("Search result requires provider identity")
    item_id = f"{provider_id}:{identity}"
    title = get_show_display_title(show)
    # Keep the existing header's primary title and genre fallback decisions.
    url = PosterManager._get_cover_url(show)
    return ViewItem(
        item_id,
        title,
        kind="submenu",
        details=build_show_detail_lines(show, ttype, primary=show.get("name") or title),
        image=ImageSpec(item_id, "available", url) if url else ImageSpec(item_id, "unavailable"),
        info=sanitize_terminal_text(show.get("description") or ""),
        reference=show,
    )


def build_search_results_view(
    shows: Sequence[dict[str, Any]],
    *,
    provider_id: str,
    provider_name: str,
    query: str,
    ttype: str,
    prompt: str,
    auto_single: bool = False,
    loading: bool = False,
    message: str = "",
    error: str = "",
    badges: tuple[str, ...] = (),
) -> ViewSpec:
    """No fetching or rendering: callers retain ownership of prepared records."""
    items = tuple(_result_item(show, provider_id, ttype) for show in shows)
    clean_query = sanitize_terminal_text(query)
    provider_name = sanitize_terminal_text(provider_name)
    state: ViewState = "failed" if error else "loading" if loading else "ready" if items else "empty"
    status = sanitize_terminal_text(error or message)
    if not items and not status and not loading:
        status = f'No results for "{truncate_display(clean_query, 20)}"'
    return ViewSpec(
        f"search:{provider_id}",
        prompt,
        items,
        details=DetailLines(
            Blank,
            Content("Use Up/Down to browse previous searches.") if loading and not items else Blank,
            Content(LabelValue("Provider", provider_name)) if provider_name else Blank,
        ),
        state=state,
        message=status,
        message_kind=StatusKind.ERROR if error else StatusKind.INFO,
        context=tuple(
            part
            for part in (
                provider_name,
                f'"{truncate_display(clean_query, 16)}"' if clean_query else "",
                *(sanitize_terminal_text(badge) for badge in badges),
            )
            if part
        ),
        actions=(
            ViewAction("select", "select", ("ENTER", "RIGHT")),
            ViewAction("help", "Help", ("?",)),
            ViewAction("back", "search", ("ESC", "LEFT")),
            ViewAction("information", "Information", ("CTRL_O",), footer=False),
        ),
        help=tuple(picker_help("Select anime", "New search", "New search", info_label="Title information").items()),
        selection_policy=SelectionPolicy(auto_single=auto_single),
    )
