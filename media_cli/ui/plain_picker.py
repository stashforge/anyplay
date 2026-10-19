"""Plain text / Non-TUI Picker Engine.

Provides sequential, numbered line-by-line terminal interaction without
raw mode, alternate screens, or cursor manipulations.
"""

from __future__ import annotations

import os
import re
import select
import shutil
import sys
from collections.abc import Sequence
from typing import Any, Callable
from weakref import WeakKeyDictionary

from .picker_render import match
from .picker_state import PickerState
from .row_layout import DEFAULT_ROW_INDENTATION, RowIndentation, RowKind

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
# Partial pipe lines must survive adapter restarts without retaining dead streams.
_pipe_input_buffers = WeakKeyDictionary()


def strip_ansi(value: Any) -> str:
    """Strip ANSI escape sequences from text for clean plain-text output."""
    return ANSI_RE.sub("", str(value or ""))


def _emit(text: str, output_fn: Callable[..., None] = print) -> None:
    """Emit output, stripping ANSI escapes if stdout is not a TTY."""
    if not sys.stdout.isatty():
        output_fn(strip_ansi(text))
    else:
        output_fn(text)


SPINNER_CHARS = set("⣾⣽⣻⢿⡿⣟⣯⣷⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")


def _clean_header_lines(hdr: str) -> list[str]:
    """Clean multi-line header, stripping TUI-specific navigation footers and spinners."""
    cleaned_lines = []
    for raw_line in str(hdr or "").splitlines():
        plain = strip_ansi(raw_line).strip()
        if not plain:
            continue
        # Filter out lines containing spinner animations or background status
        if any(c in plain for c in SPINNER_CHARS):
            continue
        if "Checking for new episodes" in plain or "Loading..." in plain or "Enriching metadata" in plain:
            continue
        # Filter out TUI-specific keybinding footers
        if any(marker in plain for marker in (
            "Left=search", "Esc=quit", "Esc=Back", "Left/Esc=back",
            "Esc=back", "Esc=search", "Esc=cancel", "Esc=Cancel",
            "Tab=Sub/Dub • Enter=", "Ctrl+R=flip", "?=Help"
        )):
            continue
        cleaned_lines.append(raw_line.strip())
    return cleaned_lines


def plain_pick(
    flags: Any,
    ui: Any,
    prompt: Any,
    options: Sequence[str] | list[str],
    header: str = "",
    hints: Any = None,
    live_fn: Callable[[str], tuple[list[str], str, bool]] | None = None,
    header_fn: Callable[[int], str] | None = None,
    top_header_fn: Callable[[int], str] | None = None,
    tab_fn: Callable[..., Any] | None = None,
    reverse_fn: Callable[..., Any] | None = None,
    delete_fn: Callable[..., Any] | None = None,
    delete_prompt: Any = None,
    can_delete_fn: Callable[[int], bool] | None = None,
    info_fn: Callable[[int], Any] | None = None,
    multi_select: bool = False,
    is_search: bool = False,
    query_history: Sequence[str] | None = None,
    help_dict: dict[str, str] | None = None,
    hide_separator: bool = False,
    cursor_pos_override: Any = None,
    count_total: Any = None,
    return_query_on_enter: bool = False,
    initial_query: str = "",
    auto_select_single_when_done: bool | Callable[[], bool] = False,
    tick_fn: Callable[[], bool] | None = None,
    keep_cursor_hidden_on_select: bool = False,
    select_fn: Callable[[int], bool] | None = None,
    select_loading_message_fn: Callable[..., str] | None = None,
    item_prefix_fn: Callable[[int], str] | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[..., None] = print,
    disabled_indices=None,
    heading_indices=None,
    state: PickerState | None = None,
    return_on_quit: bool = False,
    structured: bool = False,
    action_labels: Sequence[str] | None = None,
    filter_fn: Callable[[str, Sequence[str]], list[int]] | None = None,
    key_fn: Callable[[str, int], bool] | None = None,
    exit_fn: Callable[[], bool] | None = None,
    row_indentation: RowIndentation = DEFAULT_ROW_INDENTATION,
    **kwargs: Any,
) -> int | list[int] | str:
    """Numbered line-by-line picker compatible with tui_pick contract.

    State and callback indexes refer to original options, not printed numbers.
    Headings and disabled actions are unnumbered. Structured headers bypass
    legacy footer cleaning; action_labels=None keeps the legacy action hints.
    return_on_quit returns -5 for quit/EOF/interrupt instead of exiting.
    filter_fn returns ordered option indexes. key_fn(command, focused_index)
    and exit_fn() return true to request a transition (-6).
    Structured selectable stdin polls live_fn/exit_fn while idle; updates
    that change the view return through exit_fn so the adapter can reopen it.
    Default pipe input uses the same bytewise reader in both modes, avoiding
    text-buffer read-ahead when switching between legacy and structured views.
    """

    if not isinstance(row_indentation, RowIndentation):
        raise TypeError("row_indentation requires RowIndentation")
    if state is not None:
        if not isinstance(state, PickerState):
            raise TypeError("state must be a PickerState")
        state.validate()

    current_options = list(options or [])
    disabled_indices = set(disabled_indices or ())
    heading_indices = set(heading_indices or ())
    query = state.query if state is not None else initial_query
    selected_index = state.selected_index if state is not None else 0
    marked_indices = set(state.marked_indices) if state is not None else set()
    visible = []
    input_fd = None
    terminal_input = False
    if input_fn is input and (structured or not sys.stdin.isatty()):
        try:
            descriptor = sys.stdin.fileno()
            select.select([descriptor], [], [], 0)
        except (AttributeError, OSError, TypeError, ValueError):
            pass
        else:
            input_fd = descriptor
            terminal_input = sys.stdin.isatty()
    poll_input = structured and input_fd is not None

    def eligible(index):
        return 0 <= index < len(current_options) and index not in disabled_indices and index not in heading_indices

    def visible_indices():
        if filter_fn is not None:
            return list(dict.fromkeys(
                index for index in filter_fn(query, current_options) if 0 <= index < len(current_options)
            ))
        if state is None or not query:
            return list(range(len(current_options)))
        scored = []
        for index, option in enumerate(current_options):
            score = match(query, strip_ansi(option))
            if score is not None:
                scored.append((score[0], index))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [index for _, index in scored]

    def persist(refresh_filter=True):
        nonlocal selected_index, visible
        if refresh_filter:
            visible = visible_indices()
        selectable = [index for index in visible if eligible(index)]
        if selected_index not in selectable:
            selected_index = min(selectable, key=lambda index: abs(index - selected_index), default=0)
        marked_indices.intersection_update({index for index in marked_indices if eligible(index)})
        if state is not None:
            state.query = query
            state.selected_index = selected_index
            state.scroll = 0  # Plain output has no scrolling viewport.
            state.marked_indices = set(marked_indices)

    def finish(result):
        persist(refresh_filter=result != -6)
        return result

    def can_select(index):
        return eligible(index) and (select_fn is None or bool(select_fn(index)))

    def quit_picker():
        persist()
        if return_on_quit:
            return -5
        sys.exit(0)

    def read_input(prompt):
        if input_fd is None:
            return input_fn(prompt)
        if poll_input:
            _emit(prompt, output_fn)
        else:
            sys.stdout.write(prompt)
        sys.stdout.flush()
        pending = None if terminal_input else _pipe_input_buffers.setdefault(sys.stdin, bytearray())

        def completed_line():
            line = bytes(pending).decode(sys.stdin.encoding or "utf-8", sys.stdin.errors or "strict")
            pending.clear()
            return line

        while True:
            # The structured adapter keeps this invocation's rows immutable;
            # live_fn drains queued updates and exit_fn requests a fresh view.
            if poll_input and live_fn is not None:
                live_fn(query)
            if poll_input and exit_fn is not None and exit_fn():
                return None
            try:
                ready = select.select([input_fd], [], [], 0.1)[0] if poll_input else [input_fd]
            except InterruptedError:
                continue
            if ready:
                if terminal_input:
                    return input_fn("")
                # A readable pipe may hold only part of a line. Read one byte
                # so later lines stay available to the next picker invocation.
                char = os.read(input_fd, 1)
                if not char:
                    if pending:
                        return completed_line()
                    raise EOFError
                if char == b"\n":
                    return completed_line()
                pending.extend(char)

    persist()
    if exit_fn is not None and exit_fn():
        return finish(-6)
    # Prompt string resolution
    prompt_text = str(prompt() if callable(prompt) else prompt)

    # 1. Search Query Input Mode (when return_query_on_enter=True)
    if return_query_on_enter:
        default = str(query or "")
        suffix = f" [{default}]" if default else ""
        _emit(f"\n\033[1m{prompt_text}\033[0m", output_fn)
        try:
            entered = read_input(f"Query{suffix}: ")
        except (EOFError, KeyboardInterrupt):
            return finish(-5 if return_on_quit else -2)
        if entered is None:
            return finish(-6)
        entered = entered.strip()
        if key_fn is not None and key_fn(entered, selected_index if eligible(selected_index) else -1):
            return finish(-6)
        if return_on_quit and entered.lower() in ("q", "quit", "exit"):
            return quit_picker()
        query = entered or default
        return finish(query)

    # 2. Handle Live Search / Dynamic Options
    current_header = header
    live_done = live_fn is None

    if live_fn is not None:
        try:
            live_opts, live_hdr, live_done = live_fn(query)
            if live_opts or live_done:
                current_options = list(live_opts)
            if live_hdr:
                current_header = live_hdr
        except Exception:
            live_done = True
        if exit_fn is not None and exit_fn():
            return finish(-6)

        # If options are empty and live_fn indicates it's still loading (e.g. async search)
        if not current_options and not live_done and not poll_input:
            import time
            start_wait = time.time()
            printed_searching = False
            while not live_done and (time.time() - start_wait < 15.0):
                if not printed_searching:
                    _emit("Searching for results...", output_fn)
                    printed_searching = True
                time.sleep(0.08)
                try:
                    live_opts, live_hdr, live_done = live_fn(query)
                    if live_opts or live_done:
                        current_options = list(live_opts)
                    if live_hdr:
                        current_header = live_hdr
                    if live_done or current_options:
                        break
                except Exception:
                    break
                if exit_fn is not None and exit_fn():
                    return finish(-6)

    if exit_fn is not None and exit_fn():
        return finish(-6)

    # Auto-select single option if requested and done
    auto_select = (auto_select_single_when_done() if callable(auto_select_single_when_done)
                   else auto_select_single_when_done)
    selectable = [index for index in range(len(current_options)) if eligible(index)]
    if auto_select and live_done and len(selectable) == 1 and selectable[0] in visible_indices():
        selected_index = selectable[0]
        if can_select(selected_index):
            _emit(
                f"\n{prompt_text}: Automatically selecting only result -> {strip_ansi(current_options[selected_index])}",
                output_fn,
            )
            return finish(selected_index)

    show_options = True

    while True:
        persist()
        if exit_fn is not None and exit_fn():
            return finish(-6)
        visible = visible_indices()
        numbered_indices = [index for index in visible if eligible(index)]
        number_by_index = {index: number for number, index in enumerate(numbered_indices, 1)}
        # Resolve prompt & header dynamically
        prompt_text = str(prompt() if callable(prompt) else prompt)

        hdr_to_show = current_header
        # Header section: only single-show screens render their metadata card
        # before a row is selected.
        if not hdr_to_show and header_fn is not None and top_header_fn is None:
            try:
                hdr_eval = header_fn(0)
                if hdr_eval:
                    hdr_to_show = hdr_eval
            except Exception:
                pass

        # Render options list on initial pass, tab switch, or explicit request
        if show_options:
            if hdr_to_show:
                cleaned_hdr = str(hdr_to_show).splitlines() if structured else _clean_header_lines(hdr_to_show)
                if cleaned_hdr:
                    _emit("", output_fn)
                    for hl in cleaned_hdr:
                        _emit(hl, output_fn)

            # Section Prompt
            _emit(f"\n\033[1;36m=== {prompt_text} ===\033[0m", output_fn)

            if not current_options:
                _emit("(No selectable options)", output_fn)
                if live_fn is None and not structured:
                    return finish(-4)

            # Print Numbered Options with Hints (formatted as Python comments: # hint)
            clean_opts = [strip_ansi(opt) for opt in current_options]
            has_any_hints = bool(hints)
            max_opt_len = max((len(o) for o in clean_opts), default=0) if has_any_hints else 0

            indent_limit = max(0, shutil.get_terminal_size().columns - 3)
            for idx in visible:
                opt, clean_opt = current_options[idx], clean_opts[idx]
                kind = RowKind.HEADING if idx in heading_indices else RowKind.OPTION if eligible(idx) else RowKind.DISABLED
                indent_width = min(row_indentation.columns_for(kind), indent_limit)
                indent = " " * indent_width
                if idx in heading_indices:
                    _emit(indent + clean_opt, output_fn)
                    continue
                if not eligible(idx):
                    _emit(f"{indent}  {clean_opt} (disabled)", output_fn)
                    continue
                number = number_by_index[idx]
                hint_str = ""
                if hints is not None:
                    if isinstance(hints, dict):
                        hint_str = hints.get(opt) or hints.get(clean_opt) or ""
                    elif isinstance(hints, (list, tuple)) and 0 <= idx < len(hints):
                        hint_str = str(hints[idx] or "")
                    elif callable(hints):
                        hint_str = str(hints(idx) or "")

                clean_hint = strip_ansi(hint_str).strip()
                prefix = item_prefix_fn(idx) if item_prefix_fn else ""
                if clean_hint:
                    if clean_hint.startswith("(") and clean_hint.endswith(")"):
                        clean_hint = clean_hint[1:-1].strip()
                    pad = " " * (max(0, max_opt_len - len(clean_opt)) + 2) if max_opt_len <= 35 else "  "
                    _emit(
                        f"{indent}\033[1;34m[{number}]\033[0m {prefix}{clean_opt}{pad}\033[38;5;244m# {clean_hint}\033[0m",
                        output_fn,
                    )
                else:
                    _emit(f"{indent}\033[1;34m[{number}]\033[0m {prefix}{clean_opt}", output_fn)

            # Help / Available Actions Line (with gap separating from results)
            actions = ["[1-N] Select", "[q] Quit", "[b] Back"]
            if tab_fn is not None:
                actions.append("[t] Next Tab")
            if reverse_fn is not None:
                actions.append("[r] Refresh / Flip")
            if multi_select:
                actions[0] = "[1,2,3 or 1-4] Multi-select"
            if action_labels is not None:
                actions = list(action_labels)

            if actions:
                _emit("", output_fn)
                _emit(f"\033[38;5;244mActions: {', '.join(actions)}\033[0m", output_fn)
            show_options = False

        try:
            choice_str = read_input("\nEnter choice: ")
        except (EOFError, KeyboardInterrupt):
            return quit_picker()
        if choice_str is None:
            return finish(-6)
        choice_str = choice_str.strip()

        if not choice_str:
            continue

        lower = choice_str.lower()
        if key_fn is not None and key_fn(choice_str, selected_index if eligible(selected_index) else -1):
            return finish(-6)

        # Handle Quit
        if lower in ("q", "quit", "exit"):
            return quit_picker()

        # Handle Back / Search
        if lower in ("b", "back", "<", "left", "esc"):
            return finish(-3)

        # Handle Help / Re-display options
        if lower in ("ls", "list", "?", "help"):
            show_options = True
            continue

        # Handle Tab Cycling (e.g. Sub/Dub, History tabs)
        if lower in ("t", "tab") and tab_fn is not None:
            res = tab_fn(None, direction=1)
            if res and res[0] is not None:
                current_options = list(res[0])
                if len(res) > 1 and res[1]:
                    current_header = str(res[1])
                if len(res) > 2:
                    disabled_indices = set(res[2] or ())
            show_options = True
            continue

        # Handle Refresh / Reverse (reverse_fn)
        if lower in ("r", "refresh", "flip") and reverse_fn is not None:
            res = reverse_fn(None)
            if res and res[0] is not None:
                current_options = list(res[0])
                if len(res) > 1 and res[1]:
                    current_header = str(res[1])
                if len(res) > 2:
                    disabled_indices = set(res[2] or ())
            show_options = True
            continue

        # Handle Delete (e.g. History deletion: "del 2" or "d 2")
        if (lower.startswith("del ") or lower.startswith("d ")) and delete_fn is not None:
            parts = choice_str.split(None, 1)
            if len(parts) == 2 and parts[1].isdigit():
                target_number = int(parts[1])
                if 1 <= target_number <= len(numbered_indices):
                    target_idx = numbered_indices[target_number - 1]
                    if can_delete_fn is not None and not can_delete_fn(target_idx):
                        output_fn("Cannot delete this item.")
                        continue
                    res = delete_fn(target_idx)
                    if res and res[0] is not None:
                        current_options = list(res[0])
                        if len(res) > 1 and res[1]:
                            current_header = str(res[1])
                        if len(res) > 2:
                            disabled_indices = set(res[2] or ())
                    show_options = True
                    continue

        # Handle Multi-Selection Mode
        if multi_select:
            selected_indices: list[int] = []
            tokens = re.split(r"[\s,]+", choice_str)
            valid = True
            for token in tokens:
                if not token:
                    continue
                if "-" in token:
                    m = re.match(r"^(\d+)-(\d+)$", token)
                    if m:
                        start, end = int(m.group(1)), int(m.group(2))
                        if not (1 <= min(start, end) <= max(start, end) <= len(numbered_indices)):
                            valid = False
                            break
                        selected_indices.extend(numbered_indices[n - 1] for n in range(min(start, end), max(start, end) + 1))
                    else:
                        valid = False
                        break
                elif token.isdigit():
                    n = int(token)
                    if 1 <= n <= len(numbered_indices):
                        selected_indices.append(numbered_indices[n - 1])
                    else:
                        valid = False
                        break
                else:
                    valid = False
                    break
            if valid and selected_indices:
                selected_indices = sorted(set(selected_indices))
                if all(can_select(index) for index in selected_indices):
                    selected_index = selected_indices[0]
                    marked_indices = set(selected_indices)
                    return finish(selected_indices)
                _emit("Unable to select these items.", output_fn)
                continue
            else:
                _emit(
                    f"\033[31mInvalid selection '{choice_str}'. Use numbers like 1,2 or 1-4.\033[0m",
                    output_fn,
                )
                continue

        # Handle Single Option Selection
        if choice_str.isdigit():
            chosen_num = int(choice_str)
            if 1 <= chosen_num <= len(numbered_indices):
                chosen_idx = numbered_indices[chosen_num - 1]
                selected_index = chosen_idx
                persist()
                if not can_select(chosen_idx):
                    _emit(f"\033[31mUnable to select item {chosen_num}.\033[0m", output_fn)
                    continue
                return finish(chosen_idx)
            else:
                _emit(
                    f"\033[31mChoice out of range (1-{len(numbered_indices)}).\033[0m",
                    output_fn,
                )
                continue

        _emit(
            f"\033[31mUnknown option '{choice_str}'. Type [1-{len(numbered_indices)}], 'ls' to show list, 'q' to quit, or 'b' for back.\033[0m",
            output_fn,
        )
