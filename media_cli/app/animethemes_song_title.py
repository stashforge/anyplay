"""Human-readable player titles from the selected AnimeThemes video entry."""

import re

from ..core.terminal import sanitize_terminal_text
from ..services.animethemes import _artist_names, _video_flags


def _text(value):
    return str(value or "").strip()


def song_credit(song, artists):
    title = _text(song)
    if title and _text(artists):
        title += " by " + _text(artists)
    return sanitize_terminal_text(title)


def theme_result_lines(theme: dict, *, artist="", extras=()) -> tuple[str, str, str]:
    anime = theme.get("anime") or {}
    anime_name = _text(anime.get("name")) if isinstance(anime, dict) else _text(anime)
    song = theme.get("song") or {}
    title = _text(song.get("title")) or "Unknown song"
    alternate = _text(song.get("alternative_title"))
    if not alternate or alternate.casefold() == title.casefold():
        alternate = "No alternative title"
    artists = _artist_names(song) or _text(artist)
    label, version, _ = _theme_details({"raw": {"theme": theme}})
    if version > 1:
        label += f" v{version}"
    entries = theme.get("animethemeentries") or []
    ranges = {_text(entry.get("episodes")) for entry in entries}
    episodes = next(iter(ranges)) if len(ranges) == 1 and "" not in ranges else ""
    metadata = " · ".join(part for part in (
        "by " + artists if artists else "", anime_name, label, f"EP {episodes}" if episodes else "", *extras,
    ) if part)
    return (sanitize_terminal_text(title), sanitize_terminal_text(alternate),
            sanitize_terminal_text(metadata))


def _theme_details(row):
    raw = row.get("raw") or {}
    entry = raw.get("entry") or {}
    theme = raw.get("theme") or {}
    label = _text(row.get("theme") or theme.get("slug") or (
        _text(theme.get("type")) + _text(theme.get("sequence"))))
    match = re.fullmatch(r"(OP|ED)(\d+)?(?:v(\d+))?", label, re.IGNORECASE)
    if match:
        label = " ".join(part for part in (match[1].upper(), match[2]) if part)
    version = entry.get("version") or (match[3] if match else None)
    try:
        version = int(version or 1)
    except (TypeError, ValueError):
        version = 1
    return label, version, _text(entry.get("episodes") or row.get("episodes"))


def song_player_title(row: dict) -> str:
    label, version, episodes = _theme_details(row)
    if version > 1:
        label += f" v{version}"
    title = song_credit(row.get("song"), row.get("artists")) or "Unknown song"
    context = " ".join(part for part in (_text(row.get("anime")), label) if part)
    if context:
        title += " - " + context
    if episodes:
        title += f" (EP {episodes})"
    return sanitize_terminal_text(title)


def song_player_lines(row: dict) -> tuple[str, str]:
    label, version, episodes = _theme_details(row)
    metadata = " • ".join(part for part in (
        label, "Original" if version == 1 else f"Version {version}",
        f"EP {episodes}" if episodes else "",
    ) if part)
    stream = row.get("stream") or {}
    resolution = _text(stream.get("resolution"))
    if resolution.isdecimal():
        resolution += "p"
    raw = row.get("raw") or {}
    video = {"source": stream.get("source"), **(raw.get("video") or {})}
    parts = []
    for part in (resolution, *_video_flags(video, raw.get("entry") or {})):
        if part and part.lower() != "unknown" and part not in parts:
            parts.append(part)
    return sanitize_terminal_text(metadata), sanitize_terminal_text(" • ".join(parts))
