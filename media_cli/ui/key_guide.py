"""Read-only, grouped keyboard card shared by terminal menus."""
from ..core.terminal import display_width, sanitize_terminal_text, truncate_display
from ..state.theme import MINT_SCREEN_THEME
from .screen_chrome import RESET, role_prefix, run_framed_screen


def key_guide_frame(groups, width, height, *, offset=0):
    theme = MINT_SCREEN_THEME
    limit = max(1, width - 1)
    lines = [""] * max(1, height)
    def put(row, text):
        if 0 <= row < height:
            lines[row] = truncate_display(text, limit)
    put(0, role_prefix(theme, "screen.brand") + "Anyplay" + RESET
        + role_prefix(theme, "secondary") + " · Keys" + RESET)
    rule = role_prefix(theme, "separator") + "─" * limit + RESET
    put(1, rule)
    content = []
    for title, entries in groups:
        if not entries:
            continue
        content.append(role_prefix(theme, "success") + sanitize_terminal_text(title) + RESET)
        content.append("")
        for key, label in entries:
            key, label = sanitize_terminal_text(key), sanitize_terminal_text(label)
            if width < 46:
                content.extend(("  " + role_prefix(theme, "screen.key") + key + RESET,
                                "    " + role_prefix(theme, "secondary") + label + RESET))
            else:
                content.append("  " + role_prefix(theme, "screen.key") + key + RESET
                               + " " * max(2, 23 - display_width(key))
                               + role_prefix(theme, "secondary") + label + RESET)
        content.append("")
    capacity = max(0, height - 5)
    offset = max(0, min(offset, max(0, len(content) - capacity)))
    for index, text in enumerate(content[offset:offset + capacity]):
        put(3 + index, text)
    put(height - 2, rule)
    put(height - 1, role_prefix(theme, "secondary")
        + ("↑↓ scroll · any other key closes" if len(content) > capacity else "Any key closes this") + RESET)
    return lines, max(0, len(content) - capacity)


def show_key_guide(groups):
    offset, maximum = 0, 0
    def render(width, height, _elapsed):
        nonlocal maximum
        lines, maximum = key_guide_frame(groups, width, height, offset=offset)
        return lines
    def key(value):
        nonlocal offset
        if value == "CTRL_C":
            raise KeyboardInterrupt
        if value in ("UP", "DOWN", "PAGE_UP", "PAGE_DOWN", "HOME", "END") and maximum:
            delta = {"UP": -1, "DOWN": 1, "PAGE_UP": -10, "PAGE_DOWN": 10}.get(value, 0)
            offset = 0 if value == "HOME" else maximum if value == "END" else min(maximum, max(0, offset + delta))
            return None
        return True
    return run_framed_screen(render, key)


def list_key_groups(help_dict=None, *, aliases=(), filtered=True):
    navigation, actions, editing = [], [], []
    entries = dict(help_dict or {})
    if filtered:
        entries.pop("Type", None)
        entries["/"] = "Start filtering; Escape clears the filter"
    for key, label in entries.items():
        if key in ("Backspace", "Ctrl+U", "/", "Type"):
            editing.append((key, label))
        elif key.startswith(("↑", "Home", "Page", "Enter", "Right", "Left", "Esc", "Tab", "Shift+Tab", "Ctrl+N", "Ctrl+P")):
            navigation.append((key, label))
        elif key not in ("?", "Ctrl+C"):
            actions.append((key, label))
    actions = list(aliases) + actions
    return (("Navigation", navigation), ("This screen", actions), ("Filter", editing),
            ("Everywhere", (("?", "Keyboard guide"), ("q / Ctrl+C", "Quit CLI (outside text input/playback)"))))
