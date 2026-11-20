"""Search landing page: named, bounded sections with explicit focus ownership."""
from __future__ import annotations

from dataclasses import dataclass

from ..core.terminal import display_width, sanitize_terminal_text, sanitize_input_text, truncate_display
from .screen_chrome import ACCENT as ACCENT
from .screen_chrome import (RESET,
                            shortcut_footer, run_framed_screen, role_prefix, MINT_SCREEN_THEME)


@dataclass
class SearchHomeState:
    query: str = ""
    cursor: int = 0
    focus: int = 0  # input, History, Downloads, Providers
    history: tuple[str, ...] = ()
    history_index: int = -1
    menu: tuple = (("history", "History", "Continue watched titles"), ("downloads", "Downloads", "Play downloaded titles"), ("providers", "Providers", ""))

    def key(self, key: str) -> str | None:
        if key in ("CTRL_C", "EOF", ""):
            return "quit"
        if key == "ESC":
            self.query = ""
            self.cursor = 0
            self.history_index = -1
            self.focus = 0
        elif key == "TAB":
            self.focus = (self.focus + 1) % (len(self.menu) + 1)
        elif key == "BTAB":
            self.focus = (self.focus - 1) % (len(self.menu) + 1)
        elif key in ("UP", "DOWN"):
            self.focus = 0
            if self.history:
                self.history_index = min(len(self.history) - 1, self.history_index + 1) if key == "UP" else max(-1, self.history_index - 1)
                self.query = self.history[self.history_index] if self.history_index >= 0 else ""
                self.cursor = len(self.query)
        elif key == "ENTER":
            return ("search" if self.query.strip() else None) if self.focus == 0 else self.menu[self.focus - 1][0]
        elif key == "LEFT":
            self.cursor = max(0, self.cursor - 1)
        elif key == "RIGHT":
            self.cursor = min(len(self.query), self.cursor + 1)
        elif key == "HOME":
            self.cursor = 0
        elif key == "END":
            self.cursor = len(self.query)
        elif key in ("BACKSPACE", "DELETE"):
            self.focus = 0
            if key == "BACKSPACE" and self.cursor:
                self.query = self.query[:self.cursor - 1] + self.query[self.cursor:]
                self.cursor -= 1
            elif key == "DELETE":
                self.query = self.query[:self.cursor] + self.query[self.cursor + 1:]
        elif len(key) == 1 and key.isprintable():
            self.focus = 0
            self.query = self.query[:self.cursor] + key + self.query[self.cursor:]
            self.cursor += 1
        return None


# Compact half-block lettering. Character artwork is deferred for user review.
LETTER_ROWS = {
    "A": ("█▀█", "█▀█"),
    "N": ("█▄ █", "█ ▀█"),
    "Y": ("█ █", " █ "),
    "P": ("█▀█", "█▀▀"),
    "L": ("█  ", "█▄▄"),
}
LOGO = tuple(" ".join(LETTER_ROWS[letter][row] for letter in "ANYPLAY") for row in range(2))
# Same endpoint colors used by Tork's two-row dark wordmark.



def search_tagline(provider: str, provider_type: str) -> str:
    if provider_type == "torrent" or provider.casefold() in ("torrent", "torrents"):
        return "Find a torrent and choose what to play."
    if provider_type == "donghua":
        return "Find a donghua and watch or download it."
    if provider_type in ("movie", "movies"):
        return "Find a movie or show to watch or download."
    if provider.casefold() == "anilist":
        return "Find anime and manage AniList."
    if provider.casefold() == "animethemes":
        return "Find a song to listen to or download."
    return "Find a show and watch or download it."


def search_home_lines(state: SearchHomeState, provider: str, width: int, height: int, *, message: str = "", provider_type: str = "anime", cursor_visible: bool = True, theme=MINT_SCREEN_THEME, image=(), image_width=15):
    """Named sections: brand, tagline, query, actions, status, footer."""
    accent = role_prefix(theme, "prompt")
    dim = role_prefix(theme, "secondary")
    faint = role_prefix(theme, "muted")
    selected_style = role_prefix(theme, "screen.home_selected")
    cursor_style = role_prefix(theme, "query_cursor")
    width, height = max(1, width), max(1, height)
    lines = [""] * height
    content_width = max(1, min(50, width - 6))
    left = max(0, (width - content_width) // 2)
    def put(row, text, *, centered=False):
        if 0 <= row < height - 2:
            indent = max(0, (width - display_width(text)) // 2) if centered else left
            lines[row] = " " * indent + truncate_display(text, max(1, width - indent - 1) if centered else content_width)

    logo = height >= 20 and content_width >= 28
    brand_rows = 3 if logo else 2
    image = image if height >= 28 else ()
    from .covers import poster_uses_native_protocol
    native_image = image[0] if image and poster_uses_native_protocol(image[0]) else ""
    # Match the one blank row between the wordmark and its description.
    image_gap = 1
    panel_height = brand_rows + 12 + (len(image) + image_gap if image else 0)
    top = max(0, (height - 2 - panel_height) // 2)
    row = top
    for line in image:
        if not native_image:
            put(row, line + RESET, centered=True)
        row += 1
    if image:
        row += image_gap
    if logo:
        for color, text in zip((role_prefix(theme, "screen.logo_high"), role_prefix(theme, "screen.logo_low")), LOGO):
            put(row, color + text + RESET, centered=True)
            row += 1
        row += 1
    else:
        put(row, f"{accent}ANYPLAY{RESET}", centered=True)
        row += 2
    put(row, f"{dim}{search_tagline(provider, provider_type)}{RESET}", centered=True)
    row += 2

    # Query section: show a placeholder even while the empty input has focus.
    inner = max(1, content_width - 5)
    query = sanitize_input_text(state.query)
    before = query[:state.cursor]
    while display_width(before) >= inner:
        before = before[1:]
    after = query[state.cursor:]
    placeholder = "Search torrents…" if provider.casefold() in ("torrent", "torrents") else "Search for a title…"
    if not query:
        text = (cursor_style + placeholder[0] + RESET + faint + placeholder[1:] + RESET) if state.focus == 0 and cursor_visible else faint + placeholder + RESET
    elif state.focus == 0 and cursor_visible:
        text = before + cursor_style + (after[:1] or " ") + RESET + after[1:]
    else:
        text = before + after
    put(row, f"{accent}╭" + "─" * max(0, content_width - 2) + f"╮{RESET}")
    visible = truncate_display(text, inner)
    padding = max(0, inner - display_width(visible))
    put(row + 1, f"{accent}│ ❯{RESET} {visible}" + " " * padding + f"{accent}│{RESET}")
    put(row + 2, f"{accent}╰" + "─" * max(0, content_width - 2) + f"╯{RESET}")
    put(row + 3, f"{faint}type to search · ↑↓ browse history{RESET}", centered=True)

    # Actions section: selection and descriptive text have separate roles.
    for focus, (_action, label, hint) in enumerate(state.menu, 1):
        offset = focus + 4
        hint = hint or sanitize_terminal_text(provider)
        selected = state.focus == focus
        pointer = f"{accent}❯{RESET}" if selected else " "
        style = selected_style if selected else dim
        put(row + offset, f"{pointer} {style}{label:<11}{RESET}{dim if selected else faint}{hint}{RESET}")
    put(row + 8, f"{dim}{sanitize_terminal_text(message)}{RESET}")
    if height >= 2:
        shortcuts = (("enter", "search/open"), ("tab", "menu"), ("?", "help"), ("esc", "clear"), ("ctrl+c", "quit"))
        if width < 58:
            shortcuts = (("enter", "open"), ("tab", "menu"), ("esc", "clear"), ("^C", "quit"))
        if width < 44:
            shortcuts = (("tab", "menu"), ("esc", "clear"), ("^C", "quit"))
        lines[-2:] = shortcut_footer(width, shortcuts, theme=theme)
    if native_image:
        from .list_screen import ScreenFrame
        return ScreenFrame(lines, native_image, top + 1, max(0, (width - image_width) // 2), image_width, len(image))
    return lines


def run_search_home(state: SearchHomeState, provider: str, *, message: str = "", provider_type: str = "anime", image_config=None) -> str | None:
    from .search_image import search_image_lines, search_image_width
    def key(value):
        if value == "?" and not state.query:
            from .key_guide import show_key_guide
            from .input_result import KeyResult
            show_key_guide((("Search", (("Enter", "Search or open focused item"), ("↑ / ↓", "Browse search history"),
                ("Tab / Shift+Tab", "Focus search, History, Downloads or Providers"), ("← / →", "Move text cursor"),
                ("Esc", "Clear input"))), ("Everywhere", (("?", "Keyboard guide (when input is empty)"),
                ("Ctrl+C", "Quit CLI")))))
            return KeyResult.CONSUMED
        return state.key(value)
    return run_framed_screen(
        lambda width, height, elapsed: search_home_lines(
            state, provider, width, height, message=message, provider_type=provider_type,
            cursor_visible=int(elapsed / .5) % 2 == 0,
            image=search_image_lines(image_config or {}, width, height),
            image_width=search_image_width(image_config or {}, width),
        ), key,
    )
