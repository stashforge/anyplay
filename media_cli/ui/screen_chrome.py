"""Shared landing/list screen palette, fixed shortcut footer and frame driver."""
from __future__ import annotations

import logging
import os
from .viewport import content_size as _content_size
import select
import time

from ..core.terminal import display_width, strip_ansi, truncate_display, sanitize_terminal_text
from .picker_render import get_key
from .terminal_output import write_terminal
from .terminal_runtime import borrow_terminal

from ..state.theme import MINT_SCREEN_THEME

_log = logging.getLogger(__name__)

RESET = "\033[0m"

def role_prefix(theme, role):
    return theme.prefix(theme.resolve_style(role))

# Compatibility aliases for the landing page; all colors originate in a preset.
ACCENT = role_prefix(MINT_SCREEN_THEME, "prompt")
DIM = role_prefix(MINT_SCREEN_THEME, "secondary")
FAINT = role_prefix(MINT_SCREEN_THEME, "muted")
FOREGROUND = role_prefix(MINT_SCREEN_THEME, "primary")


def standard_screen_title(prompt, context=()):
    """Name the screen, rather than repeating an input instruction in its header."""
    names = {'select mirror': 'Mirrors', 'select episode': 'Episodes', 'select song': 'Songs',
             'select anime': 'Anime', 'select version': 'Versions', 'select quality': 'Quality',
             'select action': 'Details', 'set watch progress': 'Progress'}
    label = names.get(str(prompt).casefold())
    if label is None:
        return str(prompt)
    mode = next((part for part in context if part in {'History', 'Search', 'Downloads', 'AniList', 'AnimeThemes'}), '')
    return f'{mode} · {label}' if mode else label



def list_header_context(title, context=(), *, badge=""):
    """Project old mode/provider context into fixed header slots.

    Application views should supply a badge explicitly. The compatibility path
    accepts the existing (mode, [offline], provider, extra context...) contract.
    Counts and tabs never inherit the mode or provider a second time.
    """
    parts = [str(part).strip() for part in context if str(part).strip()]
    modes = {"History", "Watch History", "Search", "Downloads", "AniList", "AnimeThemes"}
    mode = next((part for part in parts if part in modes), "")
    remaining = [part for part in parts if part not in modes]
    def is_flag(part):
        return part.startswith("[") or "INCOGNITO" in part.upper() or part in {"Sync Off", "Incognito"}
    if not badge:
        if mode in {"AniList", "AnimeThemes"}:
            badge = mode
        elif mode:
            badge = next((part for part in remaining if not is_flag(part)), "")
    remaining = [part for part in remaining if part != badge
                 and title != "Results · " + part.strip('"')]
    return title, badge, " · ".join(remaining)

def screen_header(title, *, width, badge=""):
    """Shared semantic heading and divider for ANSI and curses screens."""
    from .styled_text import StyledText, TextRun
    limit = max(1, width)
    badge = truncate_display(sanitize_terminal_text(badge), max(1, limit // 3)) if badge else ''
    budget = max(1, limit - display_width(badge) - (2 if badge else 0))
    heading = truncate_display('Anyplay · ' + sanitize_terminal_text(title), budget)
    runs = [TextRun(heading[:7], 'screen.brand'), TextRun(heading[7:], 'secondary')]
    if badge:
        runs.append(TextRun(' ' * max(1, limit - display_width(heading) - display_width(badge)) + badge, 'muted'))
    return StyledText(tuple(runs)), StyledText((TextRun('─' * limit, 'separator'),))


def shortcut_content(width, shortcuts):
    """Semantic shortcut spacing shared by ANSI frames and curses."""
    from .styled_text import StyledText, TextRun, fit_styled_text
    labels = [(key.split('/', 1)[0] if '/' in key and key != '/' else key, label) for key, label in shortcuts]
    used = sum(display_width(key + ' ' + label) for key, label in labels)
    gap = max(1, min(3, (width - used) // max(1, len(labels) - 1)))
    runs = []
    for index, (key, label) in enumerate(labels):
        if index:
            runs.append(TextRun(' ' * gap, 'actions'))
        runs.extend((TextRun(key, 'screen.key'), TextRun(' ' + label, 'actions')))
    return fit_styled_text(StyledText(tuple(runs)), columns=max(1, width))


def shortcut_footer(width, shortcuts, *, query=None, message="", message_priority=False, theme=MINT_SCREEN_THEME):
    """Exactly two rows: divider, then shortcuts or the active filter editor."""
    limit = max(1, width - 1)
    border = role_prefix(theme, "separator")
    accent = role_prefix(theme, "prompt")
    key_style = role_prefix(theme, "screen.key")
    dim = role_prefix(theme, "actions")
    rule = border + "─" * limit + RESET
    if strip_ansi(message).strip() and (message_priority or query is None):
        content = message
    elif query is not None:
        content = accent + "/" + RESET + query + RESET
        if strip_ansi(message).strip() and display_width(content) + 8 < limit:
            content += dim + " · " + RESET + message
    else:
        from .styled_text import render_styled_text
        content = render_styled_text(shortcut_content(limit, shortcuts), width=width, theme=theme)
    return [rule, truncate_display(content, limit)]


class ScreenTransition(Exception):
    """Internal list rebuild, without leaving the terminal UI."""


def run_framed_screen(render, on_key, *, frame_ready=None, batch_navigation=False, frame_memory=None):
    """Run on the UI owner; render receives size and elapsed time since input."""
    from .display import enter_alt_screen
    from .picker import remember_screen_frame
    from .frame_surface import compose, invalidate_text
    from .stall_diagnostics import ListStallDiagnostics
    def timed_call(stage, function, *args):
        began = time.perf_counter()
        cpu_began = time.thread_time()
        stalls.stage(stage)
        try:
            return function(*args)
        finally:
            elapsed = time.perf_counter() - began
            stalls.stage("between-operations")
            if elapsed >= .05:
                _log.debug("Slow list operation: stage=%s elapsed_ms=%.1f cpu_ms=%.1f",
                           stage, elapsed*1000, (time.thread_time()-cpu_began)*1000)

    with ListStallDiagnostics() as stalls, borrow_terminal() as runtime:
        if runtime is None:
            return None
        enter_alt_screen()
        invalidate_text()
        started = time.monotonic()
        try:
            painted = False
            while True:
                # Process repeats that arrived during the previous frame before
                # accepting refresh results or rebuilding another expensive frame.
                if painted and batch_navigation:
                    deadline = time.monotonic() + .005
                    for _ in range(32):
                        if time.monotonic() >= deadline or not select.select([runtime.fileno()], [], [], 0)[0]:
                            break
                        pending = timed_call("key-read", get_key, runtime.fileno())
                        if pending == "ALT_D":
                            from .download_manager import run_download_manager
                            result = run_download_manager(standalone=False)
                            if result == "QUIT":
                                raise KeyboardInterrupt
                            break
                        result = timed_call("key-dispatch", on_key, pending)
                        from .input_result import KeyResult
                        if result is not None and result is not KeyResult.CONSUMED:
                            return result
                        started = time.monotonic()
                        if pending not in ("UP", "DOWN"):
                            break
                frame_started = time.perf_counter()
                current = _content_size(runtime.fileno())
                frame = timed_call("render", render, current.columns, current.lines, time.monotonic() - started)
                rendered = time.perf_counter()
                from .list_screen import ScreenFrame
                if not isinstance(frame, ScreenFrame):
                    frame = ScreenFrame(frame)
                stalls.stage("terminal-write")
                write_terminal("\033[?25l" + compose(frame, current.lines, current.columns)
                               + f"\033[{current.lines};{current.columns}H\033[?25l")
                remember_screen_frame(frame.lines)
                painted = True
                finished = time.perf_counter()
                if finished - frame_started >= .05:
                    _log.debug("Slow list frame: render_ms=%.1f terminal_ms=%.1f total_ms=%.1f rows=%d columns=%d",
                               (rendered-frame_started)*1000, (finished-rendered)*1000,
                               (finished-frame_started)*1000, current.lines, current.columns)
                if frame_ready is not None:
                    frame_ready()
                    frame_ready = None
                stalls.stage("input-wait")
                if not select.select([runtime.fileno()], [], [], .1)[0]:
                    continue
                key = timed_call("key-read", get_key, runtime.fileno())
                if key == "ALT_D":
                    from .download_manager import run_download_manager
                    result = run_download_manager(standalone=False)
                    if result == "QUIT":
                        raise KeyboardInterrupt
                    continue
                result = timed_call("key-dispatch", on_key, key)
                # Drain queued arrow repeats before repainting expensive covers.
                # Dispatch the first other key before any refresh can replace this frame.
                if batch_navigation and key in ("UP", "DOWN") and result is None:
                    deadline = time.monotonic() + .005
                    for _ in range(31):
                        if time.monotonic() >= deadline or not select.select([runtime.fileno()], [], [], 0)[0]:
                            break
                        following = timed_call("key-read", get_key, runtime.fileno())
                        result = timed_call("key-dispatch", on_key, following)
                        if following not in ("UP", "DOWN") or result is not None:
                            break
                started = time.monotonic()
                from .input_result import KeyResult
                if result is KeyResult.CONSUMED:
                    continue
                if result is not None:
                    return result
        finally:
            # The next frame or application terminal cleanup owns image removal.
            # Leaving a list for a loading footer must not erase its cover.
            write_terminal("\033[?25l")
