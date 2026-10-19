"""Immutable semantic styles; no file loading, layout or terminal ownership."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields
from enum import Enum


class BasicColor(Enum):
    BLACK = 30
    RED = 31
    GREEN = 32
    YELLOW = 33
    BLUE = 34
    MAGENTA = 35
    CYAN = 36
    WHITE = 37
    BRIGHT_BLACK = 90
    BRIGHT_RED = 91
    BRIGHT_GREEN = 92
    BRIGHT_YELLOW = 93
    BRIGHT_BLUE = 94
    BRIGHT_MAGENTA = 95
    BRIGHT_CYAN = 96
    BRIGHT_WHITE = 97


Color = BasicColor | int | tuple[int, int, int] | None


def validate_role_name(role: str) -> None:
    if not isinstance(role, str):
        raise TypeError("Style role requires a name, not a color or TextStyle")
    if re.fullmatch(r"[a-z][a-z0-9_.-]*", role) is None:
        raise ValueError(f"Invalid style role name: {role!r}")


def _validate_color(value: Color, owner: str) -> None:
    if value is None or isinstance(value, BasicColor):
        return
    if type(value) is int:
        if not 0 <= value <= 255:
            raise ValueError(f"{owner} palette index must be between 0 and 255")
        return
    if isinstance(value, tuple) and len(value) == 3 and all(type(channel) is int for channel in value):
        if not all(0 <= channel <= 255 for channel in value):
            raise ValueError(f"{owner} RGB channels must be between 0 and 255")
        return
    raise TypeError(f"{owner} requires BasicColor, palette index, RGB tuple or None")


def _color_codes(color: Color, *, background: bool = False) -> tuple[int, ...]:
    if color is None:
        return ()
    if isinstance(color, BasicColor):
        return (color.value + (10 if background else 0),)
    base = 48 if background else 38
    if isinstance(color, int):
        return (base, 5, color)
    return (base, 2, *color)


def _sgr(codes: tuple[int, ...]) -> str:
    return f"\033[{';'.join(map(str, codes))}m" if codes else ""


@dataclass(frozen=True)
class TextStyle:
    foreground: Color = None
    background: Color = None
    bold: bool = False
    italic: bool = False
    underline: bool = False

    def __post_init__(self) -> None:
        _validate_color(self.foreground, "TextStyle.foreground")
        _validate_color(self.background, "TextStyle.background")
        for name in ("bold", "italic", "underline"):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"TextStyle.{name} requires bool")


@dataclass(frozen=True)
class Theme:
    background: Color = None
    primary: TextStyle = TextStyle(BasicColor.BRIGHT_WHITE, bold=True)
    secondary: TextStyle = TextStyle(248)
    details: TextStyle = TextStyle(248)
    muted: TextStyle = TextStyle(244)
    separator: TextStyle = TextStyle(244)
    context: TextStyle = TextStyle(244)
    actions: TextStyle = TextStyle(244)
    loading: TextStyle = TextStyle(BasicColor.CYAN)
    info: TextStyle = TextStyle(244)
    warning: TextStyle = TextStyle(222)
    error: TextStyle = TextStyle(203)
    success: TextStyle = TextStyle(114)
    extensions: tuple[tuple[str, TextStyle], ...] = ()
    counter: TextStyle = field(default=TextStyle(244), kw_only=True)
    selected_count: TextStyle = field(default=TextStyle((166, 227, 161)), kw_only=True)
    scroll_indicator: TextStyle = field(default=TextStyle((243, 139, 168)), kw_only=True)
    prompt: TextStyle = field(default=TextStyle((203, 166, 247)), kw_only=True)
    query: TextStyle = field(default=TextStyle((137, 220, 235)), kw_only=True)
    query_cursor: TextStyle = field(default=TextStyle((30, 30, 46), background=(137, 220, 235)), kw_only=True)
    rule: TextStyle = field(default=TextStyle(244), kw_only=True)
    tab_active: TextStyle = field(default=TextStyle((203, 166, 247)), kw_only=True)
    tab_inactive: TextStyle = field(default=TextStyle(244), kw_only=True)

    def __post_init__(self) -> None:
        _validate_color(self.background, "Theme.background")
        for descriptor in fields(self):
            if descriptor.name not in ("background", "extensions") and not isinstance(
                getattr(self, descriptor.name), TextStyle
            ):
                raise TypeError(f"Theme.{descriptor.name} requires TextStyle")
        if not isinstance(self.extensions, tuple):
            raise TypeError("Theme.extensions requires an immutable tuple")
        names = {field.name for field in fields(self)}
        for entry in self.extensions:
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise TypeError("Theme.extensions entries require (role, TextStyle) tuples")
            role, style = entry
            validate_role_name(role)
            if role in names:
                raise ValueError(f"Duplicate or reserved theme role: {role!r}")
            if not isinstance(style, TextStyle):
                raise TypeError(f"Theme role {role!r} requires TextStyle")
            names.add(role)

    def resolve_style(self, role: str) -> TextStyle:
        """Resolve a declared role; unknown names are programming errors."""
        validate_role_name(role)
        style = getattr(self, role, None)
        if isinstance(style, TextStyle):
            return style
        for name, extension in self.extensions:
            if role == name:
                return extension
        raise ValueError(f"Unknown theme role: {role!r}")

    def prefix(self, style: TextStyle) -> str:
        if not isinstance(style, TextStyle):
            raise TypeError("Theme.prefix requires TextStyle")
        attributes = tuple(
            code for enabled, code in ((style.bold, 1), (style.italic, 3), (style.underline, 4)) if enabled
        )
        background = self.background if style.background is None else style.background
        return _sgr(attributes + _color_codes(style.foreground) + _color_codes(background, background=True))

    @property
    def reset(self) -> str:
        """Reset segment attributes while preserving the active canvas color."""
        return "\033[0m" + _sgr(_color_codes(self.background, background=True))


DEFAULT_THEME = Theme()
