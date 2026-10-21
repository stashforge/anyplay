"""Semantic row text, independent of picker state and application data."""

from __future__ import annotations

from dataclasses import replace

from .fuzzy import fuzzy_highlight, fuzzy_match
from .styled_text import StyledText, TextRun, fit_styled_text
from .theme import DEFAULT_THEME, Theme


def plain_row_text(content: str | StyledText) -> str:
    """Return color-free filtering/plain-mode content, rejecting terminal controls."""
    if isinstance(content, StyledText):
        return content.text
    return TextRun(content, "details").text


def render_row_text(
    content: str | StyledText,
    *,
    columns: int,
    query: str = "",
    selected: bool = False,
    theme: Theme = DEFAULT_THEME,
) -> str:
    """Apply roles, selection and fuzzy highlights inside an exact cell budget."""
    if type(columns) is not int:
        raise TypeError("Row text columns requires integer cells")
    if columns < 0:
        raise ValueError("Row text columns must be nonnegative")
    if not isinstance(selected, bool):
        raise TypeError("Row text selected requires a boolean")
    if not isinstance(query, str):
        raise TypeError("Row text query requires a string")
    if not isinstance(theme, Theme):
        raise TypeError("Row text theme requires Theme")
    source = content if isinstance(content, StyledText) else StyledText((TextRun(content, "details"),))
    # Validate roles before clipping so hidden invalid data cannot slip through.
    styles = {run.role: theme.resolve_style(run.role) for run in source.runs}
    bounded = fit_styled_text(source, columns=columns)
    match = fuzzy_match(query, bounded.text) if query else None
    indices = match[1] if match is not None else set()
    match_style = replace(theme.row_selected, underline=True) if selected else theme.row_match
    chunks = []
    offset = 0
    for run in bounded.runs:
        base = theme.row_selected if selected else styles[run.role]
        local_matches = {index - offset for index in indices if offset <= index < offset + len(run.text)}
        chunks.append(fuzzy_highlight(
            run.text, local_matches, base_style=theme.prefix(base),
            match_style=theme.prefix(match_style), reset_style=theme.reset,
        ))
        offset += len(run.text)
    return "".join(chunks)
