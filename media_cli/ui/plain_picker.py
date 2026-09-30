"""Plain text / Non-TUI Picker Engine.

Provides sequential, numbered line-by-line terminal interaction without
raw mode, alternate screens, or cursor manipulations.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from typing import Any, Callable

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


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
    auto_select_single_when_done: bool = False,
    tick_fn: Callable[[], bool] | None = None,
    keep_cursor_hidden_on_select: bool = False,
    select_fn: Callable[[int], bool] | None = None,
    item_prefix_fn: Callable[[int], str] | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[..., None] = print,
    **kwargs: Any,
) -> int | list[int] | str:
    """Numbered line-by-line picker compatible with tui_pick contract."""

    # Prompt string resolution
    prompt_text = str(prompt() if callable(prompt) else prompt)

    # 1. Search Query Input Mode (when return_query_on_enter=True)
    if return_query_on_enter:
        default = str(initial_query or "")
        suffix = f" [{default}]" if default else ""
        _emit(f"\n\033[1m{prompt_text}\033[0m", output_fn)
        try:
            entered = input_fn(f"Query{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            return -2
        return entered or default

    # 2. Handle Live Search / Dynamic Options
    current_options = list(options or [])
    current_header = header

    if live_fn is not None:
        try:
            live_opts, live_hdr, live_done = live_fn(initial_query)
            if live_opts:
                current_options = list(live_opts)
            if live_hdr:
                current_header = live_hdr
        except Exception:
            live_done = True

        # If options are empty and live_fn indicates it's still loading (e.g. async search)
        if not current_options and not live_done:
            import time
            start_wait = time.time()
            printed_searching = False
            while not live_done and (time.time() - start_wait < 15.0):
                if not printed_searching:
                    _emit("Searching for results...", output_fn)
                    printed_searching = True
                time.sleep(0.08)
                try:
                    live_opts, live_hdr, live_done = live_fn(initial_query)
                    if live_opts:
                        current_options = list(live_opts)
                    if live_hdr:
                        current_header = live_hdr
                    if live_done or current_options:
                        break
                except Exception:
                    break

    # Auto-select single option if requested and done
    if auto_select_single_when_done and len(current_options) == 1:
        if select_fn is None or select_fn(0):
            _emit(
                f"\n{prompt_text}: Automatically selecting only result -> {strip_ansi(current_options[0])}",
                output_fn,
            )
            return 0

    show_options = True

    while True:
        # Resolve prompt & header dynamically
        prompt_text = str(prompt() if callable(prompt) else prompt)

        hdr_to_show = current_header
        # In multi-show list pickers (top_header_fn is provided), header_fn(0) renders
        # hover details for the 1st anime in the list. In plain mode, no anime is selected
        # yet, so we do not display premature item 0 details.
        # Only in single-show screens (top_header_fn is None, e.g. details, episode list)
        # do we evaluate header_fn to display the selected show's metadata card.
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
                cleaned_hdr = _clean_header_lines(hdr_to_show)
                if cleaned_hdr:
                    _emit("", output_fn)
                    for hl in cleaned_hdr:
                        _emit(hl, output_fn)

            # Section Prompt
            _emit(f"\n\033[1;36m=== {prompt_text} ===\033[0m", output_fn)

            if not current_options:
                _emit("(No selectable options)", output_fn)
                if live_fn is None:
                    return -4

            # Print Numbered Options with Hints (formatted as Python comments: # hint)
            clean_opts = [strip_ansi(opt) for opt in current_options]
            has_any_hints = bool(hints)
            max_opt_len = max((len(o) for o in clean_opts), default=0) if has_any_hints else 0

            for idx, (opt, clean_opt) in enumerate(zip(current_options, clean_opts)):
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
                        f"\033[1;34m[{idx + 1}]\033[0m {prefix}{clean_opt}{pad}\033[38;5;244m# {clean_hint}\033[0m",
                        output_fn,
                    )
                else:
                    _emit(f"\033[1;34m[{idx + 1}]\033[0m {prefix}{clean_opt}", output_fn)

            # Help / Available Actions Line (with gap separating from results)
            actions = ["[1-N] Select", "[q] Quit", "[b] Back"]
            if tab_fn is not None:
                actions.append("[t] Next Tab")
            if reverse_fn is not None:
                actions.append("[r] Refresh / Flip")
            if multi_select:
                actions[0] = "[1,2,3 or 1-4] Multi-select"

            _emit("", output_fn)
            _emit(f"\033[38;5;244mActions: {', '.join(actions)}\033[0m", output_fn)
            show_options = False

        try:
            choice_str = input_fn("\nEnter choice: ").strip()
        except (EOFError, KeyboardInterrupt):
            sys.exit(0)

        if not choice_str:
            continue

        lower = choice_str.lower()

        # Handle Quit
        if lower in ("q", "quit", "exit"):
            sys.exit(0)

        # Handle Back / Search
        if lower in ("b", "back", "<", "left", "esc"):
            return -3

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
            show_options = True
            continue

        # Handle Refresh / Reverse (reverse_fn)
        if lower in ("r", "refresh", "flip") and reverse_fn is not None:
            res = reverse_fn(None)
            if res and res[0] is not None:
                current_options = list(res[0])
                if len(res) > 1 and res[1]:
                    current_header = str(res[1])
            show_options = True
            continue

        # Handle Delete (e.g. History deletion: "del 2" or "d 2")
        if (lower.startswith("del ") or lower.startswith("d ")) and delete_fn is not None:
            parts = choice_str.split(None, 1)
            if len(parts) == 2 and parts[1].isdigit():
                target_idx = int(parts[1]) - 1
                if 0 <= target_idx < len(current_options):
                    if can_delete_fn is not None and not can_delete_fn(target_idx):
                        output_fn("Cannot delete this item.")
                        continue
                    res = delete_fn(target_idx)
                    if res and res[0] is not None:
                        current_options = list(res[0])
                        if len(res) > 1 and res[1]:
                            current_header = str(res[1])
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
                        for n in range(min(start, end), max(start, end) + 1):
                            if 1 <= n <= len(current_options):
                                selected_indices.append(n - 1)
                    else:
                        valid = False
                        break
                elif token.isdigit():
                    n = int(token)
                    if 1 <= n <= len(current_options):
                        selected_indices.append(n - 1)
                    else:
                        valid = False
                        break
                else:
                    valid = False
                    break
            if valid and selected_indices:
                return sorted(set(selected_indices))
            else:
                _emit(
                    f"\033[31mInvalid selection '{choice_str}'. Use numbers like 1,2 or 1-4.\033[0m",
                    output_fn,
                )
                continue

        # Handle Single Option Selection
        if choice_str.isdigit():
            chosen_num = int(choice_str)
            if 1 <= chosen_num <= len(current_options):
                chosen_idx = chosen_num - 1
                if select_fn is not None:
                    try:
                        if not select_fn(chosen_idx):
                            _emit(
                                f"\033[31mUnable to select item {chosen_num}.\033[0m",
                                output_fn,
                            )
                            continue
                    except Exception:
                        pass
                return chosen_idx
            else:
                _emit(
                    f"\033[31mChoice out of range (1-{len(current_options)}).\033[0m",
                    output_fn,
                )
                continue

        _emit(
            f"\033[31mUnknown option '{choice_str}'. Type [1-{len(current_options)}], 'ls' to show list, 'q' to quit, or 'b' for back.\033[0m",
            output_fn,
        )
