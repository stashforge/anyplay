"""AniList list sorting helpers."""


ANILIST_SORT_MODES = ("recent", "anilist", "title", "progress")
ANILIST_SORT_LABELS = {
    "recent": "Recent",
    "anilist": "List Order",
    "title": "Title",
    "progress": "Progress",
}


def normalize_anilist_sort_mode(mode):
    mode = str(mode or "").strip().casefold()
    return mode if mode in ANILIST_SORT_MODES else "recent"


def next_anilist_sort_mode(mode):
    mode = normalize_anilist_sort_mode(mode)
    index = ANILIST_SORT_MODES.index(mode)
    return ANILIST_SORT_MODES[(index + 1) % len(ANILIST_SORT_MODES)]


def previous_anilist_sort_mode(mode):
    mode = normalize_anilist_sort_mode(mode)
    index = ANILIST_SORT_MODES.index(mode)
    return ANILIST_SORT_MODES[(index - 1) % len(ANILIST_SORT_MODES)]


def _sort_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _anilist_media_id(show):
    media_id = (show or {}).get("_anilist_id")
    if media_id:
        return media_id
    media_id = (show or {}).get("aniListId")
    if media_id:
        return media_id
    candidate = (show or {}).get("_id")
    return candidate if str(candidate or "").isdigit() else None


def anilist_history_timestamps(history):
    timestamps = {}
    for entry in history or []:
        media_id = _anilist_media_id(entry.get("show") or {})
        if not media_id:
            continue
        key = str(media_id)
        timestamp = _sort_int(entry.get("timestamp"))
        timestamps[key] = max(timestamps.get(key, 0), timestamp)
    return timestamps


def sort_anilist_shows(shows, mode, history=None):
    items = list(shows or [])
    mode = normalize_anilist_sort_mode(mode)
    if mode == "anilist":
        return items
    if mode == "title":
        return sorted(
            items,
            key=lambda show: str(
                show.get("name") or show.get("englishName") or ""
            ).casefold(),
        )
    if mode == "progress":
        return sorted(
            items,
            key=lambda show: -_sort_int(show.get("_anilist_progress")),
        )

    local_timestamps = anilist_history_timestamps(history)
    return sorted(
        items,
        key=lambda show: -max(
            local_timestamps.get(str(_anilist_media_id(show) or ""), 0),
            _sort_int(show.get("_anilist_updated_at")),
        ),
    )


def anilist_sort_label(mode, reverse=False):
    mode = normalize_anilist_sort_mode(mode)
    base = ANILIST_SORT_LABELS[mode]
    if mode == "title":
        return f"{base} Z-A" if reverse else f"{base} A-Z"
    if mode == "anilist":
        return f"{base} Reverse" if reverse else base
    return f"{base} ↑" if reverse else f"{base} ↓"
