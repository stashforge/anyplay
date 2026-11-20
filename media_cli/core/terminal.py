"""Terminal-safe text and display-width helpers."""

import re
import unicodedata

_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_TERMINAL_STRING_RE = re.compile(
    r"\x1b(?:\].*?(?:\x07|\x1b\\)|[P^_].*?\x1b\\)",
    re.DOTALL,
)

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_WHITESPACE_RE = re.compile(r"\s+")


def sanitize_terminal_text(value: object) -> str:
    text = str(value or "")
    text = _TERMINAL_STRING_RE.sub("", text)
    text = _ANSI_RE.sub("", text)
    text = _CONTROL_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def sanitize_input_text(value: object) -> str:
    """Keep editable whitespace; unlike metadata, input must not be trimmed."""
    text = _TERMINAL_STRING_RE.sub("", str(value or ""))
    text = _ANSI_RE.sub("", text)
    return _CONTROL_RE.sub(" ", text)


def strip_ansi(value: object) -> str:
    text = str(value or "")
    text = _TERMINAL_STRING_RE.sub("", text)
    return _ANSI_RE.sub("", text)


def char_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("F", "W") else 1


def display_width(value: object) -> int:
    return sum(char_width(char) for char in strip_ansi(str(value or "")))


def truncate_display(value: object, max_width: int) -> str:
    value = str(value or "")
    if max_width <= 0 or display_width(value) <= max_width:
        return value

    output = []
    used = 0
    index = 0
    while index < len(value):
        match = _ANSI_RE.match(value, index)
        if match:
            output.append(match.group(0))
            index = match.end()
            continue

        char = value[index]
        width = char_width(char)
        if used + width + 1 > max_width:
            break
        output.append(char)
        used += width
        index += 1
    return "".join(output) + "…"


def split_display_prefix(value, max_width):
    value = str(value or "")
    if max_width <= 0:
        return "", value

    output = []
    used = 0
    for index, char in enumerate(value):
        width = char_width(char)
        if used + width > max_width:
            return "".join(output), value[index:]
        output.append(char)
        used += width
    return "".join(output), ""


def fit_terminal_line(line: object, columns: int) -> str:
    """Keep a trusted ANSI-styled TUI line from triggering terminal autowrap."""
    return truncate_display(str(line or ""), max(1, int(columns) - 1))


def _drop_styled_columns(text, columns):
    """Drop a trusted styled prefix while retaining its active SGR state."""
    index, used, styles = 0, 0, []
    while index < len(text) and used < columns:
        match = _ANSI_RE.match(text, index)
        if match:
            styles.append(match.group(0))
            index = match.end()
        else:
            used += char_width(text[index])
            index += 1
    return ''.join(styles) + text[index:]


def absolute_terminal_frame(lines, rows, columns, previous_lines=None, *, protected_image=None):
    """Render exactly one bounded line at each absolute terminal row."""
    frame = []
    bounded_rows = max(1, int(rows))
    for row in range(1, bounded_rows + 1):
        line = lines[row - 1] if row - 1 < len(lines) else ""
        if previous_lines is not None and row - 1 < len(previous_lines) and previous_lines[row - 1] == line:
            continue
        if protected_image is not None:
            top, left, width, height = protected_image
            if top <= row < top + height:
                # Clearing the whole row would erase the unchanged native image.
                end = left + width
                if left:
                    frame.append(f"\033[{row};1H" + (' ' * left))
                frame.append(f"\033[{row};{end + 1}H\033[K" + _drop_styled_columns(fit_terminal_line(line, columns), end))
                continue
        frame.append(f"\033[{row};1H\033[2K{fit_terminal_line(line, columns)}")
    return "".join(frame)


def picker_vertical_layout(
    rows,
    header_count,
    poster_count,
    poster_margin,
    gap,
    item_count,
):
    fixed = 1 + header_count + poster_count + poster_margin + gap
    max_visible = max(1, rows - fixed)
    shown = min(max(0, item_count), max_visible)
    padding = max(
        0,
        rows
        - poster_count
        - poster_margin
        - gap
        - shown
        - 1
        - header_count,
    )
    return max_visible, shown, padding


def bottom_align_panel_lines(lines, height):
    visible = list(lines)[-height:] if height > 0 else []
    return ([""] * max(0, height - len(visible))) + visible
