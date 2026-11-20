"""Shared terminal-cell viewport; terminal-owned spacing is never modified."""
import os
import re
from functools import lru_cache


@lru_cache(maxsize=1)
def configured_padding():
    from ..state.config import load_config_file
    from ..state.paths import CONFIG_PATH
    value = load_config_file(CONFIG_PATH, disabled=True).get('padding', '')
    if value is None or not str(value).strip():
        return None
    parts = str(value).split()
    if not 1 <= len(parts) <= 4 or any(not part.isdecimal() for part in parts):
        return None
    values = tuple(min(1000, int(part)) for part in parts)
    if len(values) == 1:
        return values * 4
    if len(values) == 2:
        return values * 2
    if len(values) == 3:
        return values[0], values[1], values[2], values[1]
    return values


@lru_cache(maxsize=1)
def configured_max_width():
    from ..state.config import load_config_file
    from ..state.paths import CONFIG_PATH
    return load_config_file(CONFIG_PATH, disabled=True).get('max_width', 120)


def padding_for(columns, rows):
    from ..state.theme import MINT_SCREEN_THEME as theme
    padding = configured_padding()
    if padding is None:
        padding = (1, 2, 1, 2) if theme.background is not None else (0, 0, 0, 0)
    top, right, bottom, left = padding
    # Preserve at least 40 columns and 12 rows, or all available smaller space.
    horizontal = max(0, columns - 40)
    vertical = max(0, rows - 12)
    if left + right > horizontal:
        total = left + right
        left = horizontal * left // total
        right = horizontal - left
    if top + bottom > vertical:
        total = top + bottom
        top = vertical * top // total
        bottom = vertical - top
    maximum = configured_max_width()
    if maximum:
        maximum = max(40, maximum)
    available = columns - left - right
    if maximum and available > maximum:
        extra = available - maximum
        left += extra // 2
        right += extra - extra // 2
    return top, right, bottom, left


def content_size(fd=1):
    size = os.get_terminal_size(fd)
    top, right, bottom, left = padding_for(size.columns, size.lines)
    return os.terminal_size((size.columns - left - right, size.lines - top - bottom))


def offset_output(text):
    """Translate UI-owned absolute cursor positions into the padded viewport."""
    try:
        size = os.get_terminal_size()
    except OSError:
        return text
    top, _, _, left = padding_for(size.columns, size.lines)
    if not top and not left:
        return text
    def move(match):
        row = int(match[1] or 1) + top
        column = int(match[2] or 1) + left
        return f'\033[{row};{column}{match[3]}'
    return re.sub(r'\x1b\[(\d*)(?:;(\d*))?([Hf])', move, text)


def positioned_lines(lines):
    """Place every frame row inside the shared viewport, without carriage returns."""
    return "".join(f"\033[{row};1H{line}" for row, line in enumerate(lines, 1))
