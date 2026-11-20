#!/usr/bin/env python3
"""Download queue entry point, terminal lifecycle and compatibility helpers."""

import os
from .viewport import content_size as _content_size
os.environ.setdefault("ESCDELAY", "25")

import curses
import glob
import re
import shutil
import signal
import subprocess
import sys
import termios
import textwrap
import time
from typing import Any

try:
    from ..media.download_worker import (
        DownloadWorker,
        DownloadItem,
        DownloadStatus,
    )
except (ImportError, ValueError):
    from media_cli.media.download_worker import (
        DownloadWorker,
        DownloadItem,
        DownloadStatus,
    )

# --- Show & Episodes Data ---
SHOW_TITLE = "That Time I Got Reincarnated as a Slime S3"


def format_ep_display(ep_label: Any) -> str:
    """Format episode label with appropriate prefix (e.g. '26 [56]' -> 'E26 [56]', 'Movie' -> 'MOVIE', 'OVA 2' -> 'OVA 2')."""
    s = str(ep_label or "").strip()
    if not s or s == "?":
        return "E?"
    low = s.lower()
    if low.startswith(("episode ", "ep ")):
        clean = re.sub(r"^(?:episode|ep)\s*", "", s, flags=re.I).strip()
        return f"E{clean}"
    if low.startswith("e") and len(low) > 1 and (low[1].isdigit() or low[1] in (" ", "[")):
        return s
    if low in ("movie", "ova"):
        return low.upper()
    if low == "special" or low.startswith(("ova ", "special ")):
        return s.title()
    return f"E{s}"


def format_table_title(raw_title: str, ep_label: Any, width: int) -> str:
    """Formats show title with guaranteed episode suffix, e.g. Title… E2 or Title E26 [56]."""
    ep_str = format_ep_display(ep_label)
    full_str = f"{raw_title} {ep_str}"
    if len(full_str) <= width:
        return f"{full_str:<{width}}"
    # Format as: Title… E2 (using unicode ellipsis '…')
    suffix = f"… {ep_str}"
    if width > len(suffix) + 1:
        prefix = raw_title[: width - len(suffix)].rstrip()
        res = f"{prefix}… {ep_str}"
        return f"{res:<{width}}"
    elif width >= len(ep_str):
        return f"{ep_str:>{width}}"
    return f"{ep_str[:width]}"


def _table_title_text(row: Any) -> str:
    try:
        title = row["title"]
        ep = row["ep"]
    except Exception:
        title = getattr(row, "title", "")
        ep = getattr(row, "ep", "")
    return f"{title} {format_ep_display(ep)}".strip()


def _row_text(row: Any, key: str, default: str = "") -> str:
    try:
        value = row[key]
    except Exception:
        value = getattr(row, key, default)
    return str(value if value is not None else default)


def _detail_episode_value(ep_label: Any) -> str:
    value = str(ep_label or "").strip()
    if not value or value == "?":
        return "?"
    low = value.lower()
    if low.startswith("episode "):
        return value.split(None, 1)[1].strip() or value
    if low.startswith("ep "):
        return value.split(None, 1)[1].strip() or value
    if len(value) > 1 and value[0].lower() == "e" and value[1].isdigit():
        return value[1:].strip() or value
    return value


def _home_shortened_path(path: str) -> str:
    if not path:
        return ""
    expanded = os.path.abspath(os.path.expanduser(path))
    home = os.path.abspath(os.path.expanduser("~"))
    try:
        rel = os.path.relpath(expanded, home)
    except ValueError:
        return expanded
    if rel == ".":
        return "~"
    if not rel.startswith("..") and rel != os.pardir:
        return os.path.join("~", rel)
    return expanded


def build_download_table_layout(table_w: int, rows: list[Any] | None = None) -> dict[str, Any]:
    """Calculate stable, content-aware download table columns."""
    stable_status_w = len("✖ STOPPED")
    stable_progress_w = len("100.0%")
    stable_speed_w = len("999.9MiB/s")
    stable_eta_w = len("99:59:59")
    rows = rows or []
    if table_w < 54:
        layout = {
            "mode": "compact",
            "prefix_w": 1,
            "no_w": 3,
            "stat_w": 7,
            "prog_hdr_w": 5,
            "prog_hdr_text": "PRG",
            "spd_hdr_w": 0,
            "spd_hdr_text": "",
            "eta_w": 0,
            "title_gap": " ",
            "status_gap": " ",
            "progress_gap": "",
            "speed_gap": "",
            "gap": " ",
            "right_pad": " ",
            "min_title_w": 6,
        }
    elif table_w < 68:
        layout = {
            "mode": "narrow6",
            "prefix_w": 2,
            "no_w": 3,
            "stat_w": 8,
            "prog_hdr_w": 6,
            "prog_hdr_text": "PRG",
            "spd_hdr_w": 6,
            "spd_hdr_text": "SPD",
            "eta_w": 4,
            "title_gap": "  ",
            "status_gap": " ",
            "progress_gap": " ",
            "speed_gap": " ",
            "gap": " ",
            "right_pad": " ",
            "min_title_w": 8,
        }
    elif table_w < 85:
        layout = {
            "mode": "med6",
            "prefix_w": 2,
            "no_w": 4,
            "stat_w": 8,
            "prog_hdr_w": 7,
            "prog_hdr_text": "PROG",
            "spd_hdr_w": 7,
            "spd_hdr_text": "SPEED",
            "eta_w": 5,
            "title_gap": "  ",
            "status_gap": "  ",
            "progress_gap": "  ",
            "speed_gap": "  ",
            "gap": "  ",
            "right_pad": "  ",
            "min_title_w": 14,
        }
    else:
        layout = {
            "mode": "full6",
            "prefix_w": 2,
            "no_w": 4,
            "stat_w": 9,
            "prog_hdr_w": 8,
            "prog_hdr_text": "PROGRESS",
            "spd_hdr_w": 8,
            "spd_hdr_text": "SPEED",
            "eta_w": 6,
            "title_gap": "   ",
            "status_gap": "  ",
            "progress_gap": "  ",
            "speed_gap": "  ",
            "gap": "  ",
            "right_pad": "  ",
            "min_title_w": 16,
        }

    speed_values = [
        _row_text(row, "speed_str", "--")
        for row in rows
        if _row_text(row, "status") == "DOWNLOADING"
    ]
    eta_values = [
        _row_text(row, "eta_str", "--")
        for row in rows
        if _row_text(row, "status") == "DOWNLOADING"
    ]
    progress_values = [
        f"{float(getattr(row, 'progress', 0.0)):5.1f}%"
        for row in rows
    ]
    if layout["mode"] != "compact":
        layout["stat_w"] = max(layout["stat_w"], stable_status_w, len("STATUS"))
        layout["prog_hdr_w"] = max(layout["prog_hdr_w"], stable_progress_w, len(layout["prog_hdr_text"]), *[len(v) for v in progress_values], 2)
    if layout["spd_hdr_w"]:
        layout["spd_hdr_w"] = max(layout["spd_hdr_w"], stable_speed_w, len(layout["spd_hdr_text"]), *[len(v) for v in speed_values], 2)
        layout["eta_w"] = max(layout["eta_w"], stable_eta_w, len("ETA"), *[len(v) for v in eta_values], 2)

    gaps = ["title_gap", "status_gap"]
    if layout["spd_hdr_w"]:
        gaps.extend(["progress_gap", "speed_gap"])

    fixed_without_title = (
        layout["prefix_w"]
        + layout["no_w"]
        + layout["stat_w"]
        + layout["prog_hdr_w"]
        + len(layout["right_pad"])
        + sum(len(layout[g]) for g in gaps)
    )
    if layout["spd_hdr_w"]:
        fixed_without_title += layout["spd_hdr_w"] + layout["eta_w"]

    available_title_w = max(1, table_w - fixed_without_title)
    longest_title = max([len(_table_title_text(row)) for row in rows] + [len("TITLE")])
    title_w = min(max(layout["min_title_w"], min(available_title_w, longest_title)), available_title_w)
    layout["title_w"] = title_w
    base_total_w = fixed_without_title + title_w

    spare_w = max(0, table_w - base_total_w)
    if gaps and spare_w:
        per_gap, remainder = divmod(spare_w, len(gaps))
        for idx, gap_name in enumerate(gaps):
            layout[gap_name] += " " * (per_gap + (1 if idx < remainder else 0))

    layout["gap"] = layout["status_gap"]
    layout["fixed_w"] = fixed_without_title
    layout["total_w"] = min(table_w, base_total_w + spare_w)
    return layout


def download_content_row_count(height: int, table_hdr_row: int) -> int:
    """Rows available below the table header, reserving footer and one spacer line."""
    return max(1, height - table_hdr_row - 3)


def build_filter_tab_segments(
    filter_list: list[str],
    active_filter: str,
    tab_counts: dict[str, int],
    is_compact: bool,
    start_x: int = 1,
) -> list[dict[str, Any]]:
    """Return tab labels with one plain separator column between colored blocks."""
    segments: list[dict[str, Any]] = []
    curr_x = start_x
    for name in filter_list:
        if is_compact:
            short_names = {"all": "All", "active": "Act", "completed": "Done"}
            label = f" {short_names.get(name, name)}:{tab_counts[name]} "
        else:
            label = f" {name.capitalize()} ({tab_counts[name]}) "
        segments.append({
            "x": curr_x,
            "label": label,
            "name": name,
            "active": active_filter == name,
        })
        curr_x += len(label) + 1
    return segments


class UIItemWrapper:
    """Adapts a real DownloadItem so the TUI can render it seamlessly."""

    def __init__(self, item: DownloadItem, ep_num: Any = None):
        self.raw = item
        self._fallback_ep = ep_num

    @property
    def ep(self) -> str:
        # 1. Prefer user-facing display label if present on item.episode (e.g. 'Episode 26 [56]', 'Episode 2')
        ep_name = getattr(self.raw, "episode", None)
        if ep_name:
            s = str(ep_name).strip()
            from media_cli.domain.episodes import parse_episode_dual_numbers
            prim, sec = parse_episode_dual_numbers(s)
            if sec:
                return f"{prim} [{sec}]" if prim else f"[{sec}]"
            elif prim:
                return prim
            else:
                low = s.lower()
                for pfx in ("episode ", "ep ", "ep"):
                    if low.startswith(pfx):
                        return s[len(pfx):].strip()
                if s:
                    return s

        # 2. Extract clean display number from item.ep_id (handles URLs, scraper slugs, query params)
        ep_id = getattr(self.raw, "ep_id", None)
        if ep_id:
            from media_cli.domain.episodes import clean_episode_identifier, create_episode_entry
            entry = create_episode_entry(str(ep_id))
            disp = entry.get("display_number")
            f_num = entry.get("franchise_number")
            if f_num and disp:
                return f"{disp} [{f_num}]"
            elif disp and disp != "0":
                return disp
            clean = clean_episode_identifier(str(ep_id))
            if clean:
                return clean

        if self._fallback_ep is not None:
            return str(self._fallback_ep)
        return "?"

    @property
    def id(self) -> str:
        return self.raw.id

    @property
    def title(self) -> str:
        return self.raw.title

    @property
    def status(self) -> str:
        st = self.raw.status
        if st in (DownloadStatus.COMPLETED, DownloadStatus.SKIPPED):
            return "DONE"
        if st == DownloadStatus.DOWNLOADING:
            return "DOWNLOADING"
        if st == DownloadStatus.RESOLVING:
            return "RESOLVING"
        if st == DownloadStatus.PAUSED:
            return "PAUSED"
        if st == DownloadStatus.QUEUED:
            return "QUEUED"
        if st == DownloadStatus.FAILED:
            return "FAILED"
        if st == DownloadStatus.CANCELLED:
            return "CANCELLED"
        return str(st).upper()

    @property
    def progress(self) -> float:
        return self.raw.progress

    @property
    def speed_mbs(self) -> float:
        s = self.raw.speed_str
        if not s:
            return 0.0
        m = re.search(r"([\d.]+)\s*([KkMmGg])", s)
        if m:
            val, unit = float(m.group(1)), m.group(2).upper()
            if unit == "K":
                return val / 1024.0
            if unit == "M":
                return val
            if unit == "G":
                return val * 1024.0
        return 0.0

    @property
    def speed_str(self) -> str:
        return self.raw.speed_str or "--"

    @property
    def eta_sec(self) -> int:
        s = self.raw.eta_str
        if not s:
            return 0
        m = re.search(r"(\d+)s", s)
        if m:
            return int(m.group(1))
        return 0

    @property
    def eta_str(self) -> str:
        return self.raw.eta_str or "--"

    @property
    def size_mb(self) -> float:
        return self.raw.downloaded_bytes / (1024 * 1024)

    @property
    def total_mb(self) -> float:
        tot = self.raw.total_bytes / (1024 * 1024)
        return max(self.size_mb, tot if tot > 0 else 8.5)

    @property
    def logs(self) -> list[str]:
        if self.raw.logs:
            return self.raw.logs
        if self.raw.status == DownloadStatus.QUEUED:
            return ["Waiting in queue for an available download slot…"]
        if self.raw.status == DownloadStatus.RESOLVING:
            return ["Resolving stream URL…"]
        if self.raw.status in (DownloadStatus.COMPLETED, DownloadStatus.SKIPPED):
            return ["Episode already downloaded or completed."]
        return ["Waiting for download activity…"]

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


def build_log_display_lines(item: UIItemWrapper | None) -> list[str]:
    """Return user-facing log lines with stable item context first."""
    if item is None:
        return ["No logs available."]
    raw = item.raw
    lines = [
        f"Title:   {item.title}",
        f"Episode: {_detail_episode_value(item.ep)}",
        f"Status:  {item.status}",
        f"Engine:  {raw.engine or 'auto'}",
    ]
    if getattr(raw, "current_mirror_name", ""):
        lines.append(f"Mirror:  {raw.current_mirror_name}")
    if getattr(raw, "destination_path", ""):
        lines.append(f"Filename: {os.path.basename(raw.destination_path)}")
        lines.append(f"Path:    {_home_shortened_path(os.path.dirname(raw.destination_path))}")
    if getattr(raw, "error", ""):
        lines.append(f"Error:   {raw.error}")
    lines.append("─" * 36)
    lines.extend(item.logs)
    return lines


def build_log_panel_rows(
    item: UIItemWrapper | None,
    width: int,
    row_count: int,
    scroll: int | None = None,
) -> tuple[list[str], str]:
    """Build wrapped log rows and an optional scroll range marker."""
    wrapped_logs: list[str] = []
    wrap_width = max(10, width - 2)
    for line in build_log_display_lines(item):
        msg = f"• {line}"
        chunks = textwrap.wrap(msg, width=wrap_width, subsequent_indent="  ")
        wrapped_logs.extend(chunks if chunks else [msg])

    if row_count <= 0:
        return [], ""

    if scroll is None:
        start = max(0, len(wrapped_logs) - row_count)
    else:
        max_scroll = max(0, len(wrapped_logs) - row_count)
        start = max(0, min(max_scroll, scroll))
    end = min(len(wrapped_logs), start + row_count)
    marker = f"{start + 1}-{end}/{len(wrapped_logs)}" if len(wrapped_logs) > row_count else ""
    return wrapped_logs[start:end], marker


def format_log_cell(text: str, width: int) -> str:
    cell_w = max(0, int(width or 0))
    if cell_w <= 0:
        return ""
    return str(text or "")[:cell_w].ljust(cell_w)


def _count_unfinished_download_rows(rows: list[dict]) -> tuple[int, int]:
    def _status(row) -> str:
        if isinstance(row, dict):
            return str(row.get("status") or "")
        try:
            return str(row["status"] or "")
        except Exception:
            return ""

    active = sum(1 for row in rows if _status(row) in ("DOWNLOADING", "RESOLVING", "PAUSED"))
    queued = sum(1 for row in rows if _status(row) == "QUEUED")
    return active, queued


_FORCE_MANAGER_REPAINT = False


def request_download_manager_repaint() -> None:
    global _FORCE_MANAGER_REPAINT
    _FORCE_MANAGER_REPAINT = True


class SystemStats:
    """Collects real system stats on Linux/Android with graceful fallbacks."""

    def __init__(self):
        self._last_cpu_time = 0.0
        self._last_cpu_idle = 0.0
        self._last_cpu_total = 0.0
        self._last_proc_time = 0.0
        self._last_wall_time = 0.0
        self._last_cpu_sample_time: float | None = None
        self.cpu_pct = 12.0
        self.cli_cpu_pct = 0.2
        self._last_net_time = 0.0
        self._last_rx_bytes = 0
        self.rx_speed_mbs = 0.0
        self.net_speed_str = " 0.0M/s"
        self.read_cpu()
        self.read_net_speed()

    def read_net_speed(self) -> tuple[float, str]:
        """Returns (rx_speed_mbs, formatted_str) calculated from /proc/net/dev."""
        now = time.time()
        rx_total = 0
        try:
            with open("/proc/net/dev", "r") as f:
                for line in f.readlines()[2:]:
                    parts = line.split(":")
                    if len(parts) == 2:
                        iface = parts[0].strip()
                        if iface != "lo" and not iface.startswith(("docker", "br-", "veth")):
                            stats = parts[1].split()
                            rx_total += int(stats[0])

            if self._last_net_time > 0:
                dt = now - self._last_net_time
                if dt >= 0.15:
                    rx_delta = max(0, rx_total - self._last_rx_bytes)
                    instant_mbs = (rx_delta / dt) / (1024 * 1024)
                    self.rx_speed_mbs = 0.7 * instant_mbs + 0.3 * self.rx_speed_mbs
                    self._last_rx_bytes = rx_total
                    self._last_net_time = now
            else:
                self._last_rx_bytes = rx_total
                self._last_net_time = now
        except Exception:
            pass

        if self.rx_speed_mbs >= 10.0:
            self.net_speed_str = f"{self.rx_speed_mbs:4.1f}M/s"
        elif self.rx_speed_mbs >= 1.0:
            self.net_speed_str = f"{self.rx_speed_mbs:4.2f}M/s"
        elif self.rx_speed_mbs > 0.0005:
            kbs = self.rx_speed_mbs * 1024.0
            self.net_speed_str = f"{kbs:4.0f}K/s"
        else:
            self.net_speed_str = " 0.0M/s"

        return self.rx_speed_mbs, self.net_speed_str

    def read_cpu(self) -> tuple[float, float]:
        """Returns (cli_cpu_pct, total_cpu_pct)."""
        now = time.time()
        if self._last_cpu_sample_time is not None and now - self._last_cpu_sample_time < 1.0:
            return self.cli_cpu_pct, self.cpu_pct
        self._last_cpu_sample_time = now

        # 1. Process CPU via os.times()
        try:
            t = os.times()
            p_time = t.user + t.system + t.children_user + t.children_system
            if self._last_wall_time > 0:
                diff_wall = now - self._last_wall_time
                diff_p = p_time - self._last_proc_time
                cores = os.cpu_count() or 1
                if diff_wall > 0:
                    raw_pct = (diff_p / diff_wall) * 100.0 / cores
                    self.cli_cpu_pct = max(0.1, min(100.0, raw_pct))
            self._last_proc_time = p_time
            self._last_wall_time = now
        except Exception:
            pass

        # 2. Total system CPU via /proc/stat
        try:
            with open("/proc/stat", "r") as f:
                fields = [float(x) for x in f.readline().strip().split()[1:8]]
            idle = fields[3] + fields[4]
            total = sum(fields)
            if self._last_cpu_total > 0:
                diff_total = total - self._last_cpu_total
                diff_idle = idle - self._last_cpu_idle
                if diff_total > 0:
                    raw_total_cpu = max(0.0, min(100.0, (1.0 - diff_idle / diff_total) * 100.0))
                    self.cpu_pct = (0.85 * self.cpu_pct) + (0.15 * raw_total_cpu)
            self._last_cpu_idle = idle
            self._last_cpu_total = total
        except Exception:
            pass
        return self.cli_cpu_pct, self.cpu_pct

    def read_mem(self) -> tuple[float, float, float]:
        """Returns (used_gb, total_gb, pct)."""
        try:
            with open("/proc/meminfo", "r") as f:
                lines = f.readlines()
            tot = int([l.split()[1] for l in lines if l.startswith("MemTotal:")][0]) / (1024 * 1024)
            avail = int([l.split()[1] for l in lines if l.startswith("MemAvailable:")][0]) / (1024 * 1024)
            used = tot - avail
            pct = (used / tot * 100.0) if tot > 0 else 0.0
            return used, tot, pct
        except Exception:
            return 7.2, 16.0, 45.0

    def read_disk(self) -> tuple[float, float, float]:
        """Returns (free_gb, total_gb, used_pct)."""
        try:
            target = os.path.expanduser("~/Videos/anyplay")
            if not os.path.exists(target):
                target = os.path.expanduser("~")
            u = shutil.disk_usage(target)
            free_gb = u.free / (1024 ** 3)
            tot_gb = u.total / (1024 ** 3)
            used_pct = ((u.total - u.free) / u.total * 100.0) if u.total > 0 else 0.0
            return free_gb, tot_gb, used_pct
        except Exception:
            return 120.0, 500.0, 60.0

    def read_temp(self) -> float:
        """Returns CPU/SOC temperature in Celsius."""
        for path in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
            try:
                with open(path, "r") as f:
                    val = float(f.read().strip()) / 1000.0
                    if 15.0 <= val <= 105.0:
                        return val
            except Exception:
                continue
        return 47.0

    def read_network_ssid(self) -> tuple[str, str, str]:
        """Returns (type, name, iface)."""
        ssid = ""
        try:
            ssid = subprocess.check_output(["iwgetid", "-r"], text=True, timeout=0.3).strip()
        except Exception:
            pass
        if not ssid:
            try:
                nm = subprocess.check_output(["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"], text=True, timeout=0.3)
                for line in nm.splitlines():
                    if line.startswith("yes:"):
                        ssid = line.split(":", 1)[1]
                        break
            except Exception:
                pass
        if not ssid:
            ssid = "JioFiber_5G"
        return "Wi-Fi", ssid, "wlan0"

    def read_loadavg(self) -> str:
        try:
            l1, l5, l15 = os.getloadavg()
            return f"{l1:.2f} {l5:.2f} {l15:.2f}"
        except Exception:
            return "0.24 0.31 0.28"

    def read_uptime(self) -> str:
        try:
            with open("/proc/uptime", "r") as f:
                sec = float(f.readline().split()[0])
            hrs = int(sec // 3600)
            mins = int((sec % 3600) // 60)
            secs = int(sec % 60)
            return f"{hrs:02d}:{mins:02d}:{secs:02d}"
        except Exception:
            return "04:49:03"


def safe_addstr(stdscr, y: int, x: int, text: str, attr: int = 0):
    """Safely write string without crashing on terminal edge bounds."""
    try:
        h, w = stdscr.getmaxyx()
        if y < 0 or y >= h or x < 0 or x >= w:
            return
        max_len = w - x
        if y == h - 1:
            max_len = max(0, w - x - 1)
        if max_len <= 0:
            return
        stdscr.addstr(y, x, str(text)[:max_len], attr)
    except curses.error:
        pass


def _current_terminal_size(stdscr) -> tuple[int, int]:
    """Return fresh ``(height, width)`` from the real terminal when available."""
    for stream in (sys.stdin, sys.stdout, sys.__stdout__):
        try:
            if stream and stream.isatty():
                size = _content_size(stream.fileno())
                return size.lines, size.columns
        except (AttributeError, OSError, ValueError):
            continue
    try:
        return stdscr.getmaxyx()
    except curses.error:
        return 24, 80


def _restore_terminal_input_modes() -> None:
    """Reset terminal input/display modes that curses mouse/keypad can leave behind."""
    try:
        curses.mousemask(0)
    except curses.error:
        pass
    try:
        curses.flushinp()
    except curses.error:
        pass
    try:
        if sys.stdin and sys.stdin.isatty():
            termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
    except (AttributeError, OSError, termios.error, ValueError):
        pass

    reset_seq = (
        "\033[?1000l\033[?1002l\033[?1003l\033[?1006l"
        "\033[?2004l"
        "\033[?1l\033>"
        "\033[?25h\033[0m"
    )
    for stream in (sys.__stdout__, sys.stdout):
        try:
            if stream and not stream.closed:
                stream.write(reset_seq)
                stream.flush()
        except Exception:
            pass
    try:
        with open("/dev/tty", "w") as tty:
            tty.write(reset_seq)
            tty.flush()
    except Exception:
        pass


def _init_download_color_pairs(curses_mod) -> None:
    """Initialize downloader color pairs."""
    has_256 = (curses_mod.COLORS >= 256)

    if has_256:
        # Meter and status accents.
        curses_mod.init_pair(1, 116, -1)   # Soft Pastel Cyan
        curses_mod.init_pair(2, 120, -1)   # Soft Pastel Green
        curses_mod.init_pair(3, 222, -1)   # Soft Pastel Yellow
        curses_mod.init_pair(4, 204, -1)   # Soft Pastel Coral / Red
        curses_mod.init_pair(5, 141, -1)   # Soft Pastel Magenta / Purple
        curses_mod.init_pair(6, 117, -1)   # Soft Pastel Sky Blue

        # Table header, tab, and selection accents.
        curses_mod.init_pair(10, curses_mod.COLOR_BLACK, curses_mod.COLOR_GREEN)
        curses_mod.init_pair(11, curses_mod.COLOR_BLACK, curses_mod.COLOR_GREEN)
        curses_mod.init_pair(12, curses_mod.COLOR_BLACK, curses_mod.COLOR_BLUE)
        curses_mod.init_pair(13, curses_mod.COLOR_BLACK, curses_mod.COLOR_CYAN)
        curses_mod.init_pair(15, 238, -1)

        # Melo-style bottom tab bar colors
        curses_mod.init_pair(20, 245, 234)  # Footer text & bg (dark charcoal)
        curses_mod.init_pair(21, 234, 151)  # Active tab: dark text on accent (pastel cyan/teal)
        curses_mod.init_pair(22, 252, 238)  # Inactive tab 1: light text on slate gray
        curses_mod.init_pair(23, 250, 236)  # Inactive tab 2: light text on darker slate

        # Powerline  separators: fg=left_bg, bg=right_bg
        curses_mod.init_pair(24, 151, 238)  # Active -> Inact 1
        curses_mod.init_pair(25, 238, 151)  # Inact 1 -> Active
        curses_mod.init_pair(26, 151, 236)  # Active -> Inact 2
        curses_mod.init_pair(27, 236, 151)  # Inact 2 -> Active
        curses_mod.init_pair(28, 238, 236)  # Inact 1 -> Inact 2
        curses_mod.init_pair(29, 236, 238)  # Inact 2 -> Inact 1

        # Tab -> Footer background (234) trailing separator
        curses_mod.init_pair(30, 151, 234)  # Active -> Footer
        curses_mod.init_pair(31, 238, 234)  # Inact 1 -> Footer
        curses_mod.init_pair(32, 236, 234)  # Inact 2 -> Footer
    else:
        # 8-color ANSI Fallbacks
        curses_mod.init_pair(1, curses_mod.COLOR_CYAN, -1)
        curses_mod.init_pair(2, curses_mod.COLOR_GREEN, -1)
        curses_mod.init_pair(3, curses_mod.COLOR_YELLOW, -1)
        curses_mod.init_pair(4, curses_mod.COLOR_RED, -1)
        curses_mod.init_pair(5, curses_mod.COLOR_MAGENTA, -1)
        curses_mod.init_pair(6, curses_mod.COLOR_BLUE, -1)

        curses_mod.init_pair(10, curses_mod.COLOR_BLACK, curses_mod.COLOR_GREEN)
        curses_mod.init_pair(11, curses_mod.COLOR_BLACK, curses_mod.COLOR_GREEN)
        curses_mod.init_pair(12, curses_mod.COLOR_BLACK, curses_mod.COLOR_BLUE)
        curses_mod.init_pair(13, curses_mod.COLOR_BLACK, curses_mod.COLOR_CYAN)
        curses_mod.init_pair(15, curses_mod.COLOR_BLACK, -1)

        curses_mod.init_pair(20, curses_mod.COLOR_WHITE, curses_mod.COLOR_BLACK)
        curses_mod.init_pair(21, curses_mod.COLOR_BLACK, curses_mod.COLOR_CYAN)
        curses_mod.init_pair(22, curses_mod.COLOR_WHITE, curses_mod.COLOR_BLACK)
        curses_mod.init_pair(23, curses_mod.COLOR_WHITE, curses_mod.COLOR_BLUE)
        curses_mod.init_pair(24, curses_mod.COLOR_CYAN, curses_mod.COLOR_BLACK)
        curses_mod.init_pair(25, curses_mod.COLOR_BLACK, curses_mod.COLOR_CYAN)
        curses_mod.init_pair(26, curses_mod.COLOR_CYAN, curses_mod.COLOR_BLUE)
        curses_mod.init_pair(27, curses_mod.COLOR_BLUE, curses_mod.COLOR_CYAN)
        curses_mod.init_pair(28, curses_mod.COLOR_BLACK, curses_mod.COLOR_BLUE)
        curses_mod.init_pair(29, curses_mod.COLOR_BLUE, curses_mod.COLOR_BLACK)
        curses_mod.init_pair(30, curses_mod.COLOR_CYAN, curses_mod.COLOR_BLACK)
        curses_mod.init_pair(31, curses_mod.COLOR_WHITE, curses_mod.COLOR_BLACK)
        curses_mod.init_pair(32, curses_mod.COLOR_BLUE, curses_mod.COLOR_BLACK)


def _handle_resize_event(stdscr) -> None:
    new_h, new_w = _current_terminal_size(stdscr)
    try:
        curses.resizeterm(new_h, new_w)
    except (curses.error, AttributeError):
        pass
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    try:
        stdscr.clearok(False)
    except curses.error:
        pass
    try:
        stdscr.erase()
    except curses.error:
        pass


def _read_input_key(stdscr, max_resize_events: int = 20) -> tuple[int, bool]:
    """Read one actionable key while collapsing queued resize noise."""
    saw_resize = False
    for _ in range(max_resize_events):
        ch = stdscr.getch()
        if ch == curses.KEY_RESIZE:
            saw_resize = True
            continue
        return ch, saw_resize
    return -1, saw_resize


def _clear_content_area(stdscr, start_y: int, end_y: int, width: int) -> None:
    """Clear the table/log body without repainting the whole terminal."""
    blank = " " * max(0, width - 1)
    if not blank:
        return
    for row_y in range(max(0, start_y), max(0, end_y)):
        safe_addstr(stdscr, row_y, 0, blank, curses.A_NORMAL)


def draw_meter_bar(
    stdscr,
    y: int,
    x: int,
    width: int,
    label: str,
    percent: float,
    val_str: str,
    color_pair: int,
    track_pair: int,
    val_field_w: int = 10,
):
    """Renders a progress meter with square cells: Label  ■■■■■■■■■■■■■■■■■■■■■■■■  val%"""
    if width < 18:
        return

    # Left Label (5 chars)
    lbl_text = f"{label:<5}"
    safe_addstr(stdscr, y, x, lbl_text, curses.A_NORMAL)

    # Right Value (fixed width, right-aligned to column edge)
    val_clean = str(val_str).strip()
    val_text = f"{val_clean:>{val_field_w}}"
    val_x = x + width - val_field_w
    safe_addstr(stdscr, y, val_x, val_text, curses.A_NORMAL)

    # Meter bar spans from x + 6 to val_x - 1 (equal width across all gauge rows)
    bar_x = x + 6
    bar_end_x = max(bar_x, val_x - 1)
    bar_width = max(0, bar_end_x - bar_x)

    pct = max(0.0, min(100.0, float(percent)))
    filled_blocks = int(round((pct / 100.0) * bar_width))
    empty_blocks = max(0, bar_width - filled_blocks)

    # Filled portion: square cells ■ in active color
    if filled_blocks > 0:
        safe_addstr(stdscr, y, bar_x, "■" * filled_blocks, color_pair)

    # Empty portion: square cells ■ in dark gray
    if empty_blocks > 0:
        safe_addstr(stdscr, y, bar_x + filled_blocks, "■" * empty_blocks, track_pair)


def _meter_value_width(layout: str, values: object = ()) -> int:
    """Return stable meter value-column widths so live metrics do not shift bars."""
    fixed = {
        "compact": 8,
        "short": 11,
        "medium": 11,
        "wide": 13,
    }.get(layout, 11)

    if values is None:
        candidates: list[object] = []
    elif isinstance(values, (str, bytes)):
        candidates = [values]
    else:
        try:
            candidates = list(values)  # type: ignore[arg-type]
        except TypeError:
            candidates = [values]

    widths = [fixed]
    widths.extend(len(str(value).strip()) for value in candidates)
    return max(widths)


def draw_dual_aligned_row(stdscr, y: int, x: int, width: int, left_key: str, right_val: str, key_attr: int, val_attr: int):
    """Renders a row with left_key left-aligned and right_val right-aligned in the column."""
    if width <= 0:
        return
    safe_addstr(stdscr, y, x, left_key, key_attr)
    val_str = str(right_val)
    val_x = x + width - len(val_str)
    if val_x > x + len(left_key):
        safe_addstr(stdscr, y, val_x, val_str, val_attr)
    else:
        avail = max(0, width - len(left_key) - 1)
        safe_addstr(stdscr, y, x + len(left_key) + 1, val_str[:avail], val_attr)



def show_download_exit_dialog(stdscr, active_count: int, queued_count: int) -> str:
    """Displays the user-specified exit prompt:
    {active} active downloads · {queued} queued

    What would you like to do?

         [1] Stop downloads and quit
         [2] Finish downloads, then quit automatically
         [Esc] Keep AnyPlay open

    Returns 'STOP_QUIT', 'FINISH_QUIT', or 'CANCEL'.
    """
    height, width = stdscr.getmaxyx()
    box_w = min(56, max(30, width - 4))
    box_h = 10
    start_y = max(1, (height - box_h) // 2)
    start_x = max(1, (width - box_w) // 2)

    title_line = f"{active_count} active downloads · {queued_count} queued"
    prompt_q = "What would you like to do?"
    opt1 = "     [1] Stop downloads and quit"
    opt2 = "     [2] Finish downloads, then quit automatically (resume paused)"
    opt3 = "     [Esc] Keep AnyPlay open"

    while True:
        # Clear background box
        for r in range(box_h):
            safe_addstr(stdscr, start_y + r, start_x, " " * box_w, curses.A_REVERSE)

        # Border
        border_top = "┌" + "─" * (box_w - 2) + "┐"
        border_bot = "└" + "─" * (box_w - 2) + "┘"
        safe_addstr(stdscr, start_y, start_x, border_top, curses.A_REVERSE | curses.A_BOLD)
        safe_addstr(stdscr, start_y + box_h - 1, start_x, border_bot, curses.A_REVERSE | curses.A_BOLD)
        for r in range(1, box_h - 1):
            safe_addstr(stdscr, start_y + r, start_x, "│", curses.A_REVERSE | curses.A_BOLD)
            safe_addstr(stdscr, start_y + r, start_x + box_w - 1, "│", curses.A_REVERSE | curses.A_BOLD)

        # Content
        safe_addstr(stdscr, start_y + 1, start_x + 3, title_line[:box_w - 6], curses.A_REVERSE | curses.A_BOLD)
        safe_addstr(stdscr, start_y + 3, start_x + 3, prompt_q[:box_w - 6], curses.A_REVERSE | curses.A_BOLD)
        safe_addstr(stdscr, start_y + 5, start_x + 3, opt1[:box_w - 6], curses.A_REVERSE)
        safe_addstr(stdscr, start_y + 6, start_x + 3, opt2[:box_w - 6], curses.A_REVERSE)
        safe_addstr(stdscr, start_y + 7, start_x + 3, opt3[:box_w - 6], curses.A_REVERSE)

        stdscr.refresh()
        try:
            k = stdscr.getch()
        except KeyboardInterrupt:
            return "STOP_QUIT"

        if k in (ord('1'), ord('s'), ord('S')):
            return "STOP_QUIT"
        elif k in (ord('2'), ord('f'), ord('F')):
            return "FINISH_QUIT"
        elif k in (27, ord('c'), ord('C')):
            return "CANCEL"


from .terminal_runtime import terminal_application


@terminal_application
def prompt_download_exit_cli(active_count: int, queued_count: int, tty_fd: int | None = None) -> str:
    """Prompt the user in terminal cooked/raw mode when exiting CLI with downloads in progress.

    Prompt layout:
    {active} active downloads · {queued} queued

    What would you like to do?

         [1] Stop downloads and quit
         [2] Finish downloads, then quit automatically
         [Esc] Keep AnyPlay open

    Returns: 'STOP_QUIT', 'FINISH_QUIT', or 'CANCEL'
    """
    if not sys.stdin.isatty():
        return "STOP_QUIT"

    def _prompt_frame() -> str:
        from .screen_chrome import role_prefix, MINT_SCREEN_THEME
        status = role_prefix(MINT_SCREEN_THEME, "player.status")
        heading = role_prefix(MINT_SCREEN_THEME, "dialog.heading")
        lines = [
            f"\033[2;3H{status}{active_count} active downloads · {queued_count} queued\033[0m",
            f"\033[4;3H{heading}What would you like to do?\033[0m",
            "\033[6;7H[1] Stop downloads and quit",
            "\033[7;7H[2] Finish downloads, then quit automatically (resume paused)",
            "\033[8;7H[Esc] Keep AnyPlay open",
        ]
        return "".join(lines)

    from .terminal_output import write_terminal
    alt_already_active = False
    try:
        from . import display as _display
        alt_already_active = bool(_display.is_alt_screen_active())
    except Exception:
        alt_already_active = False
    alt_screen_open = False
    try:
        if alt_already_active:
            write_terminal("\033[H\033[2J\033[?25l")
        else:
            write_terminal("\033[?1049h\033[H\033[2J\033[?25l")
            alt_screen_open = True
        write_terminal(_prompt_frame())
    except Exception:
        alt_screen_open = False

    fd = tty_fd if tty_fd is not None else sys.stdin.fileno()
    try:
        import select, termios, tty
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while True:
                ch = os.read(fd, 1)
                if ch == b"\x1b":
                    if select.select([fd], [], [], 0.03)[0]:
                        _ = os.read(fd, 8)
                    return "CANCEL"
                elif ch in (b"1", b"s", b"S"):
                    return "STOP_QUIT"
                elif ch in (b"2", b"f", b"F"):
                    return "FINISH_QUIT"
                elif ch in (b"\x03",):
                    return "STOP_QUIT"
                elif ch in (b"q", b"Q", b"c", b"C"):
                    return "CANCEL"
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    except Exception:
        try:
            write_terminal("Select [1]: ")
            ans = input().strip()
            if ans in ("2", "f", "F"):
                return "FINISH_QUIT"
            elif ans in ("3", "c", "C", "esc", "Esc"):
                return "CANCEL"
            return "STOP_QUIT"
        except (KeyboardInterrupt, EOFError):
            return "STOP_QUIT"
    finally:
        if alt_already_active:
            try:
                request_download_manager_repaint()
                from .frame_surface import invalidate_text
                invalidate_text()
                write_terminal("\033[H\033[2J\033[?25l")
            except Exception:
                pass
        elif alt_screen_open:
            try:
                write_terminal("\033[?25h\033[?1049l")
            except Exception:
                pass


def _curses_download_manager(stdscr, worker, standalone: bool = False, resize_pending: dict | None = None, auto_quit: bool = False):
    from .download_queue import run_queue
    return run_queue(stdscr, worker, backend=sys.modules[__name__], standalone=standalone,
                     resize_pending=resize_pending, auto_quit=auto_quit)


@terminal_application
def run_download_manager(worker: DownloadWorker | None = None, standalone: bool = False, auto_quit: bool = False) -> str:
    """Launches the Download Manager curses TUI.

    Returns:
        'BACK': User pressed 'q' to return to CLI TUI (downloads continue).
        'QUIT': User chose to quit application.
        'DONE': All downloads finished (standalone mode).
    """
    from .stderr_capture import begin_stderr_capture

    begin_stderr_capture()
    if worker is None:
        from ..media.download_worker import get_download_manager_worker
        worker = get_download_manager_worker()
    resize_pending = {"value": False}

    def _runner(stdscr):
        return _curses_download_manager(stdscr, worker, standalone=standalone, resize_pending=resize_pending, auto_quit=auto_quit)

    # Save existing SIGWINCH handler (picker installs its own at import time).
    # While this curses screen owns the terminal, convert SIGWINCH into a
    # KEY_RESIZE event so getmaxyx() and the render loop see fresh dimensions.
    old_sigwinch = None
    try:
        old_sigwinch = signal.getsignal(signal.SIGWINCH)
    except (AttributeError, OSError, ValueError):
        pass

    def _handle_download_manager_resize(signum, frame):
        resize_pending["value"] = True

    try:
        try:
            signal.signal(signal.SIGWINCH, _handle_download_manager_resize)
        except (AttributeError, OSError, ValueError):
            pass
        from . import terminal_images
        clear_images = terminal_images.replace_for_frame(has_image=False)
        if clear_images:
            from .terminal_output import write_terminal
            write_terminal(clear_images)
            sys.stdout.flush()
        from .download_surface import queue_wrapper
        res = queue_wrapper(_runner)
    except curses.error:
        if standalone and worker is not None:
            worker.wait_all()
            res = "DONE"
        else:
            res = "BACK"
    finally:
        # Restore the original SIGWINCH handler so the picker's resize works again
        if old_sigwinch is not None:
            try:
                signal.signal(signal.SIGWINCH, old_sigwinch)
            except (AttributeError, OSError, ValueError):
                pass
        _restore_terminal_input_modes()
        from .terminal_runtime import current_runtime
        runtime = current_runtime()
        if runtime is not None:
            runtime.ensure_raw()

    return res or "BACK"
