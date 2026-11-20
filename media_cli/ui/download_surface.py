"""Download queue adapter using the shared terminal frame, without screen switching."""

import curses
import os
from .viewport import content_size as _content_size
import select

from ..state.theme import MINT_SCREEN_THEME
from .frame_surface import compose
from .list_screen import ScreenFrame
from .picker_render import get_key
from .styled_text import render_styled_text
from .terminal_output import write_terminal
from .terminal_runtime import borrow_terminal


class DownloadSurface:
    def __init__(self, runtime):
        self.runtime = runtime
        self.rows = []

    def getmaxyx(self):
        size = _content_size(self.runtime.fileno())
        return size.lines, size.columns

    def render_frame(self, frame):
        height, width = self.getmaxyx()
        self.rows = [render_styled_text(line, width=width, theme=MINT_SCREEN_THEME) for line in frame]
        self.refresh()

    def refresh(self):
        height, width = self.getmaxyx()
        write_terminal("\033[?25l" + compose(ScreenFrame(self.rows), height, width))

    def erase(self):
        self.rows = [""] * self.getmaxyx()[0]

    def addstr(self, y, x, value, attr=0):
        # Only the existing exit dialog uses this compatibility path.
        height, width = self.getmaxyx()
        while len(self.rows) < height:
            self.rows.append("")
        plain = self.rows[y]
        self.rows[y] = plain[:x].ljust(x) + value + plain[x + len(value) :]

    def getch(self):
        fd = self.runtime.fileno()
        if not select.select([fd], [], [], 0.15)[0]:
            return -1
        key = get_key(fd)
        mapping = {
            "ESC": 27,
            "CTRL_C": 3,
            "ENTER": 10,
            "TAB": 9,
            "BTAB": curses.KEY_BTAB,
            "UP": curses.KEY_UP,
            "DOWN": curses.KEY_DOWN,
            "LEFT": curses.KEY_LEFT,
            "RIGHT": curses.KEY_RIGHT,
            "HOME": curses.KEY_HOME,
            "END": curses.KEY_END,
            "PAGE_UP": curses.KEY_PPAGE,
            "PAGE_DOWN": curses.KEY_NPAGE,
            "ALT_D": 27,
            "EOF": 3,
        }
        return mapping.get(key, ord(key) if len(key) == 1 else -1)

    def nodelay(self, value):
        pass

    def timeout(self, value):
        pass

    def leaveok(self, value):
        pass


def queue_wrapper(runner):
    from .display import enter_alt_screen

    with borrow_terminal() as runtime:
        if runtime is None:
            return "BACK"
        enter_alt_screen()
        return runner(DownloadSurface(runtime))
