"""Configurable terminal loading spinners."""

import time

from ..core.terminal import sanitize_terminal_text

SPINNER_STYLES = {
    "braille": ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"],
    "dots": [".   ", "..  ", "... ", "...."],
    "line": ["-", "\\", "|", "/"],
    "pulse": ["·", "•", "●", "•"],
}
DEFAULT_SPINNER = "braille"


def spinner_frames(style=None):
    if isinstance(style, (list, tuple)):
        frames = []
        for frame in style:
            try:
                value = str(frame)
            except Exception:
                continue
            if value.strip():
                frames.append(value)
        return frames or SPINNER_STYLES[DEFAULT_SPINNER]
    name = str(style or DEFAULT_SPINNER).strip().casefold()
    return SPINNER_STYLES.get(name, SPINNER_STYLES[DEFAULT_SPINNER])


def spinner_frame(style=None, now=None):
    frames = spinner_frames(style)
    timestamp = time.time() if now is None else now
    return frames[int(timestamp * 10) % len(frames)]


def spinner_from_config(config):
    if not isinstance(config, dict):
        return DEFAULT_SPINNER
    ui_config = config.get("ui")
    if isinstance(ui_config, dict) and ui_config.get("spinner") is not None:
        return ui_config.get("spinner")
    return config.get("spinner", DEFAULT_SPINNER)


def loading_line(message, width=80, style=None):
    message = sanitize_terminal_text(message)
    if len(message) > width - 4:
        message = message[:max(1, width - 7)] + "..."
    return f"\033[36m{spinner_frame(style)} {message}\033[0m"
