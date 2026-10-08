"""Torrent search parsing, merging, and ranking."""

from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree

from . import clients
from .models import TorrentResult, TorrentSearchConfig

_logger = logging.getLogger(__name__)

TsukihimeSearchFetcher = Callable[[str, int, int], list[dict[str, Any]]]
TsukihimeDetailFetcher = Callable[[int], dict[str, Any]]
CsvFetcher = Callable[[str, int, int], list[dict[str, Any]]]
NyaaFetcher = Callable[[str, int, int], str]
NYAA_NS = "{https://nyaa.si/xmlns/nyaa}"


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_langs(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if str(item or "").strip())


def _group_from_name(name: str) -> str:
    match = re.match(r"\[([^\]]+)\]", str(name or ""))
    return match.group(1) if match else ""


def _parse_size_text(value: Any) -> int:
    text = str(value or "").strip()
    match = re.fullmatch(r"([\d.]+)\s*([kmgt]?i?b?)", text, re.I)
    if not match:
        return 0
    amount = float(match.group(1))
    unit = match.group(2).lower()
    factors = {
        "b": 1,
        "kb": 1000,
        "kib": 1024,
        "mb": 1000**2,
        "mib": 1024**2,
        "gb": 1000**3,
        "gib": 1024**3,
        "tb": 1000**4,
        "tib": 1024**4,
    }
    return int(amount * factors.get(unit, 1))


def _nyaa_id_from_url(value: Any) -> int:
    match = re.search(r"/view/(\d+)", str(value or ""))
    return int(match.group(1)) if match else 0


def _unix_from_rss_date(value: Any) -> int:
    try:
        return int(parsedate_to_datetime(str(value or "")).timestamp())
    except (TypeError, ValueError, OverflowError):
        return 0


def parse_tsukihime_rows(rows: list[dict[str, Any]]) -> list[TorrentResult]:
    results: list[TorrentResult] = []
    for row in rows or []:
        infohash = str(row.get("btih") or "").strip().lower()
        name = str(row.get("name") or "").strip()
        if not infohash or not name:
            continue
        anime = row.get("anime") if isinstance(row.get("anime"), dict) else {}
        group = row.get("group") if isinstance(row.get("group"), dict) else {}
        results.append(
            TorrentResult(
                source="tsukihime",
                name=name,
                infohash=infohash,
                size_bytes=_as_int(row.get("totalsize")),
                tsukihime_id=_as_int(row.get("id")),
                nyaa_id=_as_int(row.get("nyaa_id")),
                nekobt_id=_as_int(row.get("nekobt_id")),
                tt_id=_as_int(row.get("tt_id")),
                file_count=_as_int(row.get("filecount")),
                group=str(group.get("name") or _group_from_name(name)),
                episode=str(row.get("episode_no") or ""),
                anime_title=str(anime.get("title") or ""),
                anime_english_title=str(anime.get("english_title") or ""),
                anilist_id=_as_int(anime.get("anilist")),
                mal_id=_as_int(anime.get("mal")),
                audio_langs=_as_langs(row.get("audiolangs")),
                sub_langs=_as_langs(row.get("sublangs")),
                added_unix=_as_int(row.get("added_date") or row.get("source_date")),
                raw=dict(row),
            )
        )
    return results


def parse_tsukihime_detail(result: TorrentResult, detail: dict[str, Any]) -> TorrentResult:
    trackers = detail.get("trackers") if isinstance(detail, dict) else []
    usable_trackers = [
        str(tracker.get("url") or "")
        for tracker in trackers or []
        if isinstance(tracker, dict) and tracker.get("url") and not tracker.get("error")
    ]
    seeders = [
        _as_int(tracker.get("seeders"))
        for tracker in trackers or []
        if isinstance(tracker, dict) and not tracker.get("error")
    ]
    leechers = [
        _as_int(tracker.get("leechers"))
        for tracker in trackers or []
        if isinstance(tracker, dict) and not tracker.get("error")
    ]
    completed = [
        _as_int(tracker.get("complete"))
        for tracker in trackers or []
        if isinstance(tracker, dict) and not tracker.get("error")
    ]
    files = detail.get("files") if isinstance(detail, dict) else []
    first_file = files[0] if files and isinstance(files[0], dict) else {}

    return result.with_updates(
        seeders=max(seeders) if seeders else result.seeders,
        leechers=max(leechers) if leechers else result.leechers,
        completed=max(completed) if completed else result.completed,
        tracker_urls=tuple(usable_trackers) or result.tracker_urls,
        audio_langs=result.audio_langs or _as_langs(first_file.get("audiolangs")),
        sub_langs=result.sub_langs or _as_langs(first_file.get("sublangs")),
    )


def parse_torrents_csv_rows(rows: list[dict[str, Any]]) -> list[TorrentResult]:
    results: list[TorrentResult] = []
    for row in rows or []:
        infohash = str(row.get("infohash") or "").strip().lower()
        name = str(row.get("name") or "").strip()
        if not infohash or not name:
            continue
        results.append(
            TorrentResult(
                source="torrents-csv",
                name=name,
                infohash=infohash,
                size_bytes=_as_int(row.get("size_bytes")),
                seeders=_as_optional_int(row.get("seeders")),
                leechers=_as_optional_int(row.get("leechers")),
                completed=_as_optional_int(row.get("completed")),
                group=_group_from_name(name),
                added_unix=_as_int(row.get("created_unix")),
                raw=dict(row),
            )
        )
    return results


def parse_nyaa_rss(xml_text: str) -> list[TorrentResult]:
    try:
        root = ElementTree.fromstring(xml_text or "")
    except ElementTree.ParseError:
        return []

    results: list[TorrentResult] = []
    for item in root.findall(".//item"):
        name = (item.findtext("title") or "").strip()
        infohash = (item.findtext(f"{NYAA_NS}infoHash") or "").strip().lower()
        if not name or not infohash:
            continue
        link = item.findtext("link") or item.findtext("guid") or ""
        results.append(
            TorrentResult(
                source="nyaa",
                name=name,
                infohash=infohash,
                size_bytes=_parse_size_text(item.findtext(f"{NYAA_NS}size")),
                nyaa_id=_nyaa_id_from_url(link),
                seeders=_as_optional_int(item.findtext(f"{NYAA_NS}seeders")),
                leechers=_as_optional_int(item.findtext(f"{NYAA_NS}leechers")),
                completed=_as_optional_int(item.findtext(f"{NYAA_NS}downloads")),
                group=_group_from_name(name),
                added_unix=_unix_from_rss_date(item.findtext("pubDate")),
                raw={"rss": True, "link": link},
            )
        )
    return results


def _merge_pair(existing: TorrentResult, item: TorrentResult) -> TorrentResult:
    sources = "+".join(sorted(set(existing.source.split("+")) | set(item.source.split("+"))))
    return existing.with_updates(
        source=sources,
        name=existing.name if len(existing.name) >= len(item.name) else item.name,
        size_bytes=existing.size_bytes or item.size_bytes,
        tsukihime_id=existing.tsukihime_id or item.tsukihime_id,
        nyaa_id=existing.nyaa_id or item.nyaa_id,
        nekobt_id=existing.nekobt_id or item.nekobt_id,
        tt_id=existing.tt_id or item.tt_id,
        seeders=max(value for value in (existing.seeders, item.seeders) if value is not None)
        if existing.seeders is not None or item.seeders is not None
        else None,
        leechers=max(value for value in (existing.leechers, item.leechers) if value is not None)
        if existing.leechers is not None or item.leechers is not None
        else None,
        completed=max(value for value in (existing.completed, item.completed) if value is not None)
        if existing.completed is not None or item.completed is not None
        else None,
        file_count=existing.file_count or item.file_count,
        group=existing.group or item.group,
        episode=existing.episode or item.episode,
        anime_title=existing.anime_title or item.anime_title,
        anime_english_title=existing.anime_english_title or item.anime_english_title,
        anilist_id=existing.anilist_id or item.anilist_id,
        mal_id=existing.mal_id or item.mal_id,
        audio_langs=existing.audio_langs or item.audio_langs,
        sub_langs=existing.sub_langs or item.sub_langs,
        tracker_urls=existing.tracker_urls or item.tracker_urls,
        added_unix=max(existing.added_unix, item.added_unix),
    )


def merge_results(items: list[TorrentResult]) -> list[TorrentResult]:
    merged: dict[str, TorrentResult] = {}
    for item in items:
        key = item.infohash.casefold()
        if key in merged:
            merged[key] = _merge_pair(merged[key], item)
        else:
            merged[key] = item
    return list(merged.values())


def rank_results(items: list[TorrentResult], config: TorrentSearchConfig) -> list[TorrentResult]:
    filtered: list[TorrentResult] = []
    for item in items:
        if item.seeders is None and config.min_seeders > 0 and not config.allow_unknown_seeders:
            continue
        if item.seeders is not None and item.seeders < config.min_seeders:
            continue
        filtered.append(item)

    def size_score(item: TorrentResult) -> int:
        if not config.prefer_size:
            return 0
        lo, hi = config.prefer_size
        return 1 if lo <= item.size_bytes <= hi else 0

    def metadata_score(item: TorrentResult) -> int:
        return sum(
            1
            for value in (
                item.group,
                item.episode,
                item.audio_langs,
                item.sub_langs,
                item.anime_title or item.anime_english_title,
            )
            if value
        )

    def streamability_score(item: TorrentResult) -> int:
        if item.file_count == 1:
            return 2
        if item.file_count <= 0:
            return 1
        return 0

    filtered.sort(
        key=lambda item: (
            streamability_score(item),
            metadata_score(item),
            size_score(item),
            -item.size_bytes,
            item.seeders if item.seeders is not None else -1,
            item.completed or 0,
            item.added_unix,
        ),
        reverse=True,
    )
    return filtered


def _hydrate_tsukihime(
    items: list[TorrentResult],
    *,
    config: TorrentSearchConfig,
    fetch_tsukihime_detail: TsukihimeDetailFetcher,
) -> list[TorrentResult]:
    if config.hydrate_top <= 0:
        return items

    hydrated: list[TorrentResult] = list(items)
    targets = [
        (index, item)
        for index, item in enumerate(items)
        if item.tsukihime_id
    ][: config.hydrate_top]
    if not targets:
        return hydrated

    def hydrate(target: tuple[int, TorrentResult]) -> tuple[int, TorrentResult]:
        index, item = target
        detail = fetch_tsukihime_detail(item.tsukihime_id)
        return index, parse_tsukihime_detail(item, detail)

    max_workers = min(6, len(targets))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for index, result in executor.map(hydrate, targets):
            hydrated[index] = result
    return hydrated


def _fetch_search_sources(
    query: str,
    cfg: TorrentSearchConfig,
    *,
    fetch_tsukihime_search: TsukihimeSearchFetcher,
    fetch_torrents_csv: CsvFetcher,
    fetch_nyaa_rss: NyaaFetcher,
) -> tuple[list[TorrentResult], list[TorrentResult], list[TorrentResult]]:
    def parse_future(label: str, future, parser, default):
        try:
            return parser(future.result())
        except Exception as exc:
            _logger.debug("Torrent %s search failed for query=%r: %s", label, query, exc, exc_info=True)
            return default

    with ThreadPoolExecutor(max_workers=3) as executor:
        tsukihime_future = executor.submit(fetch_tsukihime_search, query, cfg.limit, cfg.offset)
        csv_future = executor.submit(fetch_torrents_csv, query, cfg.limit, cfg.offset)
        nyaa_future = executor.submit(fetch_nyaa_rss, query, cfg.limit, cfg.offset)
        tsukihime = parse_future("tsukihime", tsukihime_future, parse_tsukihime_rows, [])
        csv = parse_future("torrents-csv", csv_future, parse_torrents_csv_rows, [])
        nyaa = parse_future("nyaa", nyaa_future, parse_nyaa_rss, [])
    return tsukihime, csv, nyaa


def search_anime_torrents(
    query: str,
    *,
    config: TorrentSearchConfig | None = None,
    fetch_tsukihime_search: TsukihimeSearchFetcher = clients.fetch_tsukihime_search,
    fetch_tsukihime_detail: TsukihimeDetailFetcher = clients.fetch_tsukihime_detail,
    fetch_torrents_csv: CsvFetcher = clients.fetch_torrents_csv,
    fetch_nyaa_rss: NyaaFetcher = clients.fetch_nyaa_rss,
) -> list[TorrentResult]:
    cfg = config or TorrentSearchConfig()
    tsukihime, csv, nyaa = _fetch_search_sources(
        query,
        cfg,
        fetch_tsukihime_search=fetch_tsukihime_search,
        fetch_torrents_csv=fetch_torrents_csv,
        fetch_nyaa_rss=fetch_nyaa_rss,
    )
    tsukihime = _hydrate_tsukihime(tsukihime, config=cfg, fetch_tsukihime_detail=fetch_tsukihime_detail)
    return rank_results(merge_results(tsukihime + csv + nyaa), cfg)


def search_general_torrents(
    query: str,
    *,
    config: TorrentSearchConfig | None = None,
    fetch_torrents_csv: CsvFetcher = clients.fetch_torrents_csv,
    fetch_tsukihime_search: TsukihimeSearchFetcher = clients.fetch_tsukihime_search,
    fetch_tsukihime_detail: TsukihimeDetailFetcher = clients.fetch_tsukihime_detail,
    fetch_nyaa_rss: NyaaFetcher = clients.fetch_nyaa_rss,
) -> list[TorrentResult]:
    cfg = config or TorrentSearchConfig()
    tsukihime, csv, nyaa = _fetch_search_sources(
        query,
        cfg,
        fetch_tsukihime_search=fetch_tsukihime_search,
        fetch_torrents_csv=fetch_torrents_csv,
        fetch_nyaa_rss=fetch_nyaa_rss,
    )
    tsukihime = _hydrate_tsukihime(tsukihime, config=cfg, fetch_tsukihime_detail=fetch_tsukihime_detail)
    return rank_results(merge_results(csv + tsukihime + nyaa), cfg)
