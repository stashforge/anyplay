"""Compact width notice; terminal ownership and action handling stay outside."""

from .layout import MIN_TUI_COLUMNS
from .styled_text import StyledText, TextRun, render_styled_text
from .theme import DEFAULT_THEME, Theme


def render_width_notice(*, columns: int, theme: Theme = DEFAULT_THEME) -> tuple[str, str]:
    return (
        render_styled_text(
            StyledText((TextRun(f"Resize to {MIN_TUI_COLUMNS} columns", "warning"),)), width=columns, theme=theme
        ),
        render_styled_text(
            StyledText((TextRun("Esc: back \u00b7 Ctrl+C: quit", "actions"),)), width=columns, theme=theme
        ),
    )
