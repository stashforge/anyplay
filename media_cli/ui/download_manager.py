#!/usr/bin/env python3
"""Interactive Downloader TUI Prototype with live queue and system metrics.

Features:
- Dual-aligned top dashboard:
  - Left Column: Network connection, engine, queue counts, storage, load, uptime.
  - Right Column: Segmented cell progress meters for Net, Total, CPU, Mem, Disk, Temp.
- Clean gap separating top dashboard and downloads table.
- Submenu tabs: [All]  [Active]  [Completed] filterable with Tab or 1-3.
- Table: Clean show titles, segmented cell progress bars, speed, and ETA.
- Interactive controls: Space (Pause/Resume), s (Stop), Tab (Filter), q (Exit).
"""

import os
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
    from .layout import MIN_TUI_COLUMNS
    from ..media.download_worker import (
        DownloadWorker,
        DownloadItem,
        DownloadStatus,
        format_bytes,
        get_global_worker,
        has_active_downloads,
        stop_global_worker,
    )
except (ImportError, ValueError):
    from media_cli.ui.layout import MIN_TUI_COLUMNS
    from media_cli.media.download_worker import (
        DownloadWorker,
        DownloadItem,
        DownloadStatus,
        format_bytes,
        get_global_worker,
        has_active_downloads,
        stop_global_worker,
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


def make_ep_resolver(ep_num: int):
    """Generates a lazy stream resolver for the test worker."""
    def _res(item: DownloadItem):
        item.logs.append(f"⚡ Resolving 1080p stream for Episode {ep_num}…")
        time.sleep(1.2)
        return TEST_URL, {}, None
    return _res


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


def create_test_worker() -> DownloadWorker:
    """Spawns DownloadWorker populated with initial realistic test items."""
    tmp_dir = os.path.join(WORKSPACE_ROOT, "test_scripts", "tmp_ui_downloads")
    os.makedirs(tmp_dir, exist_ok=True)
    # Clean previous test artifacts in tmp_ui_downloads so downloads run live
    for f in glob.glob(os.path.join(tmp_dir, "Slime_S3_EP*.mp4*")):
        try:
            os.remove(f)
        except OSError:
            pass

    worker = DownloadWorker(tmp_dir, max_concurrent=2)

    # Ep 1: Direct URL (starts immediately on Slot 1)
    f1 = os.path.join(tmp_dir, "Slime_S3_EP01.mp4")
    it1 = DownloadItem(
        id="ep-1",
        title=SHOW_TITLE,
        episode="Episode 1",
        destination_path=f1,
        url=TEST_URL,
        engine="auto",
    )
    worker.add_item(it1)

    # Ep 2: Lazy resolver (starts immediately on Slot 2)
    f2 = os.path.join(tmp_dir, "Slime_S3_EP02.mp4")
    it2 = DownloadItem(
        id="ep-2",
        title=SHOW_TITLE,
        episode="Episode 2",
        destination_path=f2,
        resolve_fn=make_ep_resolver(2),
        engine="auto",
    )
    worker.add_item(it2)

    # Ep 3 to 6: Queued items with lazy resolvers
    for ep_n in range(3, 7):
        fn = os.path.join(tmp_dir, f"Slime_S3_EP{ep_n:02d}.mp4")
        it_n = DownloadItem(
            id=f"ep-{ep_n}",
            title=SHOW_TITLE,
            episode=f"Episode {ep_n}",
            destination_path=fn,
            resolve_fn=make_ep_resolver(ep_n),
            engine="auto",
        )
        worker.add_item(it_n)

    return worker


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
                size = os.get_terminal_size(stream.fileno())
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
        lines = [
            f"\033[2;3H\033[1;36m{active_count} active downloads · {queued_count} queued\033[0m",
            "\033[4;3H\033[1mWhat would you like to do?\033[0m",
            "\033[6;7H[1] Stop downloads and quit",
            "\033[7;7H[2] Finish downloads, then quit automatically (resume paused)",
            "\033[8;7H[Esc] Keep AnyPlay open",
        ]
        return "".join(lines)

    out = sys.stdout
    alt_already_active = False
    try:
        from . import display as _display
        alt_already_active = bool(_display.is_alt_screen_active())
    except Exception:
        alt_already_active = False
    alt_screen_open = False
    try:
        if alt_already_active:
            out.write("\033[H\033[2J\033[?25l")
        else:
            out.write("\033[?1049h\033[H\033[2J\033[?25l")
            alt_screen_open = True
        out.write(_prompt_frame())
        out.flush()
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
            ans = input("Select [1]: ").strip()
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
                out.write("\033[H\033[2J\033[?25l")
                out.flush()
            except Exception:
                pass
        elif alt_screen_open:
            try:
                out.write("\033[?25h\033[?1049l")
                out.flush()
            except Exception:
                pass


def _curses_download_manager(stdscr, worker, standalone: bool = False, resize_pending: dict | None = None, auto_quit: bool = False):
    global _FORCE_MANAGER_REPAINT
    try:
        curses.raw()
    except (AttributeError, curses.error):
        pass
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    try:
        curses.use_default_colors()
    except curses.error:
        pass
    try:
        curses.set_escdelay(25)
    except (AttributeError, curses.error):
        pass
    stdscr.nodelay(True)
    stdscr.timeout(250)
    try:
        stdscr.leaveok(True)
    except curses.error:
        pass

    # Enable mouse and touch support (clicks, taps, wheel scrolls)
    mouse_mask = (
        curses.BUTTON1_CLICKED
        | curses.BUTTON1_PRESSED
        | getattr(curses, "BUTTON1_DOUBLE_CLICKED", 0)
        | getattr(curses, "BUTTON4_PRESSED", 0x10000)
        | getattr(curses, "BUTTON4_CLICKED", 0x20000)
        | getattr(curses, "BUTTON5_PRESSED", 0x200000)
        | getattr(curses, "BUTTON5_CLICKED", 0x400000)
    )
    curses.mousemask(mouse_mask)
    try:
        curses.mouseinterval(0)
    except curses.error:
        pass

    _init_download_color_pairs(curses)

    C_CYAN = curses.color_pair(1)
    C_GREEN = curses.color_pair(2)
    C_YELLOW = curses.color_pair(3)
    C_RED = curses.color_pair(4)
    C_MAG = curses.color_pair(5)
    C_BLUE = curses.color_pair(6)

    C_HDR = curses.color_pair(10)
    C_TAB_ACT = curses.color_pair(11)
    C_TAB_INACT = curses.color_pair(12)
    C_ROW_SEL = curses.color_pair(13)
    C_TRACK = curses.color_pair(15)

    # State: unselected by default on opening
    selected_idx = -1
    scroll_top = 0
    log_scroll = 0
    show_logs_view = False     # Fullscreen inspector/logs view on narrow screens
    active_filter = "all"      # "all", "active", "completed"
    filter_list = ["all", "active", "completed"]
    auto_quit_pending = bool(auto_quit)
    auto_quit_blocked_message = ""

    sys_stats = SystemStats()
    net_type, net_ssid, net_iface = sys_stats.read_network_ssid()

    if worker is None:
        worker = get_global_worker()
    worker.start()

    log_counter = 0
    last_tap_time = 0.0
    last_tap_pos = (-1, -1)
    last_render_size = None
    last_content_signature = None
    resize_pending = resize_pending if resize_pending is not None else {"value": False}
    if auto_quit_pending and hasattr(worker, "resume_paused_items"):
        worker.resume_paused_items()

    while True:
        if _FORCE_MANAGER_REPAINT:
            _FORCE_MANAGER_REPAINT = False
            try:
                stdscr.erase()
                stdscr.clearok(False)
            except curses.error:
                pass
            last_render_size = None
            last_content_signature = None

        if resize_pending.get("value"):
            resize_pending["value"] = False
            _handle_resize_event(stdscr)
            last_render_size = None

        height, width = _current_terminal_size(stdscr)
        render_size = (height, width)
        if render_size != last_render_size:
            stdscr.erase()
            last_render_size = render_size
        filter_hitboxes = []
        visible_rows_count = 0
        filtered_eps = []
        tab_row = 7
        table_hdr_row = 8
        table_w = width
        max_rows = 1

        if height < 10 or width < MIN_TUI_COLUMNS:
            safe_addstr(stdscr, 0, 0, "Terminal window too small!", curses.A_NORMAL)
            stdscr.refresh()
            ch = stdscr.getch()
            if ch in (ord('q'), ord('Q')):
                break
            time.sleep(0.1)
            continue

        # --- Read Live Worker State ---
        with worker.lock:
            raw_items = list(worker.items)
            current_slots = worker.max_concurrent
        EPISODES = [UIItemWrapper(it) for it in raw_items]

        active_ep = None
        for ep in EPISODES:
            if ep["status"] == "DOWNLOADING":
                active_ep = ep
                break

        # Read system meters
        cli_cpu, cpu_val = sys_stats.read_cpu()
        mem_used, mem_tot, mem_pct = sys_stats.read_mem()
        disk_free, disk_tot, disk_pct = sys_stats.read_disk()
        temp_val = sys_stats.read_temp()
        load_str = sys_stats.read_loadavg()
        uptime_str = sys_stats.read_uptime()
        real_net_speed, real_net_str = sys_stats.read_net_speed()

        # Session aggregations
        total_eps = len(EPISODES)
        done_eps = sum(1 for e in EPISODES if e["status"] in ("DONE", "COMPLETED", "SKIPPED"))
        active_count, queued_count = _count_unfinished_download_rows(EPISODES)
        blocked_count = sum(1 for e in EPISODES if e["status"] in ("FAILED", "CANCELLED", "STOPPED"))
        if auto_quit_pending and active_count == 0 and queued_count == 0:
            if blocked_count > 0:
                auto_quit_pending = False
                auto_quit_blocked_message = f"Auto-quit paused: {blocked_count} download(s) failed/stopped"
            else:
                return "QUIT"
        total_transferred = sum(e["size_mb"] for e in EPISODES)
        total_all_mb = sum(e["total_mb"] for e in EPISODES)
        all_progress_pct = (total_transferred / total_all_mb * 100.0) if total_all_mb > 0 else 0.0
        worker_speed = sum(e["speed_mbs"] for e in EPISODES if e["status"] == "DOWNLOADING")
        curr_dl_speed = max(real_net_speed, worker_speed)
        curr_dl_speed_str = real_net_str if real_net_speed >= worker_speed else f"{worker_speed:4.1f}M/s"

        # Pre-filter episode list based on active tab for all views
        if active_filter == "active":
            filtered_eps = [e for e in EPISODES if e["status"] in ("DOWNLOADING", "RESOLVING", "PAUSED", "QUEUED")]
        elif active_filter == "completed":
            filtered_eps = [e for e in EPISODES if e["status"] in ("DONE", "COMPLETED", "SKIPPED")]
        else:
            filtered_eps = EPISODES

        if filtered_eps and selected_idx >= 0:
            selected_idx = min(len(filtered_eps) - 1, selected_idx)
        elif not filtered_eps:
            selected_idx = -1


        # Top meter section.
        if width < 65:
            cpu_fmt = f"{cli_cpu:.1f}%"
        else:
            cpu_fmt = f"{cli_cpu:.1f}% [{cpu_val:.0f}%]"

        gauges_data = [
            ("Net", min(100.0, curr_dl_speed * 5.0), curr_dl_speed_str, C_CYAN),
            ("CPU", cpu_val, cpu_fmt, C_CYAN),
            ("Total", all_progress_pct, f"{all_progress_pct:4.1f}%", C_GREEN),
            ("Mem", mem_pct, f"{mem_used:.1f}G/{mem_tot:.1f}G", C_MAG),
            ("Disk", disk_pct, f"{disk_free:.0f}G free", C_BLUE),
            ("Temp", min(100.0, (temp_val / 90.0) * 100.0), f"{temp_val:.1f}°C", C_RED if temp_val > 75 else C_GREEN),
        ]

        # Side logs are only shown if width >= 100 and height >= 25
        is_wide = (width >= 100 and height >= 25)

        if width < 50:
            # -----------------------------------------------------
            # COMPACT MODE (Ultra-narrow: width < 50, down to 32 cols):
            # Single column: 2 key meters + compact queue/net line
            # -----------------------------------------------------
            val_field_w = _meter_value_width("compact")
            draw_meter_bar(stdscr, 0, 1, width - 2, "Net", gauges_data[0][1], gauges_data[0][2], gauges_data[0][3], C_TRACK, val_field_w=val_field_w)
            draw_meter_bar(stdscr, 1, 1, width - 2, "Total", gauges_data[2][1], gauges_data[2][2], gauges_data[2][3], C_TRACK, val_field_w=val_field_w)
            info_line = f"{net_ssid} · {current_slots} slots · {curr_dl_speed_str.strip()}"
            safe_addstr(stdscr, 2, 1, info_line[:width - 2], curses.A_DIM)

            tab_row = 4
            table_hdr_row = 5
            is_compact = True
        elif height >= 35 and width >= 85:
            # -----------------------------------------------------
            # FULL TOP SECTION (height >= 35 and width >= 85): 6 rows
            # Dual-column split (Left Info, Right Meters)
            # -----------------------------------------------------
            is_compact = False
            col_gap = 4
            avail_w = max(20, width - col_gap - 2)
            col1_w = avail_w // 2
            col1_x = 1
            col2_x = col1_x + col1_w + col_gap
            col2_w = width - col2_x - 1

            left_rows = [
                ("Network:", f"{net_type} ({net_ssid}) via {net_iface}", C_CYAN),
                ("Engine:", f"yt-dlp + aria2c ({current_slots} slots)", C_YELLOW),
                ("Queue:", f"{active_count} active, {queued_count} queued, {done_eps} done", C_GREEN),
                ("Target:", "~/Videos/anyplay", C_BLUE),
                ("Load:", load_str, curses.A_NORMAL),
                ("Uptime:", uptime_str, curses.A_NORMAL),
            ]

            for r_i, (k_txt, v_txt, v_attr) in enumerate(left_rows):
                draw_dual_aligned_row(stdscr, r_i, col1_x, col1_w, k_txt, v_txt, curses.A_NORMAL, v_attr)

            wide_gauges = [
                gauges_data[0],  # Net
                gauges_data[2],  # Total
                gauges_data[1],  # CPU
                gauges_data[3],  # Mem
                gauges_data[4],  # Disk
                gauges_data[5],  # Temp
            ]

            val_field_w = _meter_value_width("wide", [str(g[2]) for g in wide_gauges])

            for r_i, (g_lbl, g_pct, g_val_str, g_color) in enumerate(wide_gauges):
                draw_meter_bar(
                    stdscr,
                    r_i,
                    col2_x,
                    col2_w,
                    g_lbl,
                    g_pct,
                    g_val_str,
                    g_color,
                    C_TRACK,
                    val_field_w=val_field_w,
                )

            tab_row = 7
            table_hdr_row = 8
        elif height >= 25:
            # -----------------------------------------------------
            # MEDIUM HEIGHT (height >= 25): 3 lines of meters + 1 info line
            # Layout: 3 rows x 2 cols of meters
            # -----------------------------------------------------
            is_compact = False
            meter_col_w = (width - 3) // 2
            col1_x = 1
            col2_x = col1_x + meter_col_w + 1

            val_field_w = _meter_value_width("medium", [str(g[2]) for g in gauges_data])

            # Row 0: Net, CPU
            draw_meter_bar(stdscr, 0, col1_x, meter_col_w, gauges_data[0][0], gauges_data[0][1], gauges_data[0][2], gauges_data[0][3], C_TRACK, val_field_w=val_field_w)
            draw_meter_bar(stdscr, 0, col2_x, meter_col_w, gauges_data[1][0], gauges_data[1][1], gauges_data[1][2], gauges_data[1][3], C_TRACK, val_field_w=val_field_w)

            # Row 1: Total, Mem
            draw_meter_bar(stdscr, 1, col1_x, meter_col_w, gauges_data[2][0], gauges_data[2][1], gauges_data[2][2], gauges_data[2][3], C_TRACK, val_field_w=val_field_w)
            draw_meter_bar(stdscr, 1, col2_x, meter_col_w, gauges_data[3][0], gauges_data[3][1], gauges_data[3][2], gauges_data[3][3], C_TRACK, val_field_w=val_field_w)

            # Row 2: Disk, Temp
            draw_meter_bar(stdscr, 2, col1_x, meter_col_w, gauges_data[4][0], gauges_data[4][1], gauges_data[4][2], gauges_data[4][3], C_TRACK, val_field_w=val_field_w)
            draw_meter_bar(stdscr, 2, col2_x, meter_col_w, gauges_data[5][0], gauges_data[5][1], gauges_data[5][2], gauges_data[5][3], C_TRACK, val_field_w=val_field_w)

            # Row 3: Comprehensive info line
            if width >= 85:
                info_line = f"Net: {net_type} ({net_ssid})  ·  Engine: aria2c ({current_slots} slots)  ·  Queue: {active_count} act, {queued_count} que, {done_eps} done  ·  Target: ~/Videos/anyplay"
            else:
                info_line = f"Net: {net_type} ({net_ssid})  ·  Slots: {current_slots}  ·  Queue: {active_count}/{total_eps}  ·  aria2c"
            safe_addstr(stdscr, 3, 1, info_line[:width - 2], curses.A_DIM)

            tab_row = 5
            table_hdr_row = 6
        else:
            # -----------------------------------------------------
            # SHORT HEIGHT (height < 25): 2 lines of meters + 1 info line
            # Layout: 2 rows x 2 cols of meters
            # -----------------------------------------------------
            is_compact = False
            meter_col_w = (width - 3) // 2
            col1_x = 1
            col2_x = col1_x + meter_col_w + 1
            val_field_w = _meter_value_width("short", [str(g[2]) for g in gauges_data[:4]])

            # Row 0: Net, Total
            draw_meter_bar(stdscr, 0, col1_x, meter_col_w, gauges_data[0][0], gauges_data[0][1], gauges_data[0][2], gauges_data[0][3], C_TRACK, val_field_w=val_field_w)
            draw_meter_bar(stdscr, 0, col2_x, meter_col_w, gauges_data[2][0], gauges_data[2][1], gauges_data[2][2], gauges_data[2][3], C_TRACK, val_field_w=val_field_w)

            # Row 1: CPU, Mem
            draw_meter_bar(stdscr, 1, col1_x, meter_col_w, gauges_data[1][0], gauges_data[1][1], gauges_data[1][2], gauges_data[1][3], C_TRACK, val_field_w=val_field_w)
            draw_meter_bar(stdscr, 1, col2_x, meter_col_w, gauges_data[3][0], gauges_data[3][1], gauges_data[3][2], gauges_data[3][3], C_TRACK, val_field_w=val_field_w)

            # Row 2: Comprehensive info line
            if width >= 85:
                info_line = f"Net: {net_type} ({net_ssid})  ·  Engine: aria2c ({current_slots} slots)  ·  Queue: {active_count} act, {queued_count} que, {done_eps} done  ·  Target: ~/Videos/anyplay"
            else:
                info_line = f"Net: {net_type} ({net_ssid})  ·  Slots: {current_slots}  ·  Queue: {active_count}/{total_eps}  ·  aria2c"
            safe_addstr(stdscr, 2, 1, info_line[:width - 2], curses.A_DIM)

            tab_row = 4
            table_hdr_row = 5

        # =========================================================
        # 2. DOWNLOAD CATEGORY TABS: All, Active, Completed
        # =========================================================
        # Filter episode list for display
        if active_filter == "active":
            filtered_eps = [e for e in EPISODES if e["status"] in ("DOWNLOADING", "RESOLVING", "PAUSED", "QUEUED")]
        elif active_filter == "completed":
            filtered_eps = [e for e in EPISODES if e["status"] in ("DONE", "COMPLETED", "SKIPPED")]
        else:
            filtered_eps = EPISODES

        # Clamp selected_idx if selected
        if selected_idx >= 0 and selected_idx >= len(filtered_eps):
            selected_idx = len(filtered_eps) - 1

        # Determine target episode for inspector / logs
        is_selected = (selected_idx >= 0 and len(filtered_eps) > 0)
        if is_selected:
            sel_ep = filtered_eps[selected_idx]
            is_active_fallback = False
        else:
            # Unselected: automatically track currently downloading/resolving episode
            downloading_ep = next((e for e in filtered_eps if e["status"] in ("DOWNLOADING", "RESOLVING")), None)
            if not downloading_ep:
                downloading_ep = next((e for e in EPISODES if e["status"] in ("DOWNLOADING", "RESOLVING")), None)
            sel_ep = downloading_ep if downloading_ep else (filtered_eps[0] if filtered_eps else None)
            is_active_fallback = True

        sel_ep_label = format_ep_display(sel_ep["ep"]) if sel_ep else "None"

        tab_counts = {
            "all": total_eps,
            "active": active_count + queued_count,
            "completed": done_eps,
        }

        safe_addstr(stdscr, tab_row, 0, " " * (width - 1), curses.A_NORMAL)
        for segment in build_filter_tab_segments(filter_list, active_filter, tab_counts, is_compact):
            t_attr = C_TAB_ACT if segment["active"] else C_TAB_INACT
            x = int(segment["x"])
            label = str(segment["label"])
            filter_hitboxes.append((x, x + len(label), str(segment["name"])))
            safe_addstr(stdscr, tab_row, x, label, t_attr)
            safe_addstr(stdscr, tab_row, x + len(label), " ", curses.A_NORMAL)


        # Available rows for content
        max_rows = download_content_row_count(height, table_hdr_row)
        content_signature = (
            bool(show_logs_view and not is_wide),
            active_filter,
            selected_idx,
            sel_ep.raw.id if sel_ep else None,
            table_hdr_row,
            height,
            width,
        )
        if last_content_signature is not None and content_signature != last_content_signature:
            _clear_content_area(stdscr, table_hdr_row, height - 1, width)
        last_content_signature = content_signature

        # =========================================================
        # 3. CONTENT AREA: LOGS VIEW (NARROW) vs TABLE VIEW
        # =========================================================
        if not is_wide and show_logs_view:
            # --- FULLSCREEN LOGS VIEW (NARROW WIDTH ONLY) ---
            mode_tag = "[ACTIVE]" if is_active_fallback else "[SELECTED]"
            hdr_text = f" LOGS: {sel_ep['title']} ({sel_ep_label}) {mode_tag} · Status: {sel_ep['status']} " if sel_ep else " LOGS: NONE "
            safe_addstr(stdscr, table_hdr_row, 0, hdr_text.ljust(width - 1)[:width - 1], C_HDR)

            visible_logs, range_text = build_log_panel_rows(sel_ep, width - 2, max_rows, log_scroll)
            if range_text:
                try:
                    start_text = range_text.split("/", 1)[0].split("-", 1)[0]
                    log_scroll = max(0, int(start_text) - 1)
                except (ValueError, IndexError):
                    pass
            for l_idx in range(max_rows):
                row_y = table_hdr_row + 1 + l_idx
                if row_y >= height - 1:
                    break
                l_line = visible_logs[l_idx] if l_idx < len(visible_logs) else ""
                attr = C_CYAN if "download" in l_line.lower() else (C_GREEN if "resolved" in l_line.lower() or "resumed" in l_line.lower() else curses.A_NORMAL)
                safe_addstr(stdscr, row_y, 1, format_log_cell(l_line, width - 2), attr)

            # Position indicator if logs exceed max_rows
            if range_text:
                pos_str = f" {range_text} "
                safe_addstr(stdscr, table_hdr_row, max(0, width - len(pos_str) - 2), pos_str, C_HDR)

        else:
            # --- DOWNLOAD TABLE VIEW ---
            if not is_wide:
                table_w = width - 1
                insp_w = 0
                insp_x = 0
            else:
                # 55/45 split for bottom section (download table 55%, logs inspector 45%)
                avail_w = max(20, width - 2)
                table_w = int(avail_w * 0.55)
                insp_x = table_w + 1
                insp_w = width - insp_x - 1

            table_layout = build_download_table_layout(table_w, filtered_eps)
            table_mode = table_layout["mode"]
            prefix_w = table_layout["prefix_w"]
            no_w = table_layout["no_w"]
            stat_w = table_layout["stat_w"]
            prog_hdr_w = table_layout["prog_hdr_w"]
            prog_hdr_text = table_layout["prog_hdr_text"]
            spd_hdr_w = table_layout["spd_hdr_w"]
            spd_hdr_text = table_layout["spd_hdr_text"]
            eta_w = table_layout["eta_w"]
            title_gap = table_layout["title_gap"]
            status_gap = table_layout["status_gap"]
            progress_gap = table_layout["progress_gap"]
            speed_gap = table_layout["speed_gap"]
            right_pad = table_layout["right_pad"]
            title_w = table_layout["title_w"]
            if table_mode == "compact":
                hdr_line = (
                    f" {'#':<{no_w}}{'TITLE':<{title_w}}{title_gap}"
                    f"{'STATUS':<{stat_w}}{status_gap}"
                    f"{prog_hdr_text:>{prog_hdr_w}}{right_pad}"
                )
            else:
                hdr_line = (
                    f"  {'#':<{no_w}}{'TITLE':<{title_w}}{title_gap}"
                    f"{'STATUS':<{stat_w}}{status_gap}"
                    f"{prog_hdr_text:>{prog_hdr_w}}{progress_gap}"
                    f"{spd_hdr_text:>{spd_hdr_w}}{speed_gap}"
                    f"{'ETA':>{eta_w}}{right_pad}"
                )

            safe_addstr(stdscr, table_hdr_row, 0, hdr_line[:table_w].ljust(table_w), C_HDR)

            # Viewport scrolling (only auto-follows when an item is selected)
            if selected_idx >= 0:
                if selected_idx < scroll_top:
                    scroll_top = selected_idx
                elif selected_idx >= scroll_top + max_rows:
                    scroll_top = selected_idx - max_rows + 1

            max_scroll = max(0, len(filtered_eps) - max_rows)
            scroll_top = max(0, min(max_scroll, scroll_top))

            visible_slice = filtered_eps[scroll_top : scroll_top + max_rows]
            visible_rows_count = len(visible_slice)
            for v_idx, ep in enumerate(visible_slice):
                abs_idx = scroll_top + v_idx
                row_y = table_hdr_row + 1 + v_idx
                is_sel = (abs_idx == selected_idx)
                row_attr = C_ROW_SEL if is_sel else curses.A_NORMAL

                # Status pill
                if stat_w <= 7:
                    if ep["status"] in ("DONE", "COMPLETED", "SKIPPED"):
                        st_text = "✔ DONE"
                        st_attr = C_GREEN if not is_sel else row_attr
                    elif ep["status"] == "DOWNLOADING":
                        st_text = "↓ ACT"
                        st_attr = C_CYAN if not is_sel else row_attr
                    elif ep["status"] == "RESOLVING":
                        st_text = "⚡ RES"
                        st_attr = C_MAG if not is_sel else row_attr
                    elif ep["status"] == "PAUSED":
                        st_text = "⏸ PAUS"
                        st_attr = C_YELLOW if not is_sel else row_attr
                    elif ep["status"] in ("FAILED", "CANCELLED", "STOPPED"):
                        st_text = "✖ STOP"
                        st_attr = C_RED if not is_sel else row_attr
                    else:
                        st_text = "… QUE"
                        st_attr = curses.A_DIM if not is_sel else row_attr
                elif stat_w == 8:
                    if ep["status"] in ("DONE", "COMPLETED", "SKIPPED"):
                        st_text = "✔ DONE"
                        st_attr = C_GREEN if not is_sel else row_attr
                    elif ep["status"] == "DOWNLOADING":
                        st_text = "↓ ACTIVE"
                        st_attr = C_CYAN if not is_sel else row_attr
                    elif ep["status"] == "RESOLVING":
                        st_text = "⚡ RESOLV"
                        st_attr = C_MAG if not is_sel else row_attr
                    elif ep["status"] == "PAUSED":
                        st_text = "⏸ PAUSED"
                        st_attr = C_YELLOW if not is_sel else row_attr
                    elif ep["status"] in ("FAILED", "CANCELLED", "STOPPED"):
                        st_text = "✖ STOP"
                        st_attr = C_RED if not is_sel else row_attr
                    else:
                        st_text = "… QUEUED"
                        st_attr = curses.A_DIM if not is_sel else row_attr
                else:
                    if ep["status"] in ("DONE", "COMPLETED", "SKIPPED"):
                        st_text = "✔ DONE"
                        st_attr = C_GREEN if not is_sel else row_attr
                    elif ep["status"] == "DOWNLOADING":
                        st_text = "↓ ACTIVE"
                        st_attr = C_CYAN if not is_sel else row_attr
                    elif ep["status"] == "RESOLVING":
                        st_text = "⚡ RESOLV"
                        st_attr = C_MAG if not is_sel else row_attr
                    elif ep["status"] == "PAUSED":
                        st_text = "⏸ PAUSED"
                        st_attr = C_YELLOW if not is_sel else row_attr
                    elif ep["status"] in ("FAILED", "CANCELLED", "STOPPED"):
                        st_text = "✖ STOPPED"
                        st_attr = C_RED if not is_sel else row_attr
                    else:
                        st_text = "… QUEUED"
                        st_attr = curses.A_DIM if not is_sel else row_attr

                st_col = f"{st_text[:stat_w]:<{stat_w}}"

                prog_str = f"{ep['progress']:4.0f}%" if table_mode == "compact" else f"{ep['progress']:5.1f}%"
                speed_col = ep["speed_str"] if ep["status"] == "DOWNLOADING" else "--"
                eta_col = ep["eta_str"] if ep["status"] == "DOWNLOADING" else "--"

                if prefix_w == 1:
                    prefix = ">" if is_sel else " "
                else:
                    prefix = " >" if is_sel else "  "

                s_no = abs_idx + 1
                s_no_col = f"{s_no:<{no_w}}"
                show_title_clean = format_table_title(ep['title'], ep['ep'], title_w)

                row_part1 = f"{prefix}{s_no_col}{show_title_clean}{title_gap}"
                safe_addstr(stdscr, row_y, 0, row_part1, row_attr)

                row_part2 = f"{st_col}{status_gap}"
                safe_addstr(stdscr, row_y, len(row_part1), row_part2, st_attr)

                prog_col = f"{prog_str:>{prog_hdr_w}}"
                if table_mode == "compact":
                    row_part3 = f"{prog_col}{right_pad}"
                else:
                    speed_col_fmt = f"{speed_col:>{spd_hdr_w}}"
                    eta_col_fmt = f"{eta_col:>{eta_w}}"
                    row_part3 = f"{prog_col}{progress_gap}{speed_col_fmt}{speed_gap}{eta_col_fmt}{right_pad}"

                rem_w = max(0, table_w - (len(row_part1) + len(row_part2)))
                row_part3 = row_part3.ljust(rem_w)[:rem_w]
                safe_addstr(stdscr, row_y, len(row_part1) + len(row_part2), row_part3, row_attr)

            # Right Side: Live Inspector & Logs (Wide View only)
            if is_wide:
                insp_x = table_w + 1
                insp_w = width - insp_x - 1

                mode_tag = "[ACTIVE]" if is_active_fallback else "[SELECTED]"
                insp_title = f" LOGS: {sel_ep_label} {mode_tag} "
                safe_addstr(stdscr, table_hdr_row, insp_x, insp_title.ljust(insp_w)[:insp_w], C_HDR)

                if sel_ep:
                    log_space = height - table_hdr_row - 2
                    insp_rows, range_text = build_log_panel_rows(sel_ep, insp_w, log_space)
                    if range_text:
                        pos_str = f" {range_text} "
                        safe_addstr(stdscr, table_hdr_row, max(insp_x, insp_x + insp_w - len(pos_str)), pos_str, C_HDR)

                    for r_i in range(log_space):
                        if table_hdr_row + 1 + r_i < height - 1:
                            l_txt = insp_rows[r_i] if r_i < len(insp_rows) else ""
                            safe_addstr(stdscr, table_hdr_row + 1 + r_i, insp_x, format_log_cell(l_txt, insp_w - 1))

        # =========================================================
        # 4. BOTTOM BAR: FOOTER
        # =========================================================
        footer_y = height - 1
        safe_addstr(stdscr, footer_y, 0, " " * (width - 1), curses.A_NORMAL)
        if auto_quit_pending:
            footer_text = " Auto-quit enabled: will quit when downloads finish   [q/Esc] Cancel auto-quit"
        elif auto_quit_blocked_message:
            footer_text = f" {auto_quit_blocked_message}   [r] Retry  [q] Quit"
        elif show_logs_view:
            footer_text = " [↑/↓] Scroll Logs   [← / Esc / q] Back to Downloads"
        elif not is_wide:
            footer_text = " [Space] Pause/Resume  [s] Stop  [r] Retry  [Enter] Logs  [Esc] Deselect  [q] Quit"
        else:
            footer_text = " [Space] Pause/Resume  [s] Stop  [r] Retry  [+/-] Slots  [a] Add Ep  [Esc] Deselect  [q] Quit"
        safe_addstr(stdscr, footer_y, 0, footer_text[:width - 1], curses.A_DIM)

        stdscr.refresh()

        # =========================================================
        # INPUT & INTERACTION (Keyboard, Mouse, Touch)
        # =========================================================
        def apply_scroll(delta):
            nonlocal selected_idx, log_scroll, scroll_top
            if show_logs_view:
                log_scroll = max(0, log_scroll + delta)
            elif filtered_eps:
                if selected_idx >= 0:
                    if delta < 0:
                        selected_idx = len(filtered_eps) - 1 if selected_idx == 0 else selected_idx - 1
                    else:
                        selected_idx = 0 if selected_idx == len(filtered_eps) - 1 else selected_idx + 1
                else:
                    max_scroll = max(0, len(filtered_eps) - max_rows)
                    scroll_top = max(0, min(max_scroll, scroll_top + delta))

        def apply_click(mx, my, is_double=False):
            nonlocal active_filter, selected_idx, scroll_top, log_scroll, show_logs_view
            nonlocal last_tap_time, last_tap_pos

            now = time.time()
            if not is_double:
                if (mx, my) == last_tap_pos and (now - last_tap_time) < 0.35:
                    is_double = True
            last_tap_time = now
            last_tap_pos = (mx, my)

            # 1. Tap on category filter pills (row tab_row)
            if my == tab_row:
                for start_x, end_x, f_name in filter_hitboxes:
                    if start_x <= mx < end_x:
                        if active_filter != f_name or show_logs_view:
                            active_filter = f_name
                            selected_idx = -1
                            scroll_top = 0
                            show_logs_view = False
                        return

            # 2. Tap on download rows in the table
            if (
                not show_logs_view
                and (table_hdr_row < my <= table_hdr_row + visible_rows_count)
                and mx < table_w
            ):
                row_offset = my - (table_hdr_row + 1)
                clicked_abs_idx = scroll_top + row_offset
                if 0 <= clicked_abs_idx < len(filtered_eps):
                    selected_idx = clicked_abs_idx
                    if is_double:
                        # Double-tap toggles pause/resume
                        cur_ep = filtered_eps[selected_idx]
                        if cur_ep["status"] in ("DOWNLOADING", "RESOLVING"):
                            worker.pause_item(cur_ep.raw.id)
                        elif cur_ep["status"] == "PAUSED":
                            worker.resume_item(cur_ep.raw.id)
                        elif cur_ep["status"] == "QUEUED":
                            worker.pause_item(cur_ep.raw.id)
                    else:
                        # Single-tap on narrow screen opens full logs view
                        if not is_wide:
                            show_logs_view = True
                            log_scroll = 0

        ch, resized_from_input = _read_input_key(stdscr)
        if resized_from_input:
            _handle_resize_event(stdscr)
            last_render_size = None
        if ch == -1:
            continue

        if ch == 27:
            next_ch = stdscr.getch()
            if next_ch in (ord('d'), ord('D')):
                return "BACK"

        if ch in (ord('q'), ord('Q')):
            if auto_quit_pending:
                auto_quit_pending = False
                auto_quit_blocked_message = ""
                stdscr.erase()
                last_render_size = None
                last_content_signature = None
                continue
            if show_logs_view:
                show_logs_view = False
            elif standalone:
                if active_count > 0 or queued_count > 0:
                    act = show_download_exit_dialog(stdscr, active_count, queued_count)
                    if act == "STOP_QUIT":
                        worker.stop()
                        return "QUIT"
                    elif act == "FINISH_QUIT":
                        if hasattr(worker, "resume_paused_items"):
                            worker.resume_paused_items()
                        auto_quit_pending = True
                        auto_quit_blocked_message = ""
                        stdscr.erase()
                        last_render_size = None
                        last_content_signature = None
                        continue
                    elif act == "CANCEL":
                        stdscr.erase()
                        last_render_size = None
                        last_content_signature = None
                        continue
                else:
                    return "QUIT"
            else:
                # Return to main CLI TUI without stopping background worker
                if active_count == 0 and queued_count == 0 and hasattr(worker, "dismiss_when_idle"):
                    worker.dismiss_when_idle()
                return "BACK"
        elif ch in (3, curses.KEY_CANCEL):  # Ctrl+C
            if active_count > 0 or queued_count > 0:
                act = show_download_exit_dialog(stdscr, active_count, queued_count)
                if act == "STOP_QUIT":
                    worker.stop()
                    return "QUIT"
                elif act == "FINISH_QUIT":
                    if hasattr(worker, "resume_paused_items"):
                        worker.resume_paused_items()
                    auto_quit_pending = True
                    auto_quit_blocked_message = ""
                    stdscr.erase()
                    last_render_size = None
                    last_content_signature = None
                    continue
                elif act == "CANCEL":
                    stdscr.erase()
                    last_render_size = None
                    last_content_signature = None
                    continue
            else:
                return "QUIT"
        elif ch in (ord('\t'), 9):  # Tab cycles forward: all -> active -> completed
            cur_fi = filter_list.index(active_filter) if active_filter in filter_list else 0
            active_filter = filter_list[(cur_fi + 1) % len(filter_list)]
            selected_idx = -1
            scroll_top = 0
            show_logs_view = False
        elif ch in (curses.KEY_BTAB, 353):  # Shift+Tab cycles backward: completed -> active -> all
            cur_fi = filter_list.index(active_filter) if active_filter in filter_list else 0
            active_filter = filter_list[(cur_fi - 1) % len(filter_list)]
            selected_idx = -1
            scroll_top = 0
            show_logs_view = False
        elif ch == ord('1'):
            if active_filter != "all" or show_logs_view:
                active_filter = "all"
                selected_idx = -1
                scroll_top = 0
                show_logs_view = False
        elif ch == ord('2'):
            if active_filter != "active" or show_logs_view:
                active_filter = "active"
                selected_idx = -1
                scroll_top = 0
                show_logs_view = False
        elif ch == ord('3'):
            if active_filter != "completed" or show_logs_view:
                active_filter = "completed"
                selected_idx = -1
                scroll_top = 0
                show_logs_view = False
        elif ch in (10, 13, ord('\n'), curses.KEY_RIGHT, ord('l'), ord('L')):
            # Enter or Right Arrow opens logs view on narrow screen
            if not is_wide and not show_logs_view and filtered_eps:
                show_logs_view = True
                log_scroll = 0
        elif ch in (curses.KEY_LEFT, ord('b'), ord('B')):
            # Left Arrow or 'b' goes back to table if viewing logs
            if show_logs_view:
                show_logs_view = False
        elif ch in (curses.KEY_UP, ord('k'), ord('K')):
            if show_logs_view:
                log_scroll = max(0, log_scroll - 1)
            elif filtered_eps:
                if selected_idx == -1:
                    selected_idx = len(filtered_eps) - 1
                elif selected_idx == 0:
                    selected_idx = len(filtered_eps) - 1
                else:
                    selected_idx -= 1
        elif ch in (curses.KEY_DOWN, ord('j'), ord('J')):
            if show_logs_view:
                log_scroll += 1
            elif filtered_eps:
                if selected_idx == -1:
                    selected_idx = 0
                elif selected_idx == len(filtered_eps) - 1:
                    selected_idx = 0
                else:
                    selected_idx += 1
        elif ch == curses.KEY_PPAGE:
            if show_logs_view:
                log_scroll = max(0, log_scroll - max_rows)
            elif filtered_eps:
                if selected_idx == -1:
                    selected_idx = 0
                else:
                    selected_idx = max(0, selected_idx - max_rows)
        elif ch == curses.KEY_NPAGE:
            if show_logs_view:
                log_scroll += max_rows
            elif filtered_eps:
                if selected_idx == -1:
                    selected_idx = len(filtered_eps) - 1
                else:
                    selected_idx = min(len(filtered_eps) - 1, selected_idx + max_rows)
        elif ch in (curses.KEY_HOME, ord('g')):
            if show_logs_view:
                log_scroll = 0
            elif filtered_eps:
                selected_idx = 0
        elif ch in (curses.KEY_END, ord('G')):
            if show_logs_view:
                log_scroll = 999999
            elif filtered_eps:
                selected_idx = len(filtered_eps) - 1
        elif ch == ord(' '):
            target_ep = filtered_eps[selected_idx] if (0 <= selected_idx < len(filtered_eps)) else sel_ep
            if target_ep:
                if target_ep["status"] in ("DOWNLOADING", "RESOLVING"):
                    worker.pause_item(target_ep.raw.id)
                elif target_ep["status"] == "PAUSED":
                    worker.resume_item(target_ep.raw.id)
                elif target_ep["status"] == "QUEUED":
                    worker.pause_item(target_ep.raw.id)
        elif ch in (ord('s'), ord('S')):
            target_ep = filtered_eps[selected_idx] if (0 <= selected_idx < len(filtered_eps)) else sel_ep
            if target_ep and target_ep["status"] in ("DOWNLOADING", "PAUSED", "QUEUED", "RESOLVING"):
                worker.cancel_item(target_ep.raw.id)
        elif ch in (ord('r'), ord('R')):
            target_ep = filtered_eps[selected_idx] if (0 <= selected_idx < len(filtered_eps)) else sel_ep
            if target_ep and target_ep["status"] in ("FAILED", "CANCELLED", "STOPPED"):
                worker.retry_item(target_ep.raw.id)
        elif ch in (ord('+'), ord('='), ord(']')):
            worker.set_max_concurrent(min(8, worker.max_concurrent + 1))
        elif ch in (ord('-'), ord('_'), ord('[')):
            worker.set_max_concurrent(max(1, worker.max_concurrent - 1))
        elif ch in (ord('a'), ord('A')):
            with worker.lock:
                next_ep_n = len(worker.items) + 1
            fn = os.path.join(worker.download_dir, f"Slime_S3_EP{next_ep_n:02d}.mp4")
            new_it = DownloadItem(
                id=f"ep-{next_ep_n}",
                title=SHOW_TITLE,
                episode=f"Episode {next_ep_n}",
                destination_path=fn,
                resolve_fn=make_ep_resolver(next_ep_n),
                engine="auto",
            )
            worker.add_item(new_it)
        elif ch == curses.KEY_MOUSE:
            try:
                _mid, mx, my, _mz, bstate = curses.getmouse()
            except curses.error:
                continue

            # Mouse wheel / swipe scroll
            is_wheel_up = bool(bstate & (
                getattr(curses, "BUTTON4_PRESSED", 0x10000)
                | getattr(curses, "BUTTON4_CLICKED", 0x20000)
            ))
            is_wheel_down = bool(bstate & (
                getattr(curses, "BUTTON5_PRESSED", 0x200000)
                | getattr(curses, "BUTTON5_CLICKED", 0x400000)
            ))

            if is_wheel_up:
                apply_scroll(-1)
            elif is_wheel_down:
                apply_scroll(1)
            else:
                is_click = bool(bstate & (
                    curses.BUTTON1_CLICKED
                    | curses.BUTTON1_PRESSED
                    | getattr(curses, "BUTTON1_DOUBLE_CLICKED", 0)
                ))
                is_dbl = bool(bstate & getattr(curses, "BUTTON1_DOUBLE_CLICKED", 0))
                if is_click:
                    apply_click(mx, my, is_double=is_dbl)

        elif ch == 27:
            # Standalone ESC key pressed: cancel auto-quit, deselect, or back from logs
            if auto_quit_pending:
                auto_quit_pending = False
                auto_quit_blocked_message = ""
                stdscr.erase()
                last_render_size = None
                last_content_signature = None
            elif show_logs_view:
                show_logs_view = False
            else:
                selected_idx = -1  # Deselect item / remove highlight

    return "BACK"


@terminal_application
def run_download_manager(worker: DownloadWorker | None = None, standalone: bool = False, auto_quit: bool = False) -> str:
    """Launches the Download Manager curses TUI.

    Returns:
        'BACK': User pressed 'q' to return to CLI TUI (downloads continue).
        'QUIT': User chose to quit application.
        'DONE': All downloads finished (standalone mode).
    """
    if worker is None:
        worker = get_global_worker()
    worker.start()
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
            sys.stdout.write(clear_images)
            sys.stdout.flush()
        res = curses.wrapper(_runner)
    except curses.error:
        if standalone:
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

    if not standalone:
        try:
            from .display import enter_alt_screen, set_alt_screen_active
            from .picker import invalidate_screen_buffer
            set_alt_screen_active(False)
            enter_alt_screen()
            invalidate_screen_buffer()
        except Exception:
            try:
                sys.stdout.write("\033[?1049h\033[2J\033[?25l")
                sys.stdout.flush()
            except Exception:
                pass

    return res or "BACK"


if __name__ == "__main__":
    w = create_test_worker()
    w.start()
    try:
        run_download_manager(w, standalone=True)
    finally:
        w.stop()
