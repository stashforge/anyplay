"""Episode-level torrent fallback helpers."""

from __future__ import annotations

import copy
import logging
import re
from collections.abc import Callable
from typing import Any

from media_cli.domain.matching import extract_franchise_query

from .models import TorrentResult, TorrentSearchConfig, torrent_search_config_from_app_config
from .search import merge_results, rank_results, search_anime_torrents
from .sources import build_torrent_source

TorrentSearchFn = Callable[..., list[TorrentResult]]
_logger = logging.getLogger(__name__)
_fallback_results_cache: dict[tuple, list[TorrentResult]] = {}


def _lang_base(value: str) -> str:
    return str(value or "").strip().lower().split("-", 1)[0]


def _has_lang(langs: tuple[str, ...], *needles: str) -> bool:
    normalized = {_lang_base(lang) for lang in langs}
    wanted = {_lang_base(needle) for needle in needles}
    return bool(normalized.intersection(wanted))


def _torrent_mode_rank(result: TorrentResult, ttype: str) -> int:
    """Rank mode fit. Lower is better; unknown stays usable but lower."""
    mode = str(ttype or "sub").lower()
    text = f"{result.name} {result.group}".lower()
    audio = tuple(result.audio_langs or ())
    subs = tuple(result.sub_langs or ())

    has_en_audio = _has_lang(audio, "en", "eng") or bool(
        re.search(r"\b(?:english\s+dub|eng(?:lish)?\s+audio)\b", text)
    )
    has_ja_audio = _has_lang(audio, "ja", "jpn", "jp") or "japanese" in text
    has_audio_metadata = bool(audio) or has_en_audio or has_ja_audio
    has_sub_metadata = bool(subs) or any(token in text for token in ("sub", "subs", "hardsub", "softsub"))
    is_multi_audio = len(audio) > 1 or any(token in text for token in ("dual-audio", "dual audio", "multi-audio", "multi audio"))

    if mode == "dub":
        if has_en_audio and not is_multi_audio:
            return 0
        if has_en_audio or is_multi_audio:
            return 1
        if not has_audio_metadata:
            return 2
        return 3

    if has_ja_audio and has_sub_metadata:
        return 0
    if is_multi_audio and has_sub_metadata:
        return 1
    if not has_audio_metadata:
        return 2
    return 3


def _rank_for_translation_mode(results: list[TorrentResult], ttype: str) -> list[TorrentResult]:
    return sorted(results, key=lambda result: (_torrent_mode_rank(result, ttype), result.size_bytes or 10**30))


def _title_candidates(show: dict[str, Any] | None) -> list[str]:
    if not isinstance(show, dict):
        return []

    raw_titles: list[str] = []
    for key in ("englishName", "name", "romajiName", "nativeName"):
        value = str(show.get(key) or "").strip()
        if value:
            raw_titles.append(value)
    for value in show.get("altNames") or []:
        if str(value or "").strip():
            raw_titles.append(str(value).strip())

    titles: list[str] = []
    for value in raw_titles:
        franchise = _clean_title_candidate(extract_franchise_query(value))
        if franchise:
            titles.append(franchise)
    franchise = _clean_title_candidate(show.get("_franchise"))
    if franchise:
        titles.append(franchise)
    titles.extend(raw_titles)

    unique: list[str] = []
    seen = set()
    for title in titles:
        title = _clean_title_candidate(title)
        if not title:
            continue
        key = title.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(title)
    return unique[:4]


def _clean_title_candidate(value: Any) -> str:
    title = str(value or "").strip()
    title = re.sub(r"(?i)\s+(?:&|and)\s*\d+\s*$", "", title).strip()
    if len(title) < 3:
        return ""
    return title


def _episode_number(label: Any) -> str:
    text = str(label or "").strip()
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return text
    value = match.group(0)
    if value.endswith(".0"):
        value = value[:-2]
    return value


def _season_number(show: dict[str, Any] | None) -> int:
    if not isinstance(show, dict):
        return 0
    raw = show.get("_season") or show.get("seasonNumber")
    if isinstance(raw, dict):
        raw = raw.get("number")
    try:
        season = int(raw or 0)
    except (TypeError, ValueError):
        season = 0
    return season if season > 0 else 0


def _episode_candidates(show: dict[str, Any] | None, episode_label: Any, episode_id: Any = None) -> list[str]:
    candidates: list[str] = []
    if isinstance(show, dict) and episode_id is not None:
        episodes_map = show.get("_episodes_map") or {}
        by_ttype = show.get("_episodes_map_by_ttype") or {}
        entry = episodes_map.get(str(episode_id))
        if not entry:
            for mapping in by_ttype.values():
                if isinstance(mapping, dict) and str(episode_id) in mapping:
                    entry = mapping[str(episode_id)]
                    break
        if isinstance(entry, dict):
            for key in ("franchise_number", "display_number", "number", "label"):
                value = entry.get(key)
                num = _episode_number(value)
                if num:
                    candidates.append(num)

    label_num = _episode_number(episode_label)
    if label_num:
        candidates.append(label_num)

    unique: list[str] = []
    seen = set()
    for value in candidates:
        key = str(value).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(str(value))
    return unique


def build_episode_torrent_queries(
    show: dict[str, Any] | None,
    episode_label: Any,
    *,
    episode_id: Any = None,
) -> list[str]:
    """Build torrent queries for a show episode, most specific first."""
    titles = _title_candidates(show)
    episodes = _episode_candidates(show, episode_label, episode_id)
    if not titles or not episodes:
        return []

    queries: list[str] = []
    season = _season_number(show)
    for episode in episodes:
        if season > 0 and episode.replace(".", "", 1).isdigit():
            ep_int = int(float(episode))
            for title in titles:
                queries.append(f"{title} S{season:02d}E{ep_int:02d}")
    for episode in episodes:
        for title in titles:
            queries.append(f"{title} {episode}")

    unique: list[str] = []
    seen = set()
    for query in queries:
        key = query.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(query)
    return unique


def augment_episode_data_with_torrents(
    ep_data: dict[str, Any] | None,
    *,
    show: dict[str, Any] | None,
    episode_label: Any,
    episode_id: Any = None,
    ttype: str = "sub",
    max_results: int = 12,
    search_fn: TorrentSearchFn = search_anime_torrents,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any] | None:
    """Append torrent mirrors to provider episode data.

    Search failures are intentionally non-fatal so a torrent API outage does
    not break provider playback.
    """
    if not isinstance(ep_data, dict) or ep_data.get("is_local"):
        return ep_data
    if cancel_check and cancel_check():
        _logger.debug("Torrent fallback cancelled before query build")
        return ep_data
    if ep_data.get("_torrent_fallback_checked"):
        return ep_data

    queries = build_episode_torrent_queries(show, episode_label, episode_id=episode_id)
    if not queries:
        _logger.debug("Torrent fallback skipped: no query for show=%r episode=%r", show, episode_label)
        return ep_data

    out = copy.deepcopy(ep_data)
    out["_torrent_fallback_checked"] = True
    episode = dict(out.get("episode") or {})
    sources = list(episode.get("sourceUrls") or [])
    existing_hashes = {
        str((src.get("torrent") or {}).get("infohash") or "").casefold()
        for src in sources
        if isinstance(src, dict)
    }

    try:
        from media_cli.core.storage import load_config
        config = torrent_search_config_from_app_config(load_config())
    except Exception:
        config = TorrentSearchConfig(
            limit=50,
            min_seeders=2,
            hydrate_top=5,
            prefer_size=(150 * 1024**2, 2500 * 1024**2),
            allow_unknown_seeders=True,
        )
    cache_key = (
        id(search_fn),
        tuple(queries),
        config.limit,
        config.min_seeders,
        config.hydrate_top,
        config.prefer_size,
        config.allow_unknown_seeders,
    )
    results = _fallback_results_cache.get(cache_key)
    if results is None:
        results = []
        for query in queries:
            if cancel_check and cancel_check():
                _logger.debug("Torrent fallback cancelled before search query=%r", query)
                return ep_data
            try:
                _logger.debug("Torrent fallback searching query=%r", query)
                query_results = list(search_fn(query, config=config) or [])
            except Exception as exc:
                _logger.debug("Torrent fallback search failed query=%r: %s", query, exc, exc_info=True)
                query_results = []
            if cancel_check and cancel_check():
                _logger.debug("Torrent fallback cancelled after search query=%r", query)
                return ep_data
            if query_results:
                _logger.debug("Torrent fallback found %d result(s) query=%r", len(query_results), query)
                results = rank_results(merge_results(results + query_results), config)
            if len(_rank_for_translation_mode(results, ttype)) >= max_results:
                break
        _fallback_results_cache[cache_key] = list(results)
    if not results:
        _logger.debug("Torrent fallback found no results for queries=%r", queries)

    mode_results = _rank_for_translation_mode(results, ttype)
    for result in mode_results[:max_results]:
        if result.infohash.casefold() in existing_hashes:
            continue
        source = build_torrent_source(result)
        source["priority"] = 80
        sources.append(source)
        existing_hashes.add(result.infohash.casefold())

    episode["sourceUrls"] = sources
    out["episode"] = episode
    return out
