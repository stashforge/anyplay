"""Normalized provider data builders.

Providers can fetch data however they need, but app-facing data should use one
stable shape.  These builders are intentionally pure: no network calls, no
local state, and no UI decisions.
"""

from __future__ import annotations

from typing import Any


def _clean_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _clean_list(values: Any) -> list:
    if not values:
        return []
    if isinstance(values, str):
        values = [part.strip() for part in values.split(",")]
    return [value for value in values if str(value).strip()]


def _clean_available(value: Any) -> int | str:
    try:
        number = int(value)
        return max(0, number)
    except (TypeError, ValueError):
        text = _clean_text(value)
        return text if text else 0


def build_title(
    *,
    provider: str,
    provider_name: str,
    provider_id: Any,
    name: Any = "",
    english_name: Any = "",
    romaji_name: Any = "",
    native_name: Any = "",
    alt_names: Any = None,
    thumbnail: Any = "",
    banner: Any = "",
    description: Any = "",
    media_type: Any = None,
    media_format: Any = None,
    status: Any = None,
    season: dict | None = None,
    aired_start: Any = None,
    aired_end: Any = None,
    start_date: Any = None,
    end_date: Any = None,
    episode_count: Any = None,
    available_sub: Any = 0,
    available_dub: Any = 0,
    available_raw: Any = 0,
    available_detail: dict | None = None,
    score: Any = None,
    genres: Any = None,
    tags: Any = None,
    anilist_id: Any = None,
    mal_id: Any = None,
    extra: dict | None = None,
) -> dict[str, Any]:
    source_id = _clean_text(provider_id)
    title = {
        "_id": source_id,
        "id": source_id,
        "name": _clean_text(name),
        "englishName": _clean_text(english_name),
        "romajiName": _clean_text(romaji_name),
        "nativeName": _clean_text(native_name),
        "altNames": _clean_list(alt_names),
        "thumbnail": _clean_text(thumbnail),
        "banner": _clean_text(banner),
        "description": _clean_text(description),
        "type": media_type,
        "format": media_format,
        "status": status,
        "season": dict(season or {}),
        "airedStart": aired_start,
        "airedEnd": aired_end,
        "startDate": start_date,
        "endDate": end_date,
        "episodeCount": episode_count,
        "availableEpisodes": {
            "sub": _clean_available(available_sub),
            "dub": _clean_available(available_dub),
            "raw": _clean_available(available_raw),
        },
        "availableEpisodesDetail": {
            "sub": [],
            "dub": [],
            "raw": [],
        },
        "score": score,
        "genres": _clean_list(genres),
        "tags": _clean_list(tags),
        "aniListId": _clean_text(anilist_id) or None,
        "malId": _clean_text(mal_id) or None,
        "_provider": _clean_text(provider),
        "_provider_id": source_id,
        "_provider_name": _clean_text(provider_name),
    }

    if available_detail:
        detail = dict(title["availableEpisodesDetail"])
        for key in ("sub", "dub", "raw"):
            detail[key] = [str(item) for item in available_detail.get(key) or []]
        title["availableEpisodesDetail"] = detail

    for key, value in dict(extra or {}).items():
        title[key] = value
    return title


def build_episode(
    *,
    episode_id: Any,
    label: Any = "",
    title: Any = "",
    url: Any = "",
    translation_type: str = "sub",
    date: Any = None,
    duration: Any = None,
    extra: dict | None = None,
) -> dict[str, Any]:
    source_id = _clean_text(episode_id)
    episode = {
        "id": source_id,
        "label": _clean_text(label) or source_id,
        "title": _clean_text(title),
        "url": _clean_text(url),
        "translationType": _clean_text(translation_type) or "sub",
        "date": date,
        "duration": duration,
    }
    for key, value in dict(extra or {}).items():
        episode[key] = value
    return episode


def _normalize_episode_item(item: Any, translation_type: str) -> dict[str, Any]:
    if isinstance(item, dict):
        return build_episode(
            episode_id=item.get("id") or item.get("episode") or item.get("url"),
            label=item.get("label") or item.get("episode") or item.get("id"),
            title=item.get("title") or "",
            url=item.get("url") or "",
            translation_type=item.get("translationType") or translation_type,
            date=item.get("date"),
            duration=item.get("duration"),
            extra={
                key: value
                for key, value in item.items()
                if key not in {"id", "episode", "label", "title", "url", "translationType", "date", "duration"}
            },
        )
    return build_episode(
        episode_id=item,
        label=item,
        translation_type=translation_type,
    )


def build_catalog(
    *,
    episodes: dict | None = None,
    state: str = "loaded",
    error: str = "",
    provider: str = "",
    provider_id: Any = "",
    translation_type: str = "sub",
) -> dict[str, Any]:
    from ...domain.episodes import build_episode_map

    episodes_map_by_type = {}
    by_number_by_type = {}
    detail = {}

    for key in ("sub", "dub", "raw"):
        raw_items = [
            _normalize_episode_item(item, key)
            for item in (episodes or {}).get(key, [])
        ]
        ep_map, num_map = build_episode_map(raw_items, translation_type=key)
        episodes_map_by_type[key] = ep_map
        by_number_by_type[key] = num_map
        detail[key] = list(ep_map.keys())

    tt_norm = str(translation_type or "sub").strip().lower()
    primary_key = (tt_norm if episodes_map_by_type.get(tt_norm) else None) or ("sub" if episodes_map_by_type.get("sub") else ("dub" if episodes_map_by_type.get("dub") else "sub"))
    primary_map = episodes_map_by_type.get(primary_key, {})
    primary_by_number = by_number_by_type.get(primary_key, {})

    ids = list(primary_map.keys())
    labels = {
        eid: ep.get("label", eid)
        for eid, ep in primary_map.items()
    }

    return {
        "state": state,
        "episodes": {
            k: list(m.values()) for k, m in episodes_map_by_type.items()
        },
        "episodes_map": primary_map,
        "episodes_map_by_ttype": episodes_map_by_type,
        "by_number": primary_by_number,
        "by_number_by_ttype": by_number_by_type,
        "ids": ids,
        "labels": labels,
        "detail": detail,
        "error": error,
        "_provider": _clean_text(provider),
        "_provider_id": _clean_text(provider_id),
        "_provider_episode_ids": ids,
    }


