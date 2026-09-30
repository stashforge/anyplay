"""Line-oriented fallback for environments without a usable TTY."""

import re

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def strip_ansi(value):
    return ANSI_RE.sub("", str(value or ""))


def fallback_pick(
        prompt,
        options,
        *,
        return_query_on_enter=False,
        initial_query="",
        input_fn=input,
        output_fn=print):
    prompt_text = str(prompt() if callable(prompt) else prompt)
    output_fn(f"\n\033[1m{prompt_text}\033[0m")

    if return_query_on_enter:
        default = str(initial_query or "")
        suffix = f" [{default}]" if default else ""
        try:
            entered = input_fn(f"Query{suffix}: ")
        except (EOFError, KeyboardInterrupt):
            return -2
        entered = str(entered or "").strip()
        return entered or default

    for index, option in enumerate(options):
        output_fn(
            f"  \033[1;36m[{index + 1}]\033[0m {strip_ansi(option)}"
        )
    if not options:
        output_fn("No selectable options.")
        return -2

    while True:
        try:
            entered = input_fn(f"\nChoose (1-{len(options)}): ").strip()
        except (EOFError, KeyboardInterrupt):
            return -2
        try:
            selected = int(entered)
        except ValueError:
            continue
        if 1 <= selected <= len(options):
            return selected - 1
