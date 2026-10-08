"""Normalized torrent search models."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urlencode
import re


DEFAULT_TRACKERS = (
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://exodus.desync.com:6969/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
)


@dataclass(frozen=True)
class TorrentSearchConfig:
    limit: int = 50
    offset: int = 0
    min_seeders: int = 0
    hydrate_top: int = 20
    prefer_size: tuple[int, int] | None = None
    allow_unknown_seeders: bool = True


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def _int_config(value: Any, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return default


def _parse_size(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([kmgt]?i?b?)?", text, flags=re.I)
    if not match:
        return None
    amount = float(match.group(1))
    unit = (match.group(2) or "b").casefold()
    factor = {
        "b": 1,
        "k": 1024,
        "kb": 1024,
        "kib": 1024,
        "m": 1024**2,
        "mb": 1024**2,
        "mib": 1024**2,
        "g": 1024**3,
        "gb": 1024**3,
        "gib": 1024**3,
        "t": 1024**4,
        "tb": 1024**4,
        "tib": 1024**4,
    }.get(unit)
    if factor is None:
        return None
    return int(amount * factor)


def _parse_size_range(value: Any) -> tuple[int, int] | None:
    text = str(value or "").strip()
    if not text or text.casefold() in {"none", "off", "false", "0"}:
        return None
    parts = re.split(r"\s*(?::|-|,)\s*", text, maxsplit=1)
    if len(parts) != 2:
        return None
    lo = _parse_size(parts[0])
    hi = _parse_size(parts[1])
    if lo is None or hi is None:
        return None
    return (min(lo, hi), max(lo, hi))


def torrent_search_config_from_app_config(config: dict[str, Any] | None) -> TorrentSearchConfig:
    config = config or {}
    return TorrentSearchConfig(
        limit=_int_config(config.get("torrent_search_limit"), 50, minimum=1),
        min_seeders=_int_config(config.get("torrent_min_seeders"), 2, minimum=0),
        hydrate_top=_int_config(config.get("torrent_hydrate_top"), 5, minimum=0),
        prefer_size=_parse_size_range(config.get("torrent_prefer_size", "150MB:2500MB")),
        allow_unknown_seeders=_truthy(config.get("torrent_allow_unknown_seeders"), True),
    )


@dataclass(frozen=True)
class TorrentResult:
    source: str
    name: str
    infohash: str
    size_bytes: int = 0
    tsukihime_id: int = 0
    nyaa_id: int = 0
    nekobt_id: int = 0
    tt_id: int = 0
    seeders: int | None = None
    leechers: int | None = None
    completed: int | None = None
    file_count: int = 0
    group: str = ""
    episode: str = ""
    anime_title: str = ""
    anime_english_title: str = ""
    anilist_id: int = 0
    mal_id: int = 0
    audio_langs: tuple[str, ...] = ()
    sub_langs: tuple[str, ...] = ()
    tracker_urls: tuple[str, ...] = ()
    added_unix: int = 0
    raw: dict[str, Any] | None = None

    @property
    def magnet(self) -> str:
        trackers = self.tracker_urls or DEFAULT_TRACKERS
        params = [("xt", f"urn:btih:{self.infohash}"), ("dn", self.name)]
        params.extend(("tr", tracker) for tracker in trackers)
        return "magnet:?" + urlencode(params)

    def with_updates(self, **updates: Any) -> "TorrentResult":
        return replace(self, **updates)
