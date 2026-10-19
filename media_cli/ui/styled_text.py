"""Role-based single-line text, independent of domain and screen layout."""

from __future__ import annotations

from dataclasses import dataclass

from ..core.terminal import truncate_display
from .theme import DEFAULT_THEME, Theme, validate_role_name


@dataclass(frozen=True)
class TextRun:
    text: str
    role: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("TextRun.text requires plain text")
        if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in self.text):
            raise ValueError("TextRun.text cannot contain terminal styling or control characters")
        validate_role_name(self.role)


@dataclass(frozen=True)
class StyledText:
    runs: tuple[TextRun, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.runs, tuple) or any(not isinstance(run, TextRun) for run in self.runs):
            raise TypeError("StyledText.runs requires an immutable tuple of TextRun values")


def fit_styled_text(content: StyledText, *, columns: int) -> StyledText:
    """Fit an exact cell budget while preserving run roles, without rendering."""
    if not isinstance(content, StyledText):
        raise TypeError("fit_styled_text requires StyledText")
    if type(columns) is not int:
        raise TypeError("fit_styled_text.columns requires integer cells")
    if columns < 0:
        raise ValueError("fit_styled_text.columns must be nonnegative")
    if not columns:
        return StyledText(())
    text = "".join(run.text for run in content.runs)
    bounded = truncate_display(text, columns)
    if bounded == text:
        return content
    remaining = len(bounded) - 1
    runs = []
    for run in content.runs:
        take = min(len(run.text), remaining)
        piece = run.text[:take]
        remaining -= take
        finished = take < len(run.text)
        if finished:
            piece += "\u2026"
        if piece:
            runs.append(TextRun(piece, run.role))
        if finished:
            break
    return StyledText(tuple(runs))


def render_styled_text(content: StyledText, *, width: int, theme: Theme = DEFAULT_THEME) -> str:
    """Render within width minus one, validating even roles hidden by clipping."""
    if not isinstance(content, StyledText):
        raise TypeError("render_styled_text requires StyledText")
    if not isinstance(theme, Theme):
        raise TypeError("render_styled_text.theme requires Theme")
    if type(width) is not int:
        raise TypeError("render_styled_text.width requires integer columns")
    if width < 1:
        raise ValueError("render_styled_text.width must be positive")
    styles = {run.role: theme.resolve_style(run.role) for run in content.runs}
    bounded = fit_styled_text(content, columns=max(1, width - 1))
    output = [theme.reset]
    for run in bounded.runs:
        output.append(theme.prefix(styles[run.role]) + run.text + theme.reset)
    return "".join(output)
