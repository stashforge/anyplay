"""Project already-prepared title fields into shared semantic detail lines."""

from __future__ import annotations

from typing import Any

from ..core.terminal import sanitize_terminal_text
from ..domain.metadata import format_info_metadata_parts
from ..domain.titles import get_display_titles, get_show_display_title
from ..ui.styled_text import StyledText, TextRun
from ..ui.view_model import Content, DetailLines


def build_show_detail_lines(
    show: dict[str, Any],
    ttype: str,
    *,
    primary: str | None = None,
    local_only: bool = False,
) -> DetailLines:
    """Read prepared values only; enrichment/progress preparation stays with callers."""
    title = sanitize_terminal_text(primary if primary is not None else get_show_display_title(show))
    secondary = get_display_titles(show, title)
    if not secondary:
        genres = show.get("genres") or show.get("_provider_genres")
        if isinstance(genres, list):
            secondary = " • ".join(str(genre) for genre in genres if genre)
        elif isinstance(genres, str):
            secondary = genres.strip().replace(", ", " • ")
    metadata = StyledText(
        tuple(TextRun(text, role) for text, role in format_info_metadata_parts(show, ttype, local_only=local_only))
    )
    return DetailLines(
        Content(title),
        Content(sanitize_terminal_text(secondary) or "No alternative title"),
        metadata,
    )
