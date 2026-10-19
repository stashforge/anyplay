"""Pure four-line presentation for already-resolved, domain-independent content."""

from __future__ import annotations

from ..core.terminal import sanitize_terminal_text, truncate_display
from .footer import Footer, render_footer
from .styled_text import StyledText, render_styled_text
from .theme import DEFAULT_THEME, TextStyle, Theme


def render_detail_lines(
    *,
    primary: str | StyledText,
    secondary: str | StyledText,
    details: str | StyledText,
    width: int,
    theme: Theme = DEFAULT_THEME,
) -> tuple[str, str, str]:
    """Render the three text slots independently of footer coordination.

    Resolve content/inherit/blank policies before this boundary. An explicit empty
    string represents a resolved blank; omitted fields and unresolved policies
    are errors.
    """
    for name, value in (("primary", primary), ("secondary", secondary), ("details", details)):
        if not isinstance(value, (str, StyledText)):
            raise TypeError(f"DetailPanel.{name} requires resolved text or StyledText")
    if type(width) is not int:
        raise TypeError("DetailPanel.width requires integer columns")
    if width < 1:
        raise ValueError("DetailPanel.width must be positive")
    if not isinstance(theme, Theme):
        raise TypeError("DetailPanel.theme requires Theme")

    def line(text: str | StyledText, style: TextStyle) -> str:
        if isinstance(text, StyledText):
            return render_styled_text(text, width=width, theme=theme)
        bounded = truncate_display(sanitize_terminal_text(text), max(1, width - 1))
        return f"{theme.prefix(style)}{bounded}{theme.reset}"

    return (
        line(primary, theme.primary),
        line(secondary, theme.secondary),
        line(details, theme.details),
    )


def render_detail_panel(
    *,
    primary: str | StyledText,
    secondary: str | StyledText,
    details: str | StyledText,
    footer: Footer,
    width: int,
    theme: Theme = DEFAULT_THEME,
) -> tuple[str, str, str, str]:
    """Compose text pieces and a semantic footer into four immutable slots."""
    if not isinstance(footer, Footer):
        raise TypeError("DetailPanel.footer requires Footer")
    lines = render_detail_lines(primary=primary, secondary=secondary, details=details, width=width, theme=theme)
    return (*lines, render_footer(footer, width, theme=theme))
