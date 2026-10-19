"""Semantic footer content and isolated terminal styling."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from ..core.terminal import display_width, fit_terminal_line, sanitize_terminal_text, truncate_display
from .theme import DEFAULT_THEME, TextStyle, Theme

MUTED = "\033[38;5;244m"
RESET = "\033[0m"
SEPARATOR = " \u2022 "
_SGR = re.compile(r"\x1b\[([0-9;]*)m")


class StatusKind(Enum):
    LOADING = "loading"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    SUCCESS = "success"


def _validate_text(text: str, owner: str) -> None:
    if not isinstance(text, str):
        raise TypeError(f"{owner} requires plain text")
    if not text.strip():
        raise ValueError(f"{owner} requires nonblank text")
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in text):
        raise ValueError(f"{owner} cannot contain terminal styling or control characters")


@dataclass(frozen=True)
class FooterStatus:
    message: str
    kind: StatusKind = StatusKind.INFO
    indicator: str = ""

    def __post_init__(self) -> None:
        _validate_text(self.message, "FooterStatus.message")
        if not isinstance(self.kind, StatusKind):
            raise TypeError("FooterStatus.kind requires StatusKind")
        if self.indicator:
            _validate_text(self.indicator, "FooterStatus.indicator")


@dataclass(frozen=True)
class Footer:
    statuses: tuple[FooterStatus, ...] = ()
    context: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()
    status_separator: str = "  \u2502  "
    include_cover_status: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "statuses", tuple(self.statuses))
        object.__setattr__(self, "context", tuple(self.context))
        object.__setattr__(self, "actions", tuple(self.actions))
        if any(not isinstance(status, FooterStatus) for status in self.statuses):
            raise TypeError("Footer.statuses requires FooterStatus values")
        for name, values in (("context", self.context), ("actions", self.actions)):
            for value in values:
                _validate_text(value, f"Footer.{name}")
        if self.status_separator not in ("  \u2502  ", SEPARATOR):
            raise ValueError("Footer.status_separator requires a supported separator")
        if not isinstance(self.include_cover_status, bool):
            raise TypeError("Footer.include_cover_status requires bool")


FooterInput = Footer | str


def render_footer(footer: Footer, width: int, *, theme: Theme = DEFAULT_THEME) -> str:
    """Style every segment independently; never inherit a previous role's color."""
    if not isinstance(theme, Theme):
        raise TypeError("Footer.theme requires Theme")
    statuses = {
        StatusKind.LOADING: theme.loading,
        StatusKind.INFO: theme.info,
        StatusKind.WARNING: theme.warning,
        StatusKind.ERROR: theme.error,
        StatusKind.SUCCESS: theme.success,
    }
    chunks: list[tuple[str, TextStyle]] = []
    has_loading_indicator = False
    for status in footer.statuses:
        if chunks:
            chunks.append((SEPARATOR, theme.separator))
        indicator = status.indicator
        if status.kind is StatusKind.LOADING:
            if has_loading_indicator:
                indicator = ""
            has_loading_indicator = has_loading_indicator or bool(indicator)
        message = f"{indicator} {status.message}" if indicator else status.message
        chunks.append((message, statuses[status.kind]))
    navigation = tuple((text, theme.context) for text in footer.context) + tuple(
        (text, theme.actions) for text in footer.actions
    )
    if chunks and navigation:
        chunks.append((footer.status_separator, theme.separator))
    for index, (part, style) in enumerate(navigation):
        if index:
            chunks.append((SEPARATOR, theme.separator))
        chunks.append((part, style))

    remaining = max(1, width - 1)
    output = []
    for text, style in chunks:
        if remaining <= 0:
            break
        bounded = truncate_display(text, remaining)
        output.append(f"{theme.prefix(style)}{bounded}{theme.reset}")
        remaining -= display_width(bounded)
        if bounded != text:
            break
    return theme.prefix(theme.muted) + "".join(output) + theme.reset


def plain_footer(text: str) -> Footer:
    """Compatibility boundary for legacy navigation, not a new styling API."""
    cleaned = sanitize_terminal_text(text)
    return Footer(context=(cleaned,) if cleaned else ())


def legacy_status(text: str | FooterStatus, kind: StatusKind) -> FooterStatus:
    """Adapt old spinner strings once; new callers supply plain semantic status."""
    return text if isinstance(text, FooterStatus) else FooterStatus(sanitize_terminal_text(text), kind)


def render_legacy_footer(text: str, width: int) -> str:
    """Preserve trusted old styling but restore muted foreground after any reset.

    Only legacy callers use this path. New footer values reject ANSI outright.
    Extended color arguments are skipped so an RGB channel of zero is not
    mistaken for a reset instruction.
    """

    def restore_base(match: re.Match[str]) -> str:
        codes = tuple(int(value or "0") for value in (match[1] or "0").split(";"))
        foreground_reset = False
        index = 0
        while index < len(codes):
            code = codes[index]
            if code in (0, 39):
                foreground_reset = True
            elif code in (38, 48) and index + 1 < len(codes):
                if code == 38:
                    foreground_reset = False
                index += 2 if codes[index + 1] == 5 else 4 if codes[index + 1] == 2 else 0
            elif 30 <= code <= 37 or 90 <= code <= 97:
                foreground_reset = False
            index += 1
        return match[0] + MUTED if foreground_reset else match[0]

    bounded = fit_terminal_line(_SGR.sub(restore_base, text), width)
    return f"{MUTED}{bounded}{RESET}"


def render_legacy_overlay(status: FooterStatus, navigation: str, width: int) -> str:
    """Overlay semantic cover status without flattening trusted legacy badges."""
    left = render_footer(Footer(statuses=(status,)), width)
    if not navigation:
        return left
    combined = left + MUTED + "  \u2502  " + RESET + render_legacy_footer(navigation, width)
    return fit_terminal_line(combined, width) + RESET
