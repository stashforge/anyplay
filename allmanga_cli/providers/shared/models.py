"""Provider-neutral title helpers.

The app still uses plain dictionaries internally.  These helpers add a small
common contract that future providers can share without forcing a wider UI
rewrite.
"""

from __future__ import annotations

from typing import Any

from allmanga_cli.brain import AnimeBrain
from allmanga_cli.brain.core.models import EpisodeToken

from .schema import build_catalog, build_title

_TITLE_SCHEMA_KEYS = {
    "_id", "id", "name", "englishName", "romajiName", "nativeName", "altNames",
    "thumbnail", "banner", "description", "type", "format", "status",
    "season", "airedStart", "airedEnd", "startDate", "endDate",
    "episodeCount", "availableEpisodes", "availableEpisodesDetail",
    "score", "genres", "tags", "aniListId", "malId",
    "_provider", "_provider_id", "_provider_name",
}


def _find_field(d: dict, *canonical_targets: str) -> Any:
    """Case-insensitive and separator-agnostic field extractor."""
    if not isinstance(d, dict):
        return None
    for target in canonical_targets:
        if target in d and d[target] is not None:
            return d[target]

    import re
    norm_map = {
        re.sub(r"[^a-z0-9]", "", str(k).casefold()): v
        for k, v in d.items()
        if v is not None
    }
    for target in canonical_targets:
        clean_target = re.sub(r"[^a-z0-9]", "", str(target).casefold())
        if clean_target in norm_map:
            return norm_map[clean_target]
    return None


def normalize_title(
    title: dict[str, Any] | None,
    *,
    provider_id: str,
    provider_name: str,
    id_key: str = "_id",
) -> dict[str, Any] | None:
    if not isinstance(title, dict):
        return None
    source_id = str(title.get(id_key) or title.get("_provider_id") or "")
    extra = {
        key: value
        for key, value in title.items()
        if key not in _TITLE_SCHEMA_KEYS
    }

    res = build_title(
        provider=provider_id,
        provider_name=provider_name,
        provider_id=source_id,
        name=title.get("name") or "",
        english_name=title.get("englishName") or title.get("english_name") or "",
        romaji_name=title.get("romajiName") or title.get("romaji_name") or title.get("romaji") or "",
        native_name=title.get("nativeName") or title.get("native_name") or "",
        alt_names=title.get("altNames") or [],
        thumbnail=title.get("thumbnail") or "",
        banner=title.get("banner") or "",
        description=title.get("description") or "",
        media_type=title.get("type"),
        media_format=title.get("format"),
        status=title.get("status"),
        season=title.get("season") or {},
        aired_start=title.get("airedStart"),
        aired_end=title.get("airedEnd"),
        start_date=title.get("startDate"),
        end_date=title.get("endDate"),
        episode_count=title.get("episodeCount"),
        available_sub=(title.get("availableEpisodes") or {}).get("sub", 0),
        available_dub=(title.get("availableEpisodes") or {}).get("dub", 0),
        available_raw=(title.get("availableEpisodes") or {}).get("raw", 0),
        available_detail=title.get("availableEpisodesDetail") or {},
        score=title.get("score"),
        genres=title.get("genres") or [],
        tags=title.get("tags") or [],
        anilist_id=_find_field(title, "aniListId", "anilist_id", "anilist"),
        mal_id=_find_field(title, "malId", "idMal", "myanimelist_id", "mal"),
        extra=extra,
    )
    if res is not None:
        raw_name = res.get("name") or res.get("englishName") or ""
        if raw_name:
            bout = AnimeBrain.process(raw_name, dict)
            res["_franchise"] = bout.get("franchise") or raw_name
            res["_season"] = bout.get("season")
            res["_part"] = bout.get("part")
        else:
            res["_franchise"] = ""
            res["_season"] = None
            res["_part"] = None
    return res


def normalize_titles(
    titles,
    *,
    provider_id: str,
    provider_name: str,
    id_key: str = "_id",
) -> list[dict[str, Any]]:
    normalized = []
    for title in titles or []:
        item = normalize_title(
            title,
            provider_id=provider_id,
            provider_name=provider_name,
            id_key=id_key,
        )
        if item is not None:
            normalized.append(item)

    # Sort the results so same franchises are grouped together,
    # and seasons are chronologically ordered!
    def get_sort_key(item):
        raw_name = item.get("name") or ""
        franchise = str(item.get("_franchise") or "").lower()
        season = item.get("_season")
        part = item.get("_part")
        if not franchise and raw_name:
            bout = AnimeBrain.process(raw_name, dict)
            franchise = str(bout.get("franchise") or "").lower()
            season = bout.get("season")
            part = bout.get("part")

        year = 0
        month = 0
        date_candidates = [
            item.get("airedStart"),
            item.get("startDate"),
            item.get("season") if isinstance(item.get("season"), dict) else None,
            item.get("year"),
            item.get("releaseDate"),
            item.get("airedEnd"),
            item.get("endDate"),
        ]
        for d in date_candidates:
            if isinstance(d, dict):
                y = d.get("year")
                m = d.get("month") or 0
                if y and str(y).isdigit() and int(y) > 0:
                    year = int(y)
                    month = int(m) if str(m).isdigit() else 0
                    break
            elif isinstance(d, (int, float)) and d > 1900:
                year = int(d)
                break
            elif isinstance(d, str) and d.strip():
                parts = d.strip().split('-')
                if parts[0].isdigit() and int(parts[0]) > 1900:
                    year = int(parts[0])
                    if len(parts) > 1 and parts[1].isdigit():
                        month = int(parts[1])
                    break

        # 1. Base Franchise (split by colon to group spin-offs and movies with the main series!)
        base_franchise = franchise.split(':')[0].strip() if ':' in franchise else franchise

        # 2. Year/Month (chronological watch order)
        # 3. Season/Part (fallback for same-month releases)
        # 4. Raw Name (alphabetical fallback for providers like AniDBApp that return 0 for all years)
        return (base_franchise, year == 0, year, month, season or 0, part or 0, raw_name)

    return sorted(normalized, key=get_sort_key)


def title_provider_id(title: dict[str, Any] | None) -> str:
    if not isinstance(title, dict):
        return ""
    return str(title.get("_provider_id") or title.get("_id") or "")


def title_provider_key(title: dict[str, Any] | None, default: str = "allanime") -> str:
    if not isinstance(title, dict):
        return default
    return str(title.get("_provider") or title.get("provider") or default)


def normalize_episode_catalog(
    catalog: dict[str, Any] | None,
    *,
    provider_id: str,
    provider_title_id: str,
) -> dict[str, Any]:
    normalized = dict(catalog or {})
    raw_episodes = normalized.get("episodes") or normalized.get("detail")
    labels = normalized.get("labels") or {}

    if "episodes_map" in normalized and normalized.get("ids"):
        normalized["_provider"] = provider_id
        normalized["_provider_id"] = str(provider_title_id or "")
        normalized["_provider_episode_ids"] = normalized["ids"]
        return normalized

    episodes_input = {}
    if isinstance(raw_episodes, dict):
        for k in ("sub", "dub", "raw"):
            items = raw_episodes.get(k) or []
            episodes_input[k] = [
                dict(it, label=it.get("label") or labels.get(it.get("id")) or it.get("id"))
                if isinstance(it, dict)
                else {"id": it, "label": labels.get(str(it)) or str(it)}
                for it in items
            ]
    else:
        ids = [str(episode) for episode in normalized.get("ids") or []]
        episodes_input["sub"] = [
            {"id": eid, "label": labels.get(eid) or eid}
            for eid in ids
        ]
        episodes_input["dub"] = []
        episodes_input["raw"] = []

    req_ttype = normalized.get("translation_type") or normalized.get("translationType") or "sub"
    built = build_catalog(
        provider=provider_id,
        provider_id=provider_title_id,
        state=normalized.get("state") or "loaded",
        error=normalized.get("error") or "",
        episodes=episodes_input,
        translation_type=req_ttype,
    )
    for k, v in normalized.items():
        if k not in built:
            built[k] = v

    if normalized.get("ids"):
        built["ids"] = [str(x) for x in normalized["ids"]]
    built["_provider"] = provider_id
    built["_provider_id"] = str(provider_title_id or "")
    built["_provider_episode_ids"] = built["ids"]
    return built



def normalize_episode_sources(
    payload: dict[str, Any] | None,
    *,
    provider_id: str,
    provider_title_id: str,
    episode: str,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    normalized = dict(payload)
    episode_data = dict(normalized.get("episode") or {})
    sources = list(episode_data.get("sourceUrls") or [])
    episode_data["sourceUrls"] = sources
    normalized["episode"] = episode_data
    normalized["_provider"] = provider_id
    normalized["_provider_id"] = str(provider_title_id or "")
    normalized["_provider_episode"] = str(episode)
    normalized["_provider_sources"] = sources
    return normalized
