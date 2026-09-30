"""
Bottom-anchored TUI picker for allmanga-cli.

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
import os
import select
import signal
import sys
import termios
import time
import tty
from typing import TYPE_CHECKING, Callable

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
    strip_ansi as _strip_ansi,
)
from ..core.terminal import (
    truncate_display as _truncate_display,
)
from . import terminal_images
from .covers import (
    poster_symbol_lines as _poster_symbol_lines,
)
from .covers import (
    poster_uses_native_protocol as _poster_uses_native_protocol,
)
from .picker_render import (
    get_key as _get_key,
)
from .picker_render import (
    match as _match,
)
from .picker_render import (
    render_item as _render_item,
)

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


def invalidate_screen_buffer() -> None:
    global _persistent_rendered_lines
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
    auto_select_single_when_done: bool = False,
    keep_cursor_hidden_on_select: bool = False,
    count_total=None,
    disabled_indices=None,
    reverse_items=True,
    tick_fn=None,
    multi_select: bool = False,
    select_fn=None,
    initial_selected: int = 0,
    item_prefix_fn=None,
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

    Returns
    -------
    int | str
        * ``≥0``  — index of the selected option.
        * ``-2``  — Esc pressed.
        * ``-3``  — Left arrow pressed.
        * ``-4``  — No options (live search finished with empty results).
        * ``str`` — when *return_query_on_enter* is True.
    """
    global _needs_redraw

    if getattr(flags, "plain_mode", False) or not sys.stdin.isatty():
        from .plain_picker import plain_pick

        return plain_pick(
            flags,
            ui,
            prompt,
            options,
            header=header,
            hints=hints,
            live_fn=live_fn,
            header_fn=header_fn,
            top_header_fn=top_header_fn,
            tab_fn=tab_fn,
            reverse_fn=reverse_fn,
            delete_fn=delete_fn,
            delete_prompt=delete_prompt,
            can_delete_fn=can_delete_fn,
            right_fn=right_fn,
            info_fn=info_fn,
            multi_select=multi_select,
            is_search=is_search,
            query_history=query_history,
            help_dict=help_dict,
            hide_separator=hide_separator,
            count_total=count_total,
            return_query_on_enter=return_query_on_enter,
            initial_query=initial_query,
            auto_select_single_when_done=auto_select_single_when_done,
            tick_fn=tick_fn,
            keep_cursor_hidden_on_select=keep_cursor_hidden_on_select,
            select_fn=select_fn,
            disabled_indices=disabled_indices,
            item_prefix_fn=item_prefix_fn,
        )

    def current_prompt() -> str:
        return str(prompt() if callable(prompt) else prompt)

    def fallback_pick():
        from .plain_picker import plain_pick

        return plain_pick(
            flags,
            ui,
            prompt,
            options,
            header=header,
            hints=hints,
            live_fn=live_fn,
            header_fn=header_fn,
            top_header_fn=top_header_fn,
            tab_fn=tab_fn,
            reverse_fn=reverse_fn,
            delete_fn=delete_fn,
            delete_prompt=delete_prompt,
            can_delete_fn=can_delete_fn,
            right_fn=right_fn,
            info_fn=info_fn,
            multi_select=multi_select,
            is_search=is_search,
            query_history=query_history,
            help_dict=help_dict,
            hide_separator=hide_separator,
            count_total=count_total,
            return_query_on_enter=return_query_on_enter,
            initial_query=initial_query,
            auto_select_single_when_done=auto_select_single_when_done,
            tick_fn=tick_fn,
            keep_cursor_hidden_on_select=keep_cursor_hidden_on_select,
            select_fn=select_fn,
            disabled_indices=disabled_indices,
            item_prefix_fn=item_prefix_fn,
        )

    tty_fd = -1
    tty_file = None
    try:
        tty_fd = os.open("/dev/tty", os.O_RDWR)
        tty_file = os.fdopen(tty_fd, "rb+", buffering=0)
        tty_fd = tty_file.fileno()
    except OSError:
        if tty_file:
            try:
                tty_file.close()
            except Exception:
                pass
            tty_file = None
        if tty_fd >= 0:
            os.close(tty_fd)
        return fallback_pick()

    try:
        old_attrs = termios.tcgetattr(tty_fd)
    except Exception:
        try:
            tty_file.close()
        except Exception:
            pass
        return fallback_pick()

    # Enter alt screen (only sends escape sequence if not already active)
    try:
        from . import display as _display

        _display.enter_alt_screen()
    except Exception:
        sys.stdout.write("\033[?1049h\033[2J\033[?25l")
        sys.stdout.flush()

    options = list(options or [])
    query = initial_query
    cursor_pos = len(query)
    sel = 0
    scroll = 0
    history_idx = -1
    cur_header = header_fn(0) if header_fn else header
    show_help = False
    last_poster_tick = 0
    last_poster_key = None
    last_clock_minute = int(time.time() // 60)
    last_hint_poll_time = 0.0
    last_rendered_hint_val = None
    pending_delete_index = None
    boundary_hint = ""
    boundary_hint_time = 0.0
    boundary_action = ""
    picker_start_time = time.time()
    last_cursor_activity_time = picker_start_time
    last_typing_time = last_cursor_activity_time
    last_cursor_phase = (True, True)

    def _is_cursor_blink_on(now: float) -> tuple[bool, bool]:
        active = (
            return_query_on_enter
            or bool(query)
            or (now - picker_start_time < 3.0)
            or (now - last_cursor_activity_time < 3.0)
        )
        if not active:
            return False, False

        idle = now - last_cursor_activity_time
        # Solid ON while moving or typing, and for 0.7s after stopping
        if idle < 0.7:
            return True, True

        # Natural 1Hz blink: 500ms OFF, 500ms ON
        blink_on = (int((idle - 0.7) * 2.0) % 2 == 1)
        return True, blink_on
    disabled_indices = set(disabled_indices or ())
    marked_indices: set[int] = set()

    try:
        last_terminal_size = os.get_terminal_size(tty_fd)
    except OSError:
        last_terminal_size = None

    def filt_list():
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
            if option_index not in disabled_indices:
                return pos
        return 0

    def move_selection(filt, current, delta):
        if not filt:
            return 0
        selectable = [
            pos
            for pos, option_index in enumerate(filt)
            if option_index not in disabled_indices
        ]
        if not selectable:
            return 0
        if current not in selectable:
            return selectable[0]
        current_pos = selectable.index(current)
        return selectable[(current_pos + delta) % len(selectable)]

    def render(filt):
        global _persistent_rendered_lines
        nonlocal scroll, last_poster_key, sel, last_rendered_hint_val

        if filt and any(oi >= len(options) or oi < 0 for oi in filt):
            filt = filt_list()
            sel = max(0, min(sel, len(filt) - 1)) if filt else 0

        try:
            sz = os.get_terminal_size(tty_fd)
            rows, cols = sz.lines, sz.columns
        except OSError:
            rows, cols = 24, 80

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
        if header_fn is not None:
            sel_idx = filt[sel] if (filt and sel < len(filt)) else -1
            # ← was: globals()["_active_picker_query"] = query
            ui.active_picker_query = query
            actual_header = header_fn(sel_idx)
        header_lines = actual_header.splitlines() if actual_header else []
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
        elif boundary_hint and time.time() - boundary_hint_time < 1.5:
            hint_line = f"\033[38;5;222m* {boundary_hint}\033[0m"
            if header_lines:
                header_lines[-1] = hint_line
            else:
                header_lines = [hint_line]
        header_n = (
            max(4, len(header_lines)) if header_fn is not None else len(header_lines)
        )

        # ← was: show_img = globals().get("SHOW_IMAGE", False)
        show_img = flags.show_image

        is_tmx = os.environ.get("PREFIX", "").startswith(
            "/data/data/com.termux"
        ) or os.path.exists("/data/data/com.termux")
        top_header_n = (
            (9 if is_tmx else 8) if (show_img and top_header_fn is not None) else 0
        )
        top_margin = 1 if top_header_n > 0 else 0

        GAP = 0
        fixed = top_margin + 1 + header_n + top_header_n + GAP

        if rows - fixed < 10 and top_header_n > 0:
            top_header_n = 0
            top_margin = 0
            GAP = 0
            fixed = 1 + header_n

        max_vis, _, _ = _picker_vertical_layout(
            rows, header_n, top_header_n, top_margin, GAP, len(filt)
        )

        if filt:
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
                # ← was: globals().get("_hovered_show_id")
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
            disabled = oi in disabled_indices
            prefix = item_prefix_fn(oi) if item_prefix_fn else ""
            prefix_w = _display_width(prefix)
            eff_max_w = max(10, item_max_w - prefix_w)
            if disabled:
                label = _truncate_display(
                    str(options[oi] or ""), max(0, cols - 2 - prefix_w)
                )
                out.append(f"\033[2K{_fit_terminal_line(f'  {prefix}{label}', cols)}")
                continue
            ptr = f"{_C_PTR}\u276f{_RST}" if is_sel and not disabled else " "
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
                        - _display_width(ptr)
                        - 2
                        - _display_width(label)
                        - 2,
                    )
                    if hint_w > 0:
                        hint = f"  {_C_HINT}{_truncate_display(hint_text, hint_w)}{_RST}"
            out.append(f"\033[2K{_fit_terminal_line(f'{ptr} {label}{hint}', cols)}")

        # Rendered Block Cursor: Teal block background (\033[48;2;137;220;235;38;2;30;30;46m)
        _C_BLOCK_BG = "\033[48;2;137;220;235;38;2;30;30;46m"
        now = time.time()
        cursor_active, blink_on = _is_cursor_blink_on(now)

        if cursor_pos < len(query):
            before = query[:cursor_pos]
            ch = query[cursor_pos]
            after = query[cursor_pos + 1 :]
            if cursor_active and blink_on:
                query_disp = (
                    f"{_C_QUERY}{before}{_C_BLOCK_BG}{ch}{_RST}{_C_QUERY}{after}{_RST}"
                )
            else:
                query_disp = f"{_C_QUERY}{before}{ch}{after}{_RST}"
        else:
            if cursor_active and blink_on:
                query_disp = f"{_C_QUERY}{query}{_RST}{_C_BLOCK_BG} {_RST}"
            else:
                query_disp = f"{_C_QUERY}{query}{_RST} "

        pstr = f"{_C_PROMPT}{current_prompt()} \u276f{_RST} {query_disp}"

        if hide_separator or len(options) == 0:
            out.append(f"\033[2K{_fit_terminal_line(pstr, cols)}")
        else:
            total_count = count_total() if callable(count_total) else count_total
            total_count = len(options) if total_count is None else total_count
            filtered_count = (
                sum(1 for option_index in filt if option_index not in disabled_indices)
                if disabled_indices
                else len(filt)
            )
            if multi_select and marked_indices:
                cstr = f"\033[38;2;166;227;161m{len(marked_indices)} selected\033[0m {_C_COUNT}| {filtered_count}/{total_count}{_RST}"
            else:
                cstr = f"{_C_COUNT}{filtered_count}/{total_count}{_RST}"

            hidden_below = scroll
            hidden_above = max(0, len(filt) - scroll - max_vis)
            si = ""
            if hidden_above > 0:
                si += f"\u25b2{hidden_above} "
            if hidden_below > 0:
                si += f"\u25bc{hidden_below} "
            if si:
                si = f" {_C_SCROLLI}{si}{_RST}"
            left_str = f"{pstr}  {cstr}{si}"
            used = _display_width(left_str)
            sep_w = max(1, cols - used - 2)
            sep_char = "\u2500"
            sep = f" {_C_HINT}{sep_char * sep_w}{_RST}"
            out.append(f"\033[2K{_fit_terminal_line(f'{pstr}  {cstr}{si}{sep}', cols)}")

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
        _persistent_rendered_lines = list(out)

        if frame or clear_prefix or overlay:
            # Park hidden hardware cursor at bottom-right so it never renders under graphics
            buf = f"{clear_prefix}\033[?25l{frame}{overlay}\033[{rows};{cols}H\033[?25l"
            tty_file.write(buf.encode())
            tty_file.flush()

    # -----------------------------------------------------------------------
    # Main event loop
    # -----------------------------------------------------------------------
    from ..core import reporting

    old_status_sink = reporting._status_sink
    reporting.set_status_sink(lambda msg, color: True)
    old_stderr = sys.stderr
    devnull_stderr = open(os.devnull, "w")
    sys.stderr = devnull_stderr
    try:
        init_clear = "\033[2J" if not _persistent_rendered_lines else ""
        tty_file.write(
            (terminal_images.clear_if_active() + init_clear + "\033[?25l").encode()
        )
        tty_file.flush()
        tty.setraw(tty_fd)
        termios.tcflush(tty_fd, termios.TCIFLUSH)

        filt = filt_list()
        sel = first_selectable(filt)
        if (
            initial_selected > 0
            and filt
            and 0 <= initial_selected < len(filt)
            and filt[initial_selected] not in disabled_indices
        ):
            sel = initial_selected
        result = -2

        def _can_select(target_idx):
            if select_fn is None:
                return True
            termios.tcsetattr(tty_fd, termios.TCSADRAIN, old_attrs)
            try:
                ok = select_fn(target_idx)
            finally:
                tty.setraw(tty_fd)
                termios.tcflush(tty_fd, termios.TCIFLUSH)
            return bool(ok)

        if (
            live_fn is None
            and len(options) == 0
            and not return_query_on_enter
            and tab_fn is None
        ):
            result = -4
            return result

        _needs_redraw = True
        live_done = live_fn is None
        key_queue: list[str] = []

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

            if live_fn is not None:
                new_opts, new_hdr, _done = live_fn(query)
                was_done = live_done
                live_done = bool(_done)
                if (new_opts or live_done) and initial_query and query == initial_query:
                    query = ""
                    cursor_pos = 0
                opts_changed = new_opts != options
                filt_invalid = bool(
                    filt and any(oi >= len(new_opts) or oi < 0 for oi in filt)
                )
                if opts_changed or filt_invalid:
                    prev_selected = (
                        options[filt[sel]]
                        if filt
                        and 0 <= sel < len(filt)
                        and 0 <= filt[sel] < len(options)
                        else None
                    )
                    options = list(new_opts)
                    filt = filt_list()
                    if prev_selected is not None:
                        for i, oi in enumerate(filt):
                            if 0 <= oi < len(options) and options[oi] == prev_selected:
                                sel = i
                                break
                        else:
                            sel = max(0, min(sel, len(filt) - 1)) if filt else 0
                    else:
                        sel = max(0, min(sel, len(filt) - 1)) if filt else 0
                    _needs_redraw = True
                elif not was_done and live_done:
                    filt = filt_list()
                    sel = max(0, min(sel, len(filt) - 1)) if filt else 0
                    _needs_redraw = True
                if cur_header != new_hdr:
                    cur_header = new_hdr
                    _needs_redraw = True

                if _done and auto_select_single_when_done and len(options) == 1:
                    if not _can_select(filt[0]):
                        _needs_redraw = True
                        render(filt)
                        continue
                    render(filt)
                    time.sleep(0.05)
                    result = 0
                    break

                if _done and len(options) == 0:
                    result = -4
                    break

            sel = max(0, min(sel, len(filt) - 1)) if filt else 0
            if filt and filt[sel] in disabled_indices:
                sel = first_selectable(filt)

            now = time.time()
            cursor_active, blink_phase = _is_cursor_blink_on(now)
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
            _needs_redraw = True
            last_cursor_activity_time = time.time()
            last_typing_time = last_cursor_activity_time
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
                if key in ("y", "Y"):
                    if delete_fn:
                        res = delete_fn(pending_delete_index)
                        if res:
                            options, cur_header = list(res[0] or []), res[1]
                            filt = filt_list()
                            sel = max(0, min(sel, len(filt) - 1)) if filt else 0
                    pending_delete_index = None
                    continue
                pending_delete_index = None
                if key in ("n", "N", "ESC", "CTRL_C"):
                    if key == "CTRL_C":
                        raise KeyboardInterrupt
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
            elif key in ("HOME", "PAGE_UP"):
                boundary_hint = ""

                boundary_action = ""
                if query:
                    cursor_pos = 0
                    _needs_redraw = True
                else:
                    sel = first_selectable(filt)

                    scroll = 0
            elif key in ("END", "PAGE_DOWN"):
                boundary_hint = ""

                boundary_action = ""
                if query:
                    cursor_pos = len(query)
                    _needs_redraw = True
                else:
                    sel = first_selectable(list(reversed(filt)))
                    if filt:
                        sel = len(filt) - 1 - sel
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
                        result = sorted(marked_indices)
                    elif filt and sel < len(filt) and filt[sel] not in disabled_indices:
                        result = [filt[sel]]
                    else:
                        result = []
                    break
                if not filt or not (0 <= sel < len(filt)):
                    continue
                if filt[sel] in disabled_indices:
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
                            and filt[sel] not in disabled_indices
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
                                boundary_hint = "Press ► again to select"
                                boundary_hint_time = now
                                _needs_redraw = True
                else:
                    boundary_hint = ""
                    boundary_action = ""
                    if live_fn is not None and not live_done and not filt:
                        continue
                    if not filt or not (0 <= sel < len(filt)):
                        continue
                    if filt[sel] in disabled_indices:
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
                _needs_redraw = True
            elif key == "ESC":
                boundary_hint = ""

                boundary_action = ""
                result = -2
                break
            elif key == "CTRL_C":
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
                        now = time.time()
                        if boundary_action == "BACK" and now - boundary_hint_time < 1.5:
                            result = -3
                            break
                        else:
                            boundary_action = "BACK"
                            boundary_hint = "Press ◄ again to go back"
                            boundary_hint_time = now
                            _needs_redraw = True
                else:
                    result = -3
                    break

            elif key == "BACKSPACE":
                boundary_hint = ""

                boundary_action = ""
                last_typing_time = time.time()
                if cursor_pos > 0:
                    query = query[: cursor_pos - 1] + query[cursor_pos:]
                    cursor_pos -= 1
                    filt = filt_list()
                    sel = first_selectable(filt)
                    scroll = 0

            elif key == "CTRL_U":
                boundary_hint = ""

                boundary_action = ""
                last_typing_time = time.time()
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
                        if oi not in disabled_indices:
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
                        if len(res) > 2:
                            disabled_indices.clear()
                            disabled_indices.update(res[2] or ())
                        filt = filt_list()
                        sel = first_selectable(filt)
                        _persistent_rendered_lines.clear()
                        last_poster_key = None
                        _needs_redraw = True
            elif key == "CTRL_R":
                boundary_hint = ""

                boundary_action = ""
                if reverse_fn:
                    selected = filt[sel] if filt and sel < len(filt) else None
                    res = reverse_fn(selected)
                    if res:
                        options, cur_header = list(res[0] or []), res[1]
                        if len(res) > 2:
                            disabled_indices.clear()
                            disabled_indices.update(res[2] or ())
                        filt = filt_list()
                        sel = first_selectable(filt)
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
                    and filt[sel] not in disabled_indices
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
                    if oi not in disabled_indices:
                        if oi in marked_indices:
                            marked_indices.remove(oi)
                        else:
                            marked_indices.add(oi)
                        _needs_redraw = True
            elif key != "UNKNOWN":
                boundary_hint = ""

                boundary_action = ""
                if len(key) == 1 and key.isprintable():
                    last_typing_time = time.time()
                    query = query[:cursor_pos] + key + query[cursor_pos:]
                    cursor_pos += 1
                    filt = filt_list()
                    sel = first_selectable(filt)
                    scroll = 0

            if (
                cursor_pos != old_cursor_pos
                or query != old_query
                or key in (
                    "LEFT",
                    "RIGHT",
                    "HOME",
                    "END",
                    "PAGE_UP",
                    "PAGE_DOWN",
                    "BACKSPACE",
                    "CTRL_U",
                )
            ):
                last_cursor_activity_time = time.time()
                last_typing_time = last_cursor_activity_time

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

    finally:
        termios.tcsetattr(tty_fd, termios.TCSADRAIN, old_attrs)
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
        sys.stderr = old_stderr
        try:
            devnull_stderr.close()
        except Exception:
            pass

    return result


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

