"""AniList airing schedule grouping and row formatting."""

from __future__ import annotations

import datetime as _dt

AIRING_TABS = ("today", "tomorrow", "week")
AIRING_TAB_LABELS = {
    "today": "Today",
    "tomorrow": "Tomorrow",
    "week": "Next 5 Days",
}
DIM = "\033[38;5;139m"
RESET = "\033[0m"


def normalize_airing_tab(tab):
    tab = str(tab or "").strip().casefold()
    return tab if tab in AIRING_TABS else "today"


def next_airing_tab(tab):
    tab = normalize_airing_tab(tab)
    index = AIRING_TABS.index(tab)
    return AIRING_TABS[(index + 1) % len(AIRING_TABS)]


def previous_airing_tab(tab):
    tab = normalize_airing_tab(tab)
    index = AIRING_TABS.index(tab)
    return AIRING_TABS[(index - 1) % len(AIRING_TABS)]


def airing_tab_label(tab):
    return AIRING_TAB_LABELS[normalize_airing_tab(tab)]


def _local_datetime(timestamp):
    return _dt.datetime.fromtimestamp(int(float(timestamp))).astimezone()


def _start_of_day(value):
    return value.replace(hour=0, minute=0, second=0, microsecond=0)


def _airing_timestamp(show):
    try:
        return int(float((show or {}).get("_next_airing_at")))
    except (TypeError, ValueError):
        return None


def _coerce_now(now):
    if now is None:
        return _dt.datetime.now().astimezone()
    if isinstance(now, _dt.datetime):
        return now.astimezone()
    return _local_datetime(now)


def _airing_day_label(value, now):
    today = _start_of_day(now)
    day = _start_of_day(value)
    if day == today:
        prefix = "Today"
        date_text = value.strftime("%a, %d %b")
    elif day == today + _dt.timedelta(days=1):
        prefix = "Tomorrow"
        date_text = value.strftime("%a, %d %b")
    else:
        prefix = value.strftime("%A")
        date_text = value.strftime("%d %b")
    return f"{prefix} · {date_text}"


def _row_time(value, now):
    if value <= now:
        return "aired"
    return value.strftime("%H:%M")


def filter_airing_shows(shows, tab="today", now=None):
    tab = normalize_airing_tab(tab)
    now = _coerce_now(now)
    today = _start_of_day(now)
    tomorrow = today + _dt.timedelta(days=1)
    next_five_end = today + _dt.timedelta(days=7)
    if tab == "today":
        start, end = today, tomorrow
    elif tab == "tomorrow":
        start, end = tomorrow, tomorrow + _dt.timedelta(days=1)
    else:
        start, end = tomorrow + _dt.timedelta(days=1), next_five_end

    entries = []
    for show in shows or []:
        timestamp = _airing_timestamp(show)
        if timestamp is None:
            continue
        value = _local_datetime(timestamp)
        if start <= value < end:
            entries.append((value, show))
    return [show for value, show in sorted(entries, key=lambda item: item[0])]


def airing_row_label(show, *, tab="today", now=None, day_label_width=22):
    tab = normalize_airing_tab(tab)
    now = _coerce_now(now)
    timestamp = _airing_timestamp(show)
    if timestamp is None:
        time_text = "--:--"
    else:
        value = _local_datetime(timestamp)
        time_text = _row_time(value, now)
        _airing_day_label(value, now)

    ep = show.get("_next_airing_ep") or "?"
    title = show.get("name") or show.get("englishName") or "Unknown"
    if tab == "week":
        return f"  {time_text:>5}  EP {ep:<3} {title}"
    return f"{time_text:>5}  EP {ep:<3} {title}"


def airing_rows(shows, tab="today", now=None):
    tab = normalize_airing_tab(tab)
    filtered = filter_airing_shows(shows, tab, now)
    if tab == "week":
        rows = []
        groups = []
        current_day = None
        current_items = []
        for show in filtered:
            timestamp = _airing_timestamp(show)
            day = _local_datetime(timestamp).date() if timestamp is not None else None
            if current_day is not None and day != current_day:
                groups.append((current_day, current_items))
                current_items = []
            current_day = day
            current_items.append(show)
        if current_day is not None:
            groups.append((current_day, current_items))

        for group_index, (_day, items) in enumerate(groups):
            for show in reversed(items):
                rows.append((show, airing_row_label(show, tab=tab, now=now)))
            timestamp = _airing_timestamp(items[0]) if items else None
            if timestamp is not None:
                day_label = _airing_day_label(
                    _local_datetime(timestamp), _coerce_now(now)
                )
                rows.append((None, f" {DIM}{day_label}{RESET}"))
        return rows

    rows = []
    last_day = None
    for show in filtered:
        timestamp = _airing_timestamp(show)
        day = _local_datetime(timestamp).date() if timestamp is not None else None
        if tab == "week" and day != last_day:
            day_label = _airing_day_label(_local_datetime(timestamp), _coerce_now(now))
            rows.append((None, f"{DIM}{day_label}{RESET}"))
        label = airing_row_label(show, tab=tab, now=now)
        rows.append((show, label))
        last_day = day
    return rows
