"""Lightweight two-line transfer queue. No system metrics or download ownership."""

import os
import re
import textwrap
from dataclasses import dataclass

from ..core.terminal import display_width, sanitize_terminal_text, truncate_display
from ..state.theme import MINT_SCREEN_THEME
from ..torrents.ranking import decimal_size
from .styled_text import StyledText, TextRun, fit_styled_text

TABS = ("all", "active", "completed", "failed")
ACTIVE = {"downloading", "resolving", "queued", "paused"}
DONE = {"completed", "skipped"}
FAILED = {"failed", "cancelled"}


def text(value, role="secondary"):
    return StyledText((TextRun(sanitize_terminal_text(value), role),))


def spans(*parts):
    return StyledText(tuple(TextRun(value, role) for value, role in parts))


def duration(seconds):
    if seconds < 60:
        return f"{max(1, int(seconds))}s"
    if seconds < 3600:
        return f"{int(seconds) // 60}m"
    return f"{int(seconds) // 3600}h {(int(seconds) % 3600) // 60}m"


def speed_bytes(value):
    match = re.search(r"([\d.]+)\s*([kmgt]?i?b)", str(value), re.I)
    if not match:
        return 0.0
    factors = {
        "b": 1,
        "kb": 1000,
        "mb": 1000**2,
        "gb": 1000**3,
        "tb": 1000**4,
        "kib": 1024,
        "mib": 1024**2,
        "gib": 1024**3,
        "tib": 1024**4,
    }
    return float(match[1]) * factors.get(match[2].lower(), 1)


@dataclass(frozen=True)
class Transfer:
    id: str
    title: str
    status: str
    done: int
    total: int
    progress: float
    speed: float
    upload: float
    uploaded: int
    torrent: bool
    path: str
    engine: str
    error: str
    logs: tuple[str, ...]

    @property
    def role(self):
        return (
            "success"
            if self.status in DONE or self.status == "seeding"
            else "library.airing"
            if self.status in {"downloading", "resolving"}
            else "torrent.rose"
            if self.status in FAILED
            else "torrent.amber"
            if self.status == "paused"
            else "muted"
        )


def snapshots(worker, log_id=None):
    if worker is None:
        return ()
    with worker.lock:
        result = []
        for index, item in enumerate(worker.items):
            status = getattr(item.status, "value", str(item.status)).lower()
            path = str(getattr(item, "destination_path", ""))
            torrent = getattr(item, "engine", "") == "libtorrent" or bool(getattr(item, "torrent", {}))
            title = os.path.basename(path) if torrent and path else str(item.title)
            episode = str(getattr(item, "episode", ""))
            if not torrent and episode:
                title += " · " + episode
            result.append(
                Transfer(
                    str(getattr(item, "id", index)),
                    sanitize_terminal_text(title),
                    status,
                    int(getattr(item, "downloaded_bytes", 0)),
                    int(getattr(item, "total_bytes", 0)),
                    float(getattr(item, "progress", 0)),
                    speed_bytes(getattr(item, "speed_str", "")) if status == "downloading" else 0,
                    float(getattr(item, "upload_speed", 0)),
                    int(getattr(item, "uploaded_bytes", 0)),
                    torrent,
                    sanitize_terminal_text(path),
                    str(getattr(item, "engine", "auto")),
                    sanitize_terminal_text(getattr(item, "error", "")),
                    tuple(sanitize_terminal_text(line) for line in list(getattr(item, "logs", ()))[-2000:])
                    if str(getattr(item, "id", index)) == log_id
                    else (),
                )
            )
        return tuple(result)


def filtered(items, tab):
    if tab == "active":
        return tuple(item for item in items if item.status in ACTIVE)
    if tab == "completed":
        return tuple(item for item in items if item.status in DONE or item.status == "seeding")
    if tab == "failed":
        return tuple(item for item in items if item.status in FAILED)
    return items


def progress_bar(percent, width):
    filled = min(width, max(0, round(width * percent / 100)))
    return StyledText((TextRun("━" * filled, "success"), TextRun("─" * (width - filled), "muted")))


def fixed_size(value):
    """Reserve 999.9 and a two-cell unit, independent of changing magnitudes."""
    value = max(0, float(value))
    units = ("B", "KB", "MB", "GB", "TB", "PB", "EB")
    index = 0
    while value >= 999.95 and index < len(units) - 1:
        value /= 1000
        index += 1
    return f"{value:5.1f} {units[index]:<2}"


def transfer_progress(percent, done, total, width, *, speed=None, eta=None, indent=0, metrics=True):
    """Fill the row; numeric slots keep the bar's endpoint stable."""
    percent_text = f"{min(100, max(0, percent)):.1f}%".ljust(6)
    details = ""
    reserved = 0
    if width >= 38:
        reserved = 19  # 999.9 MB / 999.9 MB, even before total is known
        details = fixed_size(done).strip()
        if total:
            details += " / " + fixed_size(total).strip()
    if metrics and width >= 72:
        reserved += 14
        details += "  ↓ " + fixed_size(speed or 0).strip() + "/s"
    if metrics and width >= 90:
        reserved += 9
        details += "  " + (duration(eta) if eta is not None else "--")
    budget = max(1, width - indent - display_width(percent_text) - 1 - reserved)
    bar = progress_bar(percent, budget)
    return spans(
        (" " * indent, "secondary"),
        *((run.text, run.role) for run in bar.runs),
        (" " + percent_text, "secondary"),
        (details.rjust(reserved), "tab_inactive"),
    )


def queue_frame(
    items,
    *,
    tab="all",
    selected=None,
    scroll=0,
    width=80,
    height=24,
    speed=None,
    log_item=None,
    log_scroll=0,
    help_view=False,
    notice="",
):
    """Pure semantic rows. The curses adapter paints only changed rows."""
    width = max(1, width - 1)
    lines = [text("") for _ in range(max(1, height))]
    top = 0
    footer = max(top + 3, height - 2)

    def put(row, value):
        if 0 <= row < len(lines):
            lines[row] = fit_styled_text(value if isinstance(value, StyledText) else text(value), columns=width)

    suffix = " · Logs" if log_item else " · Keys" if help_view else ""
    from .screen_chrome import screen_header

    heading, divider = screen_header("Download Manager" + suffix, width=width)
    put(top, heading)
    put(top + 1, divider)
    put(footer, text("─" * width, "separator"))
    if help_view:
        groups = (
            "queue",
            "↑↓ / j k       Select download",
            "tab / shift+tab Switch category",
            "space / p       Pause or resume selected",
            "s               Stop selected download",
            "x               Remove queue entry (keep files)",
            "r               Retry failed download",
            "+ / -           Change download slots",
            "enter / l       Open full-screen logs",
            "esc / q         Back (q exits standalone manager)",
            "",
            "logs",
            "↑↓ / page up/down  Scroll messages",
            "home / end          First/latest messages",
            "esc / enter         Return to queue",
            "",
            "application",
            "ctrl+c             Quit with active-download choices",
        )
        for offset, line in enumerate(groups[: max(0, footer - top - 3)]):
            put(top + 3 + offset, text(line, "success" if line in {"queue", "logs", "application"} else "secondary"))
        put(footer + 1, text("esc back", "screen.key"))
        return tuple(lines), scroll
    if log_item:
        details = (
            log_item.title,
            f"{log_item.status} · {log_item.engine}",
            f"{decimal_size(log_item.done)} / {decimal_size(log_item.total)} · ↓ {decimal_size(log_item.speed)}/s",
            "Save: " + log_item.path,
        )
        for offset, line in enumerate(details):
            put(top + 3 + offset, text(line, "row_normal" if offset == 0 else "secondary"))
        start = top + 8
        log_lines = []
        for message in (*((log_item.error,) if log_item.error else ()), *log_item.logs):
            log_lines.extend(textwrap.wrap(message, max(1, width), replace_whitespace=False) or [""])
        capacity = max(0, footer - start)
        end = max(0, len(log_lines) - log_scroll)
        visible = log_lines[max(0, end - capacity) : end] if capacity else []
        for offset, line in enumerate(visible):
            put(start + offset, text(line, "secondary"))
        put(
            footer + 1,
            spans(("↑↓", "screen.key"), (" scroll   ", "secondary"), ("esc", "screen.key"), (" queue", "secondary")),
        )
        return tuple(lines), scroll
    from .navigation_tabs import NavigationTabs
    from .view_model import ViewTab

    number = len(filtered(items, tab))
    count = f"{number} download" + ("s" if number != 1 else "")
    count_width = max(display_width(count), display_width("999 downloads"))
    available = width - count_width - 3
    tabs = NavigationTabs(tuple(ViewTab(name, name.title()) for name in TABS), tab)
    labels = tabs.window_content(max(1, available if available >= 10 else width))
    runs = (
        ()
        if available < 10
        else (TextRun(count, "success"), TextRun(" " * (count_width - display_width(count)) + " · ", "muted"))
    )
    put(top + 2, StyledText(runs + labels.runs))
    active = sum(item.status in {"downloading", "resolving"} for item in items)
    queued = sum(item.status == "queued" for item in items)
    rate = sum(item.speed for item in items) if speed is None else speed
    remaining = [item for item in items if item.status in {"downloading", "resolving", "queued"}]
    unknown = any(not item.total for item in remaining)
    left = sum(max(0, item.total - item.done) for item in remaining if item.total)
    if not remaining:
        eta = "all done" if items and all(item.status in DONE or item.status == "seeding" for item in items) else ""
    elif rate > 0 and left:
        eta = ("active ETA ≈ " if unknown else "ETA ≈ ") + duration(left / rate)
    else:
        eta = "ETA estimating…"
    summary = f"↓ {decimal_size(rate)}/s"
    if any(item.torrent for item in items):
        summary += f" · ↑ {decimal_size(sum(item.upload for item in items))}/s"
    counts = f" · {active} active · {queued} queued" + (" · " + eta if eta else "")
    put(top + 4, spans((summary, "tab_inactive"), (counts, "library.value")))
    total = sum(item.total for item in items if item.status not in FAILED)
    done = sum(min(item.done, item.total) for item in items if item.total and item.status not in FAILED)
    percent = 100 * done / total if total else 0
    put(top + 5, transfer_progress(percent, done, total, width, metrics=False))
    shown = filtered(items, tab)
    start = top + 7
    capacity = max(1, (footer - start) // 3)
    index = next((i for i, item in enumerate(shown) if item.id == selected), 0)
    scroll = min(max(0, scroll), max(0, len(shown) - capacity))
    if index < scroll:
        scroll = index
    if index >= scroll + capacity:
        scroll = index - capacity + 1
    if not shown:
        put(start + 1, text("No downloads queued." if not items else "No downloads in this category.", "secondary"))
    for offset, item in enumerate(shown[scroll : scroll + capacity]):
        row = start + offset * 3
        label = item.status
        budget = max(1, width - display_width(label) - 5)
        title = truncate_display(item.title, budget)
        pad = max(1, width - display_width(title) - display_width(label) - 2)
        put(
            row,
            spans(
                ("❯ " if item.id == selected else "  ", "success"),
                (title, "row_normal"),
                (" " * pad, "secondary"),
                (label, item.role),
            ),
        )
        item_eta = max(0, item.total - item.done) / item.speed if item.speed and item.total else None
        put(
            row + 1,
            transfer_progress(item.progress, item.done, item.total, width, speed=item.speed, eta=item_eta, indent=2),
        )
    if notice:
        put(footer - 1, text(notice, "torrent.amber"))
    from .screen_chrome import shortcut_content

    put(
        footer + 1,
        shortcut_content(
            width, (("p", "pause/resume"), ("enter", "logs"), ("tab", "next"), ("?", "help"), ("esc", "back"))
        ),
    )
    return tuple(lines), scroll


def run_queue(stdscr, worker, *, backend, standalone=False, resize_pending=None, auto_quit=False):
    """Curses/input adapter; worker remains sole owner of download actions."""
    import curses
    import time
    from enum import Enum

    for operation in (
        ()
        if hasattr(stdscr, "render_frame")
        else (
            lambda: curses.raw(),
            lambda: curses.curs_set(0),
            lambda: curses.use_default_colors(),
            lambda: curses.set_escdelay(25),
        )
    ):
        try:
            operation()
        except (AttributeError, curses.error):
            pass
    stdscr.nodelay(True)
    stdscr.timeout(150)
    try:
        stdscr.leaveok(True)
    except (AttributeError, curses.error):
        pass
    pairs = {}

    def attribute(role):
        style = MINT_SCREEN_THEME.resolve_style(role)
        fg = style.foreground
        if isinstance(fg, Enum):
            fg = fg.value - 30 if 30 <= fg.value <= 37 else 7
        if isinstance(fg, tuple):
            fg = 16 + 36 * round(fg[0] / 51) + 6 * round(fg[1] / 51) + round(fg[2] / 51)
        if fg is None:
            fg = -1
        if getattr(curses, "COLORS", 0) < 256 and fg >= 8:
            fg = 2 if role in {"success", "screen.brand", "screen.key", "tab_active"} else 7
        if fg not in pairs:
            pair = len(pairs) + 1
            try:
                curses.init_pair(pair, fg, -1)
                pairs[fg] = curses.color_pair(pair)
            except curses.error:
                pairs[fg] = 0
        return pairs[fg] | (curses.A_BOLD if style.bold else 0)

    saved_view = getattr(worker, "_queue_view_state", {}) if worker is not None else {}
    tab = saved_view.get("tab", "all")
    if tab not in TABS:
        tab = "all"
    selected = saved_view.get("selected")
    scroll = saved_view.get("scroll", 0)
    memories = dict(saved_view.get("memories", {}))
    log_id = None
    log_scroll = 0
    help_view = False
    previous = None
    previous_size = None
    smooth = None
    last_sample = time.monotonic()
    notice = ""
    notice_owner = None
    notice_time = 0.0
    remove_id = None
    if auto_quit and worker and hasattr(worker, "resume_paused_items"):
        worker.resume_paused_items()
    while True:
        if resize_pending and resize_pending.get("value"):
            resize_pending["value"] = False
            if not hasattr(stdscr, "render_frame"):
                backend._handle_resize_event(stdscr)
            previous = None
        height, width = backend._current_terminal_size(stdscr)
        items = snapshots(worker, log_id)
        active = sum(item.status in {"downloading", "resolving"} for item in items)
        pending = sum(item.status == "queued" for item in items)
        paused = sum(item.status == "paused" for item in items)
        if auto_quit and not active and not pending and not paused:
            if any(item.status in FAILED for item in items):
                auto_quit = False
                notice = "Auto-quit paused: a download failed or stopped."
            else:
                return "QUIT"
        shown = filtered(items, tab)
        if selected not in {item.id for item in shown}:
            selected = shown[0].id if shown else None
        if worker is not None:
            memories[tab] = (selected, scroll)
            worker._queue_view_state = dict(tab=tab, selected=selected, scroll=scroll, memories=dict(memories))
        now = time.monotonic()
        if notice_owner is not None and (notice_owner != selected or (remove_id is None and now - notice_time > 4)):
            notice = ""
            notice_owner = None
            remove_id = None
        rate = sum(item.speed for item in items)
        if smooth is None or not rate:
            smooth = rate
        elif now - last_sample >= 0.5:
            smooth = 0.75 * smooth + 0.25 * rate
            last_sample = now
        log_item = next((item for item in items if item.id == log_id), None)
        if log_id and not log_item:
            log_id = None
        frame, scroll = queue_frame(
            items,
            tab=tab,
            selected=selected,
            scroll=scroll,
            width=width,
            height=height,
            speed=smooth,
            log_item=log_item,
            log_scroll=log_scroll,
            help_view=help_view,
            notice=notice,
        )
        if hasattr(stdscr, "render_frame"):
            if frame != previous or previous_size != (height, width):
                stdscr.render_frame(frame)
                previous_size = (height, width)
        else:
            changed = False
            if previous_size != (height, width):
                stdscr.erase()
                previous = None
                previous_size = (height, width)
            for row, line in enumerate(frame):
                if previous is not None and row < len(previous) and line == previous[row]:
                    continue
                changed = True
                backend.safe_addstr(stdscr, row, 0, " " * max(0, width - 1))
                x = 0
                for run in line.runs:
                    backend.safe_addstr(stdscr, row, x, run.text, attribute(run.role))
                    x += display_width(run.text)
            if changed:
                stdscr.refresh()
        previous = frame
        key, resized = backend._read_input_key(stdscr)
        if resized:
            if not hasattr(stdscr, "render_frame"):
                backend._handle_resize_event(stdscr)
            previous = None
        if key == -1:
            continue
        if remove_id is not None:
            target = remove_id
            remove_id = None
            notice = ""
            notice_owner = None
            if key in (ord("y"), ord("Y")):
                worker.cancel_item(target)
                worker.discard_finished_item(target)
                continue
            if key in (27, ord("n"), ord("N")):
                continue
            # Other shortcuts dismiss confirmation and retain their usual action.
        if help_view:
            help_view = False
            previous = None
            continue
        if key == ord("?"):
            help_view = True
            continue
        if log_id:
            if key in (27, ord("q"), 10, 13, curses.KEY_LEFT):
                log_id = None
            elif key in (curses.KEY_UP, ord("k")):
                log_scroll += 1
            elif key in (curses.KEY_DOWN, ord("j")):
                log_scroll = max(0, log_scroll - 1)
            elif key == curses.KEY_PPAGE:
                log_scroll += 10
            elif key == curses.KEY_NPAGE:
                log_scroll = max(0, log_scroll - 10)
            elif key == curses.KEY_HOME:
                log_scroll = max(0, len(log_item.logs) - 1)
            elif key == curses.KEY_END:
                log_scroll = 0
            if key != 3:
                continue
        if key in (3, ord("q"), ord("Q"), 27):
            if key == 27:
                if not active and not pending and worker and hasattr(worker, "dismiss_when_idle"):
                    worker.dismiss_when_idle()
                return "BACK"
            if active or pending:
                choice = (
                    backend.prompt_download_exit_cli(active, pending, tty_fd=stdscr.runtime.fileno())
                    if hasattr(stdscr, "render_frame")
                    else backend.show_download_exit_dialog(stdscr, active, pending)
                )
                previous = None
                stdscr.erase()
                if choice == "STOP_QUIT":
                    worker.stop()
                    return "QUIT"
                if choice == "FINISH_QUIT":
                    if hasattr(worker, "resume_paused_items"):
                        worker.resume_paused_items()
                    auto_quit = True
                continue
            return "QUIT"
        if key in (9, curses.KEY_BTAB, ord("1"), ord("2"), ord("3"), ord("4")):
            memories[tab] = (selected, scroll)
            if ord("1") <= key <= ord("4"):
                tab = TABS[key - ord("1")]
            else:
                tab = TABS[(TABS.index(tab) + (-1 if key == curses.KEY_BTAB else 1)) % len(TABS)]
            selected, scroll = memories.get(tab, (None, 0))
            continue
        index = next((i for i, item in enumerate(shown) if item.id == selected), 0)
        if key in (curses.KEY_UP, ord("k"), curses.KEY_DOWN, ord("j")) and shown:
            selected = shown[(index + (-1 if key in (curses.KEY_UP, ord("k")) else 1)) % len(shown)].id
        elif key in (curses.KEY_HOME, ord("g")) and shown:
            selected = shown[0].id
        elif key in (curses.KEY_END, ord("G")) and shown:
            selected = shown[-1].id
        elif key in (curses.KEY_PPAGE, curses.KEY_NPAGE) and shown:
            selected = shown[max(0, min(len(shown) - 1, index + (-5 if key == curses.KEY_PPAGE else 5)))].id
        elif key in (10, 13, curses.KEY_RIGHT, ord("l")) and selected:
            log_id, log_scroll = selected, 0
        elif worker and key in (ord("+"), ord("="), ord("-")):
            worker.set_concurrency(worker.max_concurrent + (-1 if key == ord("-") else 1))
        elif worker and selected:
            selected_row = next((item for item in shown if item.id == selected), None)
            if key in (ord(" "), ord("p")) and selected_row:
                if selected_row.status == "paused":
                    if worker.resume_item(selected) is False:
                        notice = "Cannot resume: saved entry lacks provider identity. Reselect this file once."
                        notice_owner, notice_time = selected, time.monotonic()
                elif selected_row.status in {"downloading", "resolving", "queued"}:
                    worker.pause_item(selected)
            elif key == ord("s"):
                worker.cancel_item(selected)
            elif key == ord("x"):
                remove_id = selected
                notice_owner, notice_time = selected, time.monotonic()
                notice = "Remove selected entry? Files will be kept. y confirm · n/Esc cancel"
            elif key == ord("r"):
                if worker.retry_item(selected) is False:
                    notice = "Cannot resume: saved entry lacks provider identity. Reselect this file once."
                    notice_owner, notice_time = selected, time.monotonic()
