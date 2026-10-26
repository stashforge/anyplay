"""
Bottom-anchored TUI picker for Anyplay.

Extracted from ``app.py`` so that the picker lives with the other ``ui/``
modules.  The only structural change is that the four ``globals()`` reads
have been replaced by explicit ``flags: CliFlags`` and ``ui: UiState``
parameters:

* ``globals()["SHOW_IMAGE"]``            → ``flags.show_image``
* ``globals()["_hovered_show_id"]``      → ``ui.hovered_show_id``
* ``globals()["_active_picker_query"]``  → ``ui.active_picker_query``
* ``globals()["_needs_redraw"]``         → module-level ``_needs_redraw``
  (still a plain bool, but owned here rather than in ``app.py``)

All other logic is **preserved exactly** from the original.
"""

from __future__ import annotations

import hashlib
import logging
import os
import select
import signal
import sys
import termios
import time
from collections.abc import Hashable, Sequence
from typing import TYPE_CHECKING, Callable

from ..core.api import ProviderDependencyError
from ..core.terminal import (
    absolute_terminal_frame as _absolute_terminal_frame,
)
from ..core.terminal import (
    bottom_align_panel_lines as _bottom_align_panel_lines,
)
from ..core.terminal import (
    display_width as _display_width,
)
from ..core.terminal import (
    fit_terminal_line as _fit_terminal_line,
)
from ..core.terminal import (
    picker_vertical_layout as _picker_vertical_layout,
)
from ..core.terminal import (
    sanitize_terminal_text,
)
from ..core.terminal import (
    strip_ansi as _strip_ansi,
)
from ..core.terminal import (
    truncate_display as _truncate_display,
)
from . import terminal_images
from .footer import Footer, FooterStatus, StatusKind, render_footer
from .loading_controls import LoadingControls
from .loading_messages import result_wait_messages
from .covers import (
    poster_symbol_lines as _poster_symbol_lines,
)
from .covers import (
    poster_uses_native_protocol as _poster_uses_native_protocol,
)
from .navigation_bar import NavigationBar, layout_navigation_bar
from .layout import MIN_RESULT_ROWS_WITH_COVER, MIN_TUI_COLUMNS
from .navigation_counter import CounterMode, NavigationCounter
from .navigation_tabs import NavigationTabs
from .picker_dispatch import RendererUnavailable, dispatch_legacy_picker
from .picker_render import (
    get_key as _get_key,
)
from .picker_render import (
    match as _match,
)
from .picker_render import (
    render_item as _render_item,
)
from .picker_state import PickerState
from .query_cursor import QueryCursor
from .row_adjustment import adjustment_direction
from .input_result import KeyResult
from .picker_actions import picker_bindings
from .query_viewport import QueryField
from .row_layout import DEFAULT_ROW_INDENTATION, RowIndentation, RowKind
from .scroll_indicators import ScrollIndicators
from .styled_text import StyledText, TextRun, render_styled_text
from .width_guard import render_width_notice

if TYPE_CHECKING:
    from ..context import CliFlags, UiState

# ---------------------------------------------------------------------------
# Colours (Catppuccin-inspired, matching original fzf theme)
# ---------------------------------------------------------------------------
_C_NORMAL = "\033[38;5;252m"  # #cccccc  normal item text
_C_SEL = "\033[1;97m"  # bright-white bold  selected item
_C_PTR = "\033[38;2;243;139;168m"  # #f38ba8  pink pointer ❯
_C_PROMPT = "\033[38;2;203;166;247m"  # #cba6f7  mauve prompt
_C_MATCH = "\033[38;2;137;180;250m"  # #89b4fa  blue match highlight
_C_QUERY = "\033[38;2;137;220;235m"  # #89dceb  teal query text
_C_HINT = "\033[38;5;244m"  # #888888  dim hints / footer
_C_COUNT = "\033[38;5;244m"  # same dim for count
_C_SCROLLI = "\033[38;2;243;139;168m"  # pink scroll indicators
_RST = "\033[0m"

# ---------------------------------------------------------------------------
# Module-level redraw flag (was globals()["_needs_redraw"] in app.py)
# ---------------------------------------------------------------------------
_needs_redraw: bool = True
_persistent_rendered_lines: list[str] = []


def cached_frame_rows() -> int:
    """Report cached frame size for diagnostics, not proof of terminal contents."""
    return len(_persistent_rendered_lines)


def invalidate_screen_buffer() -> None:
    global _persistent_rendered_lines
    if _persistent_rendered_lines:
        logging.getLogger(__name__).debug("Picker frame cache invalidated: rows=%d", cached_frame_rows())
    _persistent_rendered_lines = []


def invalidate_screen_row(row: int) -> None:
    global _persistent_rendered_lines
    if 0 <= row - 1 < len(_persistent_rendered_lines):
        _persistent_rendered_lines[row - 1] = ""


def _handle_sigwinch(signum, frame) -> None:
    global _needs_redraw, _persistent_rendered_lines
    _needs_redraw = True
    _persistent_rendered_lines = []


try:
    signal.signal(signal.SIGWINCH, _handle_sigwinch)
except Exception:
    pass


# ---------------------------------------------------------------------------
# Public picker
# ---------------------------------------------------------------------------


from .terminal_runtime import borrow_terminal, terminal_application


def _legacy_navigation_line(
    prompt, query, cursor, cursor_visible, columns, *, counts=None, hidden_above=0, hidden_below=0
):
    """Retain the previous divider renderer until live-layout review is complete."""
    block = "\033[48;2;137;220;235;38;2;30;30;46m"
    if cursor < len(query):
        before, char, after = query[:cursor], query[cursor], query[cursor + 1 :]
        query_disp = (
            f"{_C_QUERY}{before}{block}{char}{_RST}{_C_QUERY}{after}{_RST}"
            if cursor_visible else f"{_C_QUERY}{before}{char}{after}{_RST}"
        )
    else:
        query_disp = (
            f"{_C_QUERY}{query}{_RST}{block} {_RST}" if cursor_visible else f"{_C_QUERY}{query}{_RST} "
        )
    pstr = f"{_C_PROMPT}{prompt} \u276f{_RST} {query_disp}"
    if counts is None:
        return _fit_terminal_line(pstr, columns)
    filtered, total, selected = counts
    cstr = (
        f"\033[38;2;166;227;161m{selected} selected\033[0m {_C_COUNT}| {filtered}/{total}{_RST}"
        if selected else f"{_C_COUNT}{filtered}/{total}{_RST}"
    )
    hints = ""
    if hidden_above:
        hints += f"\u25b2{hidden_above} "
    if hidden_below:
        hints += f"\u25bc{hidden_below} "
    if hints:
        hints = f" {_C_SCROLLI}{hints}{_RST}"
    used = _display_width(f"{pstr}  {cstr}{hints}")
    rule = "\u2500" * max(1, columns - used - 2)
    return _fit_terminal_line(f"{pstr}  {cstr}{hints} {_C_HINT}{rule}{_RST}", columns)


def tui_pick(
    flags: CliFlags,
    ui: UiState,
    prompt,
    options,
    header: str = "",
    hints=None,
    live_fn=None,
    header_fn=None,
    top_header_fn=None,
    tab_fn=None,
    reverse_fn=None,
    alt_reverse_fn=None,
    delete_fn=None,
    delete_prompt=None,
    can_delete_fn=None,
    right_fn=None,
    info_fn=None,
    return_query_on_enter: bool = False,
    query_history=None,
    hide_separator: bool = False,
    initial_query: str = "",
    is_search: bool = False,
    help_dict=None,
    auto_select_single_when_done: bool | Callable[[], bool] = False,
    keep_cursor_hidden_on_select: bool = False,
    count_total=None,
    disabled_indices=None,
    reverse_items=True,
    tick_fn=None,
    multi_select: bool = False,
    select_fn=None,
    select_loading_message_fn=None,
    initial_selected: int = 0,
    item_prefix_fn=None,
    state: PickerState | None = None,
    heading_indices=None,
    return_on_quit: bool = False,
    structured: bool = False,
    action_labels: Sequence[str] | None = None,
    filter_fn: Callable[[str, Sequence[str]], list[int]] | None = None,
    key_fn: Callable[[str, int], bool | KeyResult] | None = None,
    exit_fn: Callable[[], bool] | None = None,
    row_indentation: RowIndentation = DEFAULT_ROW_INDENTATION,
    *,
    navigation_prompt: StyledText | None = None,
    navigation_tabs: NavigationTabs | None = None,
    adjust_fn: Callable[[int, int], bool] | None = None,
    frame_ready_fn: Callable[[], None] | None = None,
    input_pending_fn: Callable[[], bool] | None = None,
    item_render_fn: Callable[[int, str, bool, int], str | None] | None = None,
    confirmation_index: int | None = None,
    item_key_fn: Callable[[int], Hashable] | None = None,
):
    """Bottom-anchored alt-screen picker with flipped (bottom-up) item list.

    Parameters
    ----------
    flags:
        Read-only CLI flags (used to check ``show_image``).
    ui:
        Mutable UI state; the picker writes ``active_picker_query`` and
        reads ``hovered_show_id`` for poster rendering.
    prompt:
        Title string or zero-argument callable that returns a string.
    options:
        List of string option labels.
    state:
        Optional mutable position. Overrides initial query/selection; selection
        and marks use underlying indexes, while scroll uses filtered positions.
    heading_indices:
        Nonselectable headings, distinct from disabled actions.
    row_indentation:
        Extra leading columns by option/disabled/heading type. Zero defaults
        preserve the existing marker gutter; prefixes remain row content.
    navigation_prompt, navigation_tabs:
        Explicit semantic prompt/tab content from structured views. Legacy
        prompts remain literal strings; the renderer never parses tab names.
    return_on_quit:
        Return -5 on EOF or interrupt. Printable letters remain filter input.
    structured, action_labels:
        Compatibility dispatch forwards these to plain mode when selected.
        Structured TUI entry preserves pending input across adapter restarts.
    filter_fn:
        Optional local filter returning ordered underlying option indexes.
    key_fn, exit_fn:
        Optional transition hooks; key_fn receives the decoded key and focused
        underlying index (-1 without eligible focus). A true result returns -6.
        KeyResult.CONSUMED keeps the current picker running without a transition.
    adjust_fn:
        Optional row adjustment callback (underlying index, -1/+1 direction).
        Called only for outward boundary arrows, never inside query text or in
        query-entry mode. True requests a -6 transition; False keeps normal input.
    frame_ready_fn:
        Called once after the first normal content frame is written and flushed,
        with no partial loading input outstanding. Notices/help do not qualify.
    input_pending_fn:
        Caller-owned blocked/data-ready state, retained until destination paint.
        While true, drain loading input through key_fn before live updates.
        Only the caller (for example in frame_ready_fn) clears this state.

    Returns
    -------
    int | str
        * ``≥0``  — index of the selected option.
        * ``-2``  — Esc pressed.
        * ``-3``  — Left arrow pressed.
        * ``-4``  — No options (live search finished with empty results).
        * ``-5``  — Quit/EOF/interrupt when *return_on_quit* is True.
        * ``-6``  — Transition requested by *key_fn* or *exit_fn*.
        * ``str`` — when *return_query_on_enter* is True.
    """
    global _needs_redraw

    prompt_key = str(prompt() if callable(prompt) else prompt)

    def current_prompt() -> str:
        return str(prompt() if callable(prompt) else prompt)

    tty_fd = -1
    tty_file = None
    try:
        with borrow_terminal() as runtime:
            if runtime is None:
                raise RendererUnavailable("No terminal runtime")
            runtime.assert_owner_thread()
            tty_fd = os.dup(runtime.fileno())
        tty_file = os.fdopen(tty_fd, "rb+", buffering=0)
        tty_fd = tty_file.fileno()
    except OSError as error:
        if tty_file:
            try:
                tty_file.close()
            except Exception:
                pass
            tty_file = None
        if tty_fd >= 0:
            os.close(tty_fd)
        raise RendererUnavailable("Cannot open picker terminal") from error

    try:
        termios.tcgetattr(tty_fd)
    except (OSError, termios.error) as error:
        try:
            tty_file.close()
        except Exception:
            pass
        raise RendererUnavailable("Cannot read picker terminal settings") from error

    # Enter alt screen (only sends escape sequence if not already active)
    try:
        from . import display as _display

        _display.enter_alt_screen()
    except ProviderDependencyError:
        tty_file.close()
        raise
    except Exception:
        from .terminal_output import write_terminal
        write_terminal("\033[?1049h\033[2J\033[?25l")
        sys.stdout.flush()

    options = list(options or [])

    def option_keys(items):
        if item_key_fn is None:
            return tuple(items)
        keys = tuple(item_key_fn(index) for index in range(len(items)))
        if len(set(keys)) != len(keys):
            raise ValueError("Picker item keys must be unique")
        return keys

    keys = option_keys(options)
    first_frame_pending = True
    frame_ack_pending = frame_ready_fn is not None
    loading_input = None
    loading_input_waiting = False
    loading_transition = False
    picker_started = time.monotonic()
    query = state.query if state is not None else initial_query
    cursor_pos = len(query)
    if state is not None and state.query_cursor is not None:
        cursor_pos = min(state.query_cursor, len(query))
    query_start = 0
    width_notice_active = False
    sel = 0
    scroll = max(0, state.scroll) if state is not None else 0
    page_size = 1
    history_idx = -1
    cur_header = header_fn(0) if header_fn else header
    show_help = False
    last_poster_tick = 0
    last_poster_key = None
    last_clock_minute = int(time.time() // 60)
    last_hint_poll_time = 0.0
    last_rendered_hint_val = None
    pending_delete_index = confirmation_index
    boundary_hint = ""
    boundary_hint_time = 0.0
    boundary_action = ""
    cursor_activity = state.cursor_activity if state is not None else None
    if cursor_activity is None:
        cursor_activity = QueryCursor(time.time())
    last_cursor_phase = (True, True)

    disabled_indices = set(disabled_indices or ())
    heading_indices = set(heading_indices or ())
    marked_indices: set[int] = set(state.marked_indices) if state is not None else set()
    input_bindings = picker_bindings(
        tabs=tab_fn is not None, info=info_fn is not None, delete=delete_fn is not None,
        reverse=reverse_fn is not None, refresh=alt_reverse_fn is not None,
        multiple=multi_select, help_available=bool(help_dict),
    )
    live_done = live_fn is None
    live_feedback = LoadingControls(input_bindings)
    last_live_feedback = ""

    def waiting_for_results():
        return live_fn is not None and not live_done and not options and not return_query_on_enter and not structured

    def eligible(option_index):
        return (
            0 <= option_index < len(options)
            and option_index not in disabled_indices
            and option_index not in heading_indices
        )

    try:
        last_terminal_size = os.get_terminal_size(tty_fd)
    except OSError:
        last_terminal_size = None

    def filt_list():
        if filter_fn is not None:
            return list(dict.fromkeys(
                index for index in filter_fn(query, options) if 0 <= index < len(options)
            ))
        if not query:
            return list(range(len(options)))
        scored = []
        for i, o in enumerate(options):
            res = _match(query, _strip_ansi(o))
            if res is not None:
                scored.append((res[0], i))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in scored]

    def first_selectable(filt):
        for pos, option_index in enumerate(filt):
            if eligible(option_index):
                return pos
        return 0

    def restored_selection(previous_key, previous_position):
        selectable = [pos for pos, index in enumerate(filt) if eligible(index)]
        matching = [pos for pos in selectable if keys[filt[pos]] == previous_key]
        return min(matching or selectable, key=lambda pos: abs(pos - previous_position), default=0)

    def move_selection(filt, current, delta):
        if not filt:
            return 0
        selectable = [
            pos
            for pos, option_index in enumerate(filt)
            if eligible(option_index)
        ]
        if not selectable:
            return 0
        if current not in selectable:
            return selectable[0]
        current_pos = selectable.index(current)
        return selectable[(current_pos + delta) % len(selectable)]

    def jump_selection(filt, current, toward_end, *, page=False):
        selectable = [pos for pos, option_index in enumerate(filt) if eligible(option_index)]
        if not selectable:
            return 0
        edge = selectable[-1] if toward_end else selectable[0]
        if not page:
            return edge
        target = current + (page_size if toward_end else -page_size)
        # Page distance counts visible row slots, including headings/disabled rows.
        candidates = (pos for pos in selectable if pos >= target) if toward_end else (
            pos for pos in reversed(selectable) if pos <= target
        )
        return next(candidates, edge)

    def render(filt):
        global _persistent_rendered_lines
        nonlocal scroll, last_poster_key, sel, last_rendered_hint_val, first_frame_pending
        nonlocal page_size, query_start, width_notice_active
        nonlocal frame_ack_pending
        runtime.assert_owner_thread()
        runtime.input_bindings = input_bindings
        render_started = time.monotonic()

        if filt and any(oi >= len(options) or oi < 0 for oi in filt):
            filt = filt_list()
            sel = max(0, min(sel, len(filt) - 1)) if filt else 0

        try:
            sz = os.get_terminal_size(tty_fd)
            rows, cols = sz.lines, sz.columns
        except OSError:
            rows, cols = 24, 80

        if cols < MIN_TUI_COLUMNS:
            if not width_notice_active:
                logging.getLogger(__name__).debug("Picker waiting for width: columns=%s minimum=%s", cols, MIN_TUI_COLUMNS)
            width_notice_active = True
            last_poster_key = None
            notice = list(render_width_notice(columns=max(1, cols)))
            out = [""] * max(0, rows - len(notice)) + notice
            previous = _persistent_rendered_lines if len(_persistent_rendered_lines) == rows else None
            frame = _absolute_terminal_frame(out, rows, cols, previous)
            clear_prefix = terminal_images.clear_if_active()
            _persistent_rendered_lines = list(out)
            first_frame_pending = False
            tty_file.write(f"{clear_prefix}\033[?25l{frame}\033[{rows};{cols}H\033[?25l".encode())
            tty_file.flush()
            return
        if width_notice_active:
            logging.getLogger(__name__).debug("Picker width restored: columns=%s", cols)
            width_notice_active = False

        if show_help and help_dict:
            last_poster_key = None
            out = []
            out.append("")
            out.append(f"  {_C_HINT}=== Keyboard Shortcuts ==={_RST}")
            out.append("")
            for k, v in help_dict.items():
                out.append(f"  {_C_PTR}{k:<15}{_RST} {v}")
            out.append("")
            out.append(f"  {_C_HINT}Press Esc or ? to close{_RST}")
            padding = rows - len(out)
            for _ in range(max(0, padding)):
                out.append("")
            frame = _absolute_terminal_frame(out, rows, cols)
            clear_prefix = terminal_images.clear_now()
            buf = f"{clear_prefix}\033[?25l{frame}\033[{rows};{cols}H\033[?25l"
            _persistent_rendered_lines = list(out)
            tty_file.write(buf.encode())
            tty_file.flush()
            return

        actual_top_header = None
        if top_header_fn is not None and filt:
            sel_idx = filt[sel] if sel < len(filt) else -1
            actual_top_header = top_header_fn(sel_idx)

        actual_header = cur_header
        ui.active_picker_query = query
        if header_fn is not None:
            sel_idx = filt[sel] if (filt and sel < len(filt)) else -1
            actual_header = header_fn(sel_idx)
        header_lines = actual_header.splitlines() if actual_header else []
        wait_message = live_feedback.message("") if waiting_for_results() else ""
        if pending_delete_index is not None:
            if callable(delete_prompt):
                p_text = delete_prompt(pending_delete_index)
            elif delete_prompt:
                p_text = str(delete_prompt)
            else:
                p_text = "Delete this history entry? y/N"
            confirm_line = f"\033[38;5;220m{p_text}\033[0m"
            if header_lines:
                header_lines[-1] = confirm_line
            else:
                header_lines = [confirm_line]
        elif wait_message:
            from .display import _configured_loading_frame

            wait_line = render_footer(Footer(statuses=(FooterStatus(
                wait_message, StatusKind.LOADING, _configured_loading_frame(),
            ),)), cols)
            if header_lines:
                header_lines[-1] = wait_line
            else:
                header_lines = [wait_line]
        elif boundary_hint and time.time() - boundary_hint_time < 1.5:
            hint_line = f"\033[38;5;222m* {boundary_hint}\033[0m"
            if header_lines:
                header_lines[-1] = hint_line
            else:
                header_lines = [hint_line]
        header_n = (
            max(4, len(header_lines)) if header_fn is not None else len(header_lines)
        )

        show_img = flags.show_image

        is_tmx = os.environ.get("PREFIX", "").startswith(
            "/data/data/com.termux"
        ) or os.path.exists("/data/data/com.termux")
        top_header_n = (
            # None means no slot; an empty string retains space while loading.
            (9 if is_tmx else 8) if (show_img and top_header_fn is not None and actual_top_header is not None) else 0
        )
        top_margin = 1 if top_header_n > 0 else 0

        GAP = 0
        fixed = top_margin + 1 + header_n + top_header_n + GAP

        # Image/gap, navigation and details are reserved before result slots.
        if rows - fixed < MIN_RESULT_ROWS_WITH_COVER and top_header_n > 0:
            top_header_n = 0
            top_margin = 0
            GAP = 0
            fixed = 1 + header_n

        max_vis, _, _ = _picker_vertical_layout(
            rows, header_n, top_header_n, top_margin, GAP, len(filt)
        )
        page_size = max_vis

        if filt:
            scroll = max(0, min(scroll, len(filt) - 1))
            if sel < scroll:
                scroll = sel
            elif sel >= scroll + max_vis:
                scroll = sel - max_vis + 1

        out = []

        visible = filt[scroll : scroll + max_vis] if filt else []
        items_shown = len(visible)
        item_max_w = max(20, cols - 3)

        _, _, padding = _picker_vertical_layout(
            rows, header_n, top_header_n, top_margin, GAP, items_shown
        )
        for _ in range(padding):
            out.append("\033[2K")

        poster_key = None
        native_poster = ""
        poster_row = None

        if top_header_n > 0:
            poster_raw = actual_top_header or ""
            poster_lines = _poster_symbol_lines(poster_raw, top_header_n, cols)
            poster_row = len(out) + 1
            poster_key = (
                ui.hovered_show_id,
                hashlib.sha256(poster_raw.encode("utf-8", errors="ignore")).hexdigest(),
                poster_row,
                top_header_n,
                cols,
            )
            native_poster = (
                poster_raw if _poster_uses_native_protocol(poster_raw) else ""
            )
            for row in range(top_header_n):
                line = poster_lines[row] if row < len(poster_lines) else ""
                out.append(f"\033[2K{line}")

        for _ in range(top_margin):
            out.append("\033[2K")
        for _ in range(GAP):
            out.append("\033[2K")

        item_positions = (
            range(items_shown - 1, -1, -1) if reverse_items else range(items_shown)
        )
        for vi in item_positions:
            oi = visible[vi]
            if not (0 <= oi < len(options)):
                continue
            is_sel = scroll + vi == sel
            disabled = not eligible(oi)
            kind = RowKind.HEADING if oi in heading_indices else RowKind.DISABLED if disabled else RowKind.OPTION
            indent_width = min(row_indentation.columns_for(kind), max(0, cols - 3))
            indent = " " * indent_width
            prefix = item_prefix_fn(oi) if item_prefix_fn else ""
            prefix_w = _display_width(prefix)
            eff_max_w = max(10, item_max_w - prefix_w - indent_width)
            if disabled:
                width = max(0, cols - 2 - prefix_w - indent_width)
                label = item_render_fn(oi, "", False, width) if item_render_fn else None
                if label is None:
                    label = _truncate_display(str(options[oi] or ""), width)
                out.append(f"\033[2K{_fit_terminal_line(f'{indent}  {prefix}{label}', cols)}")
                continue
            ptr = f"{_C_PTR}\u276f{_RST}" if is_sel and not disabled else " "
            label = item_render_fn(oi, query, is_sel, eff_max_w) if item_render_fn else None
            if label is None:
                label = _render_item(options[oi], query, is_sel, max_w=eff_max_w)
            if multi_select:
                is_marked = oi in marked_indices
                box = (
                    "\033[38;2;166;227;161m[✔]\033[0m "
                    if is_marked
                    else "\033[38;5;244m[ ]\033[0m "
                )
                label = f"{box}{label}"
            if prefix:
                label = f"{prefix}{label}"
            hint = ""
            if is_sel and hints:
                kp = _strip_ansi(options[oi])
                hint_text = ""
                if callable(hints):
                    hint_text = str(hints(oi) or "")
                    last_rendered_hint_val = hint_text
                elif isinstance(hints, dict):
                    for k, v in hints.items():
                        if _strip_ansi(k) == kp:
                            hint_text = str(v)
                            break
                if hint_text and hint_text.strip().lower() != kp.strip().lower():
                    hint_w = max(
                        0,
                        cols
                        - indent_width
                        - _display_width(ptr)
                        - 2
                        - _display_width(label)
                        - 2,
                    )
                    if hint_w > 0:
                        hint = f"  {_C_HINT}{_truncate_display(hint_text, hint_w)}{_RST}"
            out.append(f"\033[2K{_fit_terminal_line(f'{indent}{ptr} {label}{hint}', cols)}")

        now = time.time()
        cursor_active, blink_on = cursor_activity.phase(now, filtering=bool(query))
        counters = ()
        scroll_hints = None
        show_counts = not hide_separator and bool(options)
        if show_counts:
            total_count = count_total() if callable(count_total) else count_total
            total_count = len(options) if total_count is None else total_count
            filtered_count = (
                sum(1 for option_index in filt if eligible(option_index))
                if disabled_indices or heading_indices
                else len(filt)
            )
            counters = (
                (NavigationCounter(CounterMode.SELECTED, len(marked_indices)),)
                if multi_select and marked_indices else ()
            )
            counters += (NavigationCounter(CounterMode.FILTERED, filtered_count, total_count),)
            hidden_before, hidden_after = scroll, max(0, len(filt) - scroll - max_vis)
            scroll_hints = (
                ScrollIndicators(hidden_after, hidden_before)
                if reverse_items else ScrollIndicators(hidden_before, hidden_after)
            )
        navigation = NavigationBar(
            prompt=(
                navigation_prompt if navigation_prompt is not None
                else StyledText((TextRun(sanitize_terminal_text(current_prompt()), "prompt"),))
            ),
            tabs=navigation_tabs,
            marker=StyledText((TextRun("\u276f", "prompt"),)),
            query=QueryField(query, cursor_pos, cursor_active and blink_on),
            query_start=min(query_start, len(query)),
            counters=counters,
            scroll=scroll_hints,
            rule=show_counts,
        )
        navigation_layout = layout_navigation_bar(navigation, width=cols)
        query_start = navigation_layout.query_start
        out.append("\033[2K" + render_styled_text(navigation_layout.content, width=cols))

        for line in _bottom_align_panel_lines(header_lines, header_n):
            if not line:
                out.append("\033[2K")
            else:
                out.append(f"\033[2K{_fit_terminal_line(line, cols)}")

        poster_changed = poster_key != last_poster_key
        clear_prefix = (
            terminal_images.clear_now()
            if poster_changed
            and (poster_key is not None or last_poster_key is not None)
            else ""
        )
        if first_frame_pending and not clear_prefix:
            clear_prefix = terminal_images.clear_if_active()
        last_poster_key = poster_key
        overlay = ""
        if poster_changed and native_poster and poster_row is not None:
            terminal_images.mark_active()
            overlay = f"\033[{poster_row};1H{native_poster}"

        # Differential write: only send ANSI escape sequences for rows that actually changed
        if len(_persistent_rendered_lines) != rows:
            _persistent_rendered_lines = []
        prev_lines = _persistent_rendered_lines
        frame = _absolute_terminal_frame(out, rows, cols, previous_lines=prev_lines)
        if first_frame_pending and not prev_lines:
            frame = "\033[2J" + frame
        _persistent_rendered_lines = list(out)

        acknowledge_frame = frame_ack_pending and not loading_input_waiting
        if frame or clear_prefix or overlay or acknowledge_frame:
            # Park hidden hardware cursor at bottom-right so it never renders under graphics
            buf = f"{clear_prefix}\033[?25l{frame}{overlay}\033[{rows};{cols}H\033[?25l"
            tty_file.write(buf.encode())
            tty_file.flush()
            if acknowledge_frame:
                frame_ack_pending = False
                frame_ready_fn()
        elapsed = time.monotonic() - render_started
        if first_frame_pending:
            first_frame_pending = False
            from ..core.startup import startup_first_frame

            startup_first_frame()
            logging.getLogger(__name__).debug(
                "Picker first frame ready: elapsed=%.3fs rows=%s items=%s",
                time.monotonic() - picker_started, rows, len(options),
            )
        if elapsed >= 0.1:
            logging.getLogger(__name__).debug("Slow picker frame: %.3fs", elapsed)

    # -----------------------------------------------------------------------
    # Main event loop
    # -----------------------------------------------------------------------
    from ..core import reporting

    old_status_sink = reporting._status_sink
    reporting.set_status_sink(lambda msg, color: True)
    filt = []
    try:
        tty_file.write(b"\033[?25l")
        tty_file.flush()
        runtime.ensure_raw()

        filt = filt_list()
        sel = first_selectable(filt)
        if state is not None:
            selectable = [pos for pos, index in enumerate(filt) if eligible(index)]
            sel = min(selectable, key=lambda pos: abs(filt[pos] - state.selected_index), default=0)
            scroll = min(scroll, max(0, len(filt) - 1))
        elif (
            initial_selected > 0
            and filt
            and 0 <= initial_selected < len(filt)
            and eligible(filt[initial_selected])
        ):
            sel = initial_selected
        result = -2

        def _select_loading_message(target_idx):
            if select_loading_message_fn is None:
                return ""
            try:
                return str(select_loading_message_fn(target_idx) or "")
            except TypeError:
                return str(select_loading_message_fn() or "")

        def _run_select_with_picker_loading(target_idx, message):
            from .display import with_loading
            from .transition import PreparationCancelled

            try:
                return bool(with_loading(message, select_fn, target_idx))
            except PreparationCancelled:
                invalidate_screen_buffer()
                render(filt)
                return False

        def _can_select(target_idx):
            if not eligible(target_idx):
                return False
            if select_fn is None:
                return True
            message = _select_loading_message(target_idx)
            if message:
                return _run_select_with_picker_loading(target_idx, message)
            return bool(select_fn(target_idx))

        if input_pending_fn is None and exit_fn is not None and exit_fn():
            return -6
        if (
            live_fn is None
            and len(options) == 0
            and not return_query_on_enter
            and tab_fn is None
            and not structured
        ):
            result = -4
            return result

        _needs_redraw = True
        key_queue: list[str] = []

        def loading_key(key):
            nonlocal loading_transition
            global _needs_redraw
            selected_index = filt[sel] if filt and 0 <= sel < len(filt) and eligible(filt[sel]) else -1
            handled = key_fn(key, selected_index) if key_fn is not None else False
            if handled is not KeyResult.CONSUMED and handled:
                loading_transition = True
            _needs_redraw = True
            return handled

        if input_pending_fn is not None:
            from .transition import LoadingInput

            loading_input = LoadingInput(tty_fd, on_key=loading_key)

        def discard_width_guard_input():
            key_queue.clear()
            try:
                termios.tcflush(tty_fd, termios.TCIFLUSH)
            except (OSError, termios.error):
                pass

        while True:
            clock_minute = int(time.time() // 60)
            if clock_minute != last_clock_minute:
                last_clock_minute = clock_minute
                _needs_redraw = True

            try:
                terminal_size = os.get_terminal_size(tty_fd)
            except OSError:
                terminal_size = None
            if terminal_size != last_terminal_size:
                last_terminal_size = terminal_size
                _needs_redraw = True
            too_narrow = terminal_size is not None and terminal_size.columns < MIN_TUI_COLUMNS
            if width_notice_active and not too_narrow:
                discard_width_guard_input()
                _needs_redraw = True

            loading_active = loading_input is not None and (
                input_pending_fn() or loading_input.has_pending_sequence
            )
            if loading_active:
                action = loading_input.poll()
                if action == "quit":
                    if not return_on_quit:
                        raise KeyboardInterrupt
                    result = -5
                    break
                if action == "cancel":
                    handled = loading_key("ESC")
                    if handled is not KeyResult.CONSUMED and not handled:
                        result = -2
                        break
            # LoadingInput bounds each poll. Leave any remaining burst for the
            # next tick before accepting data or acknowledging a destination.
            loading_input_waiting = loading_active and (
                loading_input.has_pending_sequence
                or bool(select.select([tty_fd], [], [], 0)[0])
            )
            partial_input = loading_input_waiting
            if loading_transition and not partial_input:
                result = -6
                break

            if live_fn is not None and not partial_input:
                new_opts, new_hdr, _done = live_fn(query)
                was_done = live_done
                live_done = bool(_done)
                if state is None and (new_opts or live_done) and initial_query and query == initial_query:
                    query = ""
                    cursor_pos = 0
                new_keys = option_keys(new_opts)
                opts_changed = new_opts != options or new_keys != keys
                filt_invalid = bool(
                    filt and any(oi >= len(new_opts) or oi < 0 for oi in filt)
                )
                if opts_changed or filt_invalid:
                    prev_selected = (
                        keys[filt[sel]]
                        if filt
                        and 0 <= sel < len(filt)
                        and 0 <= filt[sel] < len(options)
                        else None
                    )
                    options = list(new_opts)
                    keys = new_keys
                    filt = filt_list()
                    sel = restored_selection(prev_selected, sel)
                    _needs_redraw = True
                elif not was_done and live_done:
                    filt = filt_list()
                    sel = max(0, min(sel, len(filt) - 1)) if filt else 0
                    _needs_redraw = True
                if cur_header != new_hdr:
                    cur_header = new_hdr
                    _needs_redraw = True

                if exit_fn is not None and exit_fn():
                    result = -6
                    break
                auto_select = (auto_select_single_when_done() if callable(auto_select_single_when_done)
                               else auto_select_single_when_done)
                # Local filtering must not turn a multi-result search into auto-entry.
                selectable = [index for index in range(len(options)) if eligible(index)]
                if not too_narrow and _done and auto_select and len(selectable) == 1 and selectable[0] in filt:
                    target_idx = selectable[0]
                    sel = filt.index(target_idx)
                    if not _can_select(target_idx):
                        _needs_redraw = True
                        render(filt)
                    else:
                        render(filt)
                        time.sleep(0.05)
                        result = target_idx
                        break

                if not too_narrow and _done and len(options) == 0 and not structured:
                    result = -4
                    break

            if not partial_input and exit_fn is not None and exit_fn():
                result = -6
                break
            if not loading_active and input_pending_fn is not None and input_pending_fn():
                continue
            sel = max(0, min(sel, len(filt) - 1)) if filt else 0
            if filt and not eligible(filt[sel]):
                sel = first_selectable(filt)
            marked_indices.intersection_update({index for index in marked_indices if eligible(index)})
            scroll = max(0, min(scroll, max(0, len(filt) - 1)))

            now = time.time()
            if not waiting_for_results() and last_live_feedback:
                live_feedback.reset(input_bindings)
            current_live_feedback = live_feedback.message("") if waiting_for_results() else ""
            if current_live_feedback != last_live_feedback:
                last_live_feedback = current_live_feedback
                _needs_redraw = True
            cursor_active, blink_phase = cursor_activity.phase(now, filtering=bool(query))
            cur_phase = (cursor_active, blink_phase)
            if cur_phase != last_cursor_phase:
                last_cursor_phase = cur_phase
                _needs_redraw = True

            if tick_fn is not None and tick_fn() and now - last_poster_tick >= 0.1:
                last_poster_tick = now
                _needs_redraw = True
            elif flags.show_image and top_header_fn is not None and filt:
                sel_idx = filt[sel] if sel < len(filt) else -1
                if 0 <= sel_idx < len(options):
                    # Poster status is set by top_header_fn during render()
                    # (ui.hovered_show_obj is None until the first render)
                    if _poster_needs_tick(ui.hovered_show_obj):
                        # Redraw every frame while poster is loading for smooth spinner
                        _needs_redraw = True
            elif header_fn is not None and now - last_poster_tick >= 0.1:
                show_ctx = getattr(ui, "ui_show_ctx", None) or getattr(
                    ui, "hovered_show_obj", None
                )
                if show_ctx and isinstance(show_ctx, dict):
                    fb_time = float(show_ctx.get("_action_feedback_time") or 0)
                    if fb_time > 0 and (now - fb_time) <= 3.0:
                        last_poster_tick = now
                        _needs_redraw = True

            if callable(hints) and filt and (now - last_hint_poll_time) >= 0.5:
                last_hint_poll_time = now
                sel_oi = filt[sel] if 0 <= sel < len(filt) else -1
                if 0 <= sel_oi < len(options):
                    cur_h = str(hints(sel_oi) or "")
                    if last_rendered_hint_val is not None and cur_h != last_rendered_hint_val:
                        _needs_redraw = True

            if width_notice_active and not too_narrow:
                render(filt)
                _needs_redraw = False

            # A destination must drain loading keys before paint; acknowledgement
            # may release the caller's pending state only after that paint.
            # Paint the first ready frame before the idle keyboard wait too.
            if (frame_ack_pending or first_frame_pending) and not partial_input:
                render(filt)
                _needs_redraw = False
            if loading_active:
                if _needs_redraw:
                    render(filt)
                    _needs_redraw = False
                select.select([tty_fd], [], [], 0.05)
                continue

            if key_queue:
                key = key_queue.pop(0)
            else:
                try:
                    ready = select.select([tty_fd], [], [], 0.05)[0]
                except InterruptedError:
                    continue

                if not ready:
                    if _needs_redraw:
                        render(filt)
                        _needs_redraw = False
                    continue

                key = _get_key(tty_fd)
            if return_on_quit and key in ("", "EOF", "CTRL_C"):
                result = -5
                break
            _needs_redraw = True
            # Recheck after input wait: resizing and queued keys may arrive
            # together. Never replay notice input into the restored picker.
            try:
                input_too_narrow = os.get_terminal_size(tty_fd).columns < MIN_TUI_COLUMNS
            except OSError:
                input_too_narrow = too_narrow
            if width_notice_active and not input_too_narrow:
                discard_width_guard_input()
                render(filt)
                _needs_redraw = False
                continue
            if input_too_narrow:
                if not width_notice_active:
                    render(filt)
                if key == "CTRL_C":
                    raise KeyboardInterrupt
                if key == "ESC" or (key == "LEFT" and not return_query_on_enter):
                    if pending_delete_index is not None:
                        pending_delete_index = None
                    elif show_help:
                        show_help = False
                    else:
                        result = -2 if key == "ESC" else -3
                        break
                continue
            if not query:
                cursor_activity.quiet()
            old_cursor_pos = cursor_pos
            old_query = query

            if show_help:
                if key in ("?", "ESC", "q", "ENTER"):
                    show_help = False
                    _persistent_rendered_lines.clear()
                    last_poster_key = None
                    _needs_redraw = True
                elif key == "CTRL_C":
                    raise KeyboardInterrupt
                continue

            if pending_delete_index is not None:
                if confirmation_index is not None:
                    if key == "CTRL_C":
                        raise KeyboardInterrupt
                    result = confirmation_index if key in ("y", "Y") else -2
                    break
                if key in ("y", "Y"):
                    if delete_fn:
                        res = delete_fn(pending_delete_index)
                        if res:
                            options, cur_header = list(res[0] or []), res[1]
                            keys = option_keys(options)
                            filt = filt_list()
                            sel = max(0, min(sel, len(filt) - 1)) if filt else 0
                    pending_delete_index = None
                    continue
                pending_delete_index = None
                if key in ("n", "N", "ESC", "CTRL_C"):
                    if key == "CTRL_C":
                        raise KeyboardInterrupt
                    continue

            selected_index = filt[sel] if filt and 0 <= sel < len(filt) and eligible(filt[sel]) else -1
            if adjust_fn is not None and not return_query_on_enter and selected_index >= 0:
                direction = adjustment_direction(key, query, cursor_pos)
                if direction is not None and adjust_fn(selected_index, direction):
                    result = -6
                    break
            if key_fn is not None:
                handled = key_fn(key, selected_index)
                if handled is KeyResult.CONSUMED:
                    continue
                if handled:
                    result = -6
                    break
            if waiting_for_results():
                action = input_bindings.resolve("SPACE" if key == " " else key)
                editing_cursor = bool(query) and key in ("HOME", "END", "RIGHT")
                if action is not None and action.action.id in ("select", "move") and not editing_cursor:
                    if live_feedback.on_key(key, messages=result_wait_messages(action.action.id)):
                        _needs_redraw = True
                        continue
            if key == "UP":
                boundary_hint = ""

                boundary_action = ""
                if filt:
                    sel = move_selection(filt, sel, -1 if not reverse_items else 1)
                elif query_history:
                    history_idx = min(history_idx + 1, len(query_history) - 1)
                    if history_idx >= 0:
                        query = query_history[history_idx]
                        cursor_pos = len(query)
                        filt = filt_list()
            elif key == "DOWN":
                boundary_hint = ""

                boundary_action = ""
                if filt:
                    sel = move_selection(filt, sel, 1 if not reverse_items else -1)
                elif query_history:
                    history_idx = max(history_idx - 1, -1)
                    if history_idx >= 0:
                        query = query_history[history_idx]
                    else:
                        query = ""
                    cursor_pos = len(query)
                    filt = filt_list()
            elif key in ("HOME", "END", "PAGE_UP", "PAGE_DOWN"):
                boundary_hint = ""
                boundary_action = ""
                if query and key in ("HOME", "END"):
                    cursor_pos = 0 if key == "HOME" else len(query)
                    _needs_redraw = True
                else:
                    toward_end = (key in ("HOME", "PAGE_UP")) == bool(reverse_items)
                    sel = jump_selection(filt, sel, toward_end, page=key in ("PAGE_UP", "PAGE_DOWN"))
            elif key == "ENTER":
                boundary_hint = ""

                boundary_action = ""
                if live_fn is not None and not live_done and not filt:
                    continue
                if return_query_on_enter:
                    result = query
                    break
                if multi_select:
                    if marked_indices:
                        targets = sorted(index for index in marked_indices if eligible(index))
                    elif filt and sel < len(filt) and eligible(filt[sel]):
                        targets = [filt[sel]]
                    else:
                        targets = []
                    if not all(_can_select(index) for index in targets):
                        _needs_redraw = True
                        render(filt)
                        continue
                    result = targets
                    break
                if not filt or not (0 <= sel < len(filt)):
                    continue
                if not eligible(filt[sel]):
                    continue
                if not _can_select(filt[sel]):
                    _needs_redraw = True
                    render(filt)
                    continue
                result = filt[sel]
                break
            elif key == "RIGHT":
                if return_query_on_enter:
                    if cursor_pos < len(query):
                        cursor_pos += 1
                        _needs_redraw = True
                elif query:
                    if cursor_pos < len(query):
                        cursor_pos += 1
                        boundary_hint = ""
                        boundary_action = ""
                        _needs_redraw = True
                    else:
                        has_valid_selection = (
                            bool(filt)
                            and 0 <= sel < len(filt)
                            and eligible(filt[sel])
                            and not (live_fn is not None and not live_done and not filt)
                        )
                        if has_valid_selection:
                            now = time.time()
                            if boundary_action == "SELECT" and now - boundary_hint_time < 1.5:
                                boundary_hint = ""
                                boundary_action = ""
                                if not _can_select(filt[sel]):
                                    _needs_redraw = True
                                    render(filt)
                                else:
                                    result = filt[sel]
                                    break
                            else:
                                boundary_action = "SELECT"
                                boundary_hint = "Press Right Arrow again or Enter to select."
                                boundary_hint_time = now
                                _needs_redraw = True
                else:
                    boundary_hint = ""
                    boundary_action = ""
                    if live_fn is not None and not live_done and not filt:
                        continue
                    if not filt or not (0 <= sel < len(filt)):
                        continue
                    if not eligible(filt[sel]):
                        continue
                    if not _can_select(filt[sel]):
                        _needs_redraw = True
                        render(filt)
                        continue
                    result = filt[sel]
                    break

            elif key == "?" and help_dict:
                boundary_hint = ""

                boundary_action = ""
                show_help = True
                _persistent_rendered_lines.clear()
                last_poster_key = None
            elif key == "ALT_D":
                from media_cli.media.download_worker import get_global_worker_if_started, has_tracked_downloads
                from media_cli.ui.download_manager import run_download_manager
                if not has_tracked_downloads():
                    boundary_hint = "No downloads yet"
                    boundary_hint_time = time.time()
                    _needs_redraw = True
                    continue
                try:
                    w = get_global_worker_if_started()
                    if w is None:
                        boundary_hint = "No downloads yet"
                        boundary_hint_time = time.time()
                        _needs_redraw = True
                        continue
                    act = run_download_manager(w, standalone=False)
                finally:
                    runtime.ensure_raw()
                    termios.tcflush(tty_fd, termios.TCIFLUSH)

                if act == "QUIT":
                    result = -2
                    break
                elif act == "FINISH_QUIT":
                    try:
                        act = run_download_manager(w, standalone=False, auto_quit=True)
                    finally:
                        runtime.ensure_raw()
                        termios.tcflush(tty_fd, termios.TCIFLUSH)
                    if act == "QUIT":
                        result = -2
                        break
                    _persistent_rendered_lines.clear()
                    last_poster_key = None
                    _needs_redraw = True
                    render(filt)
                    continue

                _persistent_rendered_lines.clear()
                last_poster_key = None
                _needs_redraw = True
                render(filt)
            elif key == "ESC":
                boundary_hint = ""

                boundary_action = ""
                result = -2
                break
            elif key == "CTRL_C":
                from media_cli.media.download_worker import has_active_downloads
                act_c, q_c = has_active_downloads()
                if act_c > 0 or q_c > 0:
                    from media_cli.ui.download_manager import prompt_download_exit_cli, run_download_manager
                    from media_cli.media.download_worker import get_global_worker, stop_global_worker
                    try:
                        act = prompt_download_exit_cli(act_c, q_c, tty_fd=tty_fd)
                    finally:
                        runtime.ensure_raw()
                        termios.tcflush(tty_fd, termios.TCIFLUSH)
                    if act == "STOP_QUIT":
                        stop_global_worker()
                        raise KeyboardInterrupt
                    elif act == "FINISH_QUIT":
                        try:
                            w = get_global_worker()
                            act = run_download_manager(w, standalone=False, auto_quit=True)
                        finally:
                            runtime.ensure_raw()
                            termios.tcflush(tty_fd, termios.TCIFLUSH)
                        if act == "QUIT":
                            stop_global_worker()
                            raise KeyboardInterrupt
                        _persistent_rendered_lines.clear()
                        last_poster_key = None
                        _needs_redraw = True
                        continue
                    else:  # CANCEL
                        _persistent_rendered_lines.clear()
                        last_poster_key = None
                        _needs_redraw = True
                        continue
                raise KeyboardInterrupt
            elif key == "LEFT":
                if return_query_on_enter:
                    if cursor_pos > 0:
                        cursor_pos -= 1
                        _needs_redraw = True
                elif query:
                    if cursor_pos > 0:
                        cursor_pos -= 1
                        boundary_hint = ""
                        boundary_action = ""
                        _needs_redraw = True
                else:
                    result = -3
                    break

            elif key == "BACKSPACE":
                boundary_hint = ""

                boundary_action = ""
                if cursor_pos > 0:
                    query = query[: cursor_pos - 1] + query[cursor_pos:]
                    cursor_pos -= 1
                    filt = filt_list()
                    sel = first_selectable(filt)
                    scroll = 0

            elif key == "CTRL_U":
                boundary_hint = ""

                boundary_action = ""
                query = ""
                filt = filt_list()
                sel = first_selectable(filt)
                scroll = 0
                cursor_pos = 0
            elif key in ("TAB", "CTRL_N"):
                boundary_hint = ""

                boundary_action = ""
                if multi_select:
                    if filt and sel < len(filt):
                        oi = filt[sel]
                        if eligible(oi):
                            if oi in marked_indices:
                                marked_indices.remove(oi)
                            else:
                                marked_indices.add(oi)
                    sel = move_selection(filt, sel, 1 if not reverse_items else -1)
                    _needs_redraw = True
                elif tab_fn:
                    selected = filt[sel] if filt and sel < len(filt) else None
                    try:
                        res = tab_fn(selected, direction=1)
                    except TypeError:
                        res = tab_fn(selected)
                    if res:
                        options, cur_header = list(res[0] or []), res[1]
                        keys = option_keys(options)
                        if len(res) > 2:
                            disabled_indices.clear()
                            disabled_indices.update(res[2] or ())
                        filt = filt_list()
                        sel = first_selectable(filt)
                        _persistent_rendered_lines.clear()
                        last_poster_key = None
                        _needs_redraw = True

            elif key in ("SHIFT_TAB", "CTRL_P"):
                boundary_hint = ""

                boundary_action = ""
                if tab_fn:
                    selected = filt[sel] if filt and sel < len(filt) else None
                    try:
                        res = tab_fn(selected, direction=-1)
                    except TypeError:
                        res = tab_fn(selected)
                    if res:
                        options, cur_header = list(res[0] or []), res[1]
                        keys = option_keys(options)
                        if len(res) > 2:
                            disabled_indices.clear()
                            disabled_indices.update(res[2] or ())
                        filt = filt_list()
                        sel = first_selectable(filt)
                        _persistent_rendered_lines.clear()
                        last_poster_key = None
                        _needs_redraw = True
            elif key in ("CTRL_R", "ALT_R", "F5"):
                boundary_hint = ""

                boundary_action = ""
                fn = reverse_fn if key == "CTRL_R" else alt_reverse_fn or reverse_fn
                if fn:
                    selected = filt[sel] if filt and sel < len(filt) else None
                    previous_key = keys[selected] if selected is not None else None
                    res = fn(selected)
                    if res:
                        options, cur_header = list(res[0] or []), res[1]
                        keys = option_keys(options)
                        if len(res) > 2:
                            disabled_indices.clear()
                            disabled_indices.update(res[2] or ())
                        filt = filt_list()
                        sel = restored_selection(previous_key, sel)
                        _persistent_rendered_lines.clear()
                        last_poster_key = None
                        _needs_redraw = True
            elif key in ("DELETE", "CTRL_D"):
                boundary_hint = ""

                boundary_action = ""
                if is_search and query_history:
                    if 0 <= history_idx < len(query_history):
                        to_del = query_history[history_idx]
                        if delete_fn:
                            delete_fn(to_del)
                        query_history.pop(history_idx)
                        if history_idx >= len(query_history):
                            history_idx = len(query_history) - 1
                        query = query_history[history_idx] if history_idx >= 0 else ""
                        cursor_pos = len(query)
                        filt = filt_list()
                        _needs_redraw = True
                elif delete_fn and filt and 0 <= sel < len(filt):
                    idx_to_del = filt[sel]
                    if can_delete_fn is None or can_delete_fn(idx_to_del):
                        pending_delete_index = idx_to_del
            elif key == "CTRL_O":
                boundary_hint = ""

                boundary_action = ""
                if (
                    info_fn is not None
                    and filt
                    and sel < len(filt)
                    and eligible(filt[sel])
                ):
                    info_fn(filt[sel])
                    last_poster_key = None
                    _persistent_rendered_lines.clear()
                    _needs_redraw = True
            elif key == " " and multi_select:
                boundary_hint = ""

                boundary_action = ""
                if filt and sel < len(filt):
                    oi = filt[sel]
                    if eligible(oi):
                        if oi in marked_indices:
                            marked_indices.remove(oi)
                        else:
                            marked_indices.add(oi)
                        _needs_redraw = True
            elif key != "UNKNOWN":
                boundary_hint = ""

                boundary_action = ""
                if len(key) == 1 and key.isprintable():
                    query = query[:cursor_pos] + key + query[cursor_pos:]
                    cursor_pos += 1
                    filt = filt_list()
                    sel = first_selectable(filt)
                    scroll = 0

            if cursor_pos != old_cursor_pos or query != old_query:
                cursor_activity.edit(time.time())

            # Render immediately if no more keys are buffered in the terminal.
            # This gives responsive feedback on single presses AND during held-key
            # scrolling (renders after each burst drains, not just after release).
            if _needs_redraw and not key_queue:
                try:
                    more_input = select.select([tty_fd], [], [], 0)[0]
                except Exception:
                    more_input = []
                if not more_input:
                    render(filt)
                    _needs_redraw = False

    except (EOFError, KeyboardInterrupt):
        if not return_on_quit:
            raise
        result = -5
    finally:
        position = state if state is not None else PickerState()
        if position is not None:
            position.query = query
            position.query_cursor = cursor_pos
            position.cursor_activity = cursor_activity
            # sel is a filtered rank, never an option index. Do not call adapter
            # hooks here: a transition may already have activated another view.
            current_filt = [index for index in filt if 0 <= index < len(options)]
            selected_index = filt[sel] if filt and 0 <= sel < len(filt) else 0
            selectable = [index for index in current_filt if eligible(index)]
            position.selected_index = min(
                selectable, key=lambda index: abs(index - selected_index), default=0
            )
            position.scroll = max(0, min(scroll, max(0, len(current_filt) - 1)))
            position.marked_indices = {index for index in marked_indices if eligible(index)}
        if not structured:
            ui._last_picker_position = (prompt_key, position)
        try:
            tty_file.write(b"\033]112\007\033[0 q\033[?25l")
            tty_file.flush()
        except Exception:
            pass

        # Cursor restoration is handled by the global atexit / finally block in
        # app.py to prevent cursor flickering during rapid screen transitions.
        try:
            tty_file.close()
        except Exception:
            pass
        reporting.set_status_sink(old_status_sink)

    return result


# Structured sessions own backend fallback and call this implementation directly.
# Keep legacy callers on their signature-preserving compatibility dispatcher.
terminal_pick = tui_pick
tui_pick = terminal_application(dispatch_legacy_picker(terminal_pick))


def _poster_needs_tick(show_obj) -> bool:
    """Return True if the poster for *show_obj* may need a frame tick.

    This is a thin re-export of the PosterManager helper so that ``tui_pick``
    does not need to import PosterManager directly.  It is wired up by
    ``app.py`` after the PosterManager instance is created.
    """
    # Default: never tick (overridden by app.py via _set_poster_tick_fn)
    return False


_poster_tick_fn: Callable | None = None


def _set_poster_tick_fn(fn: Callable) -> None:
    """Register the PosterManager.needs_tick function for use inside tui_pick."""
    global _poster_needs_tick, _poster_tick_fn
    _poster_tick_fn = fn

    def _poster_needs_tick(show_obj) -> bool:  # type: ignore[misc]
        return bool(_poster_tick_fn and _poster_tick_fn(show_obj))
