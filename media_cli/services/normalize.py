"""Data normalization for AniList API responses."""

from ..domain.metadata import set_next_airing_fields


def _fuzzy_date(media, field):
    d = media.get(field)
    if not d or not d.get("year"):
        return None
    return {"year": d.get("year"), "month": d.get("month"), "date": d.get("day")}


def normalize_media(media, *, list_name=None, entry=None):
    titles = media.get("title") or {}
    quarter = media.get("season")
    main_name = titles.get("romaji") or titles.get("english") or "Unknown"

    show = {
        "_id": str(media["id"]),
        "malId": media.get("idMal"),
        "name": main_name,
        "romajiName": titles.get("romaji") or "",
        "englishName": titles.get("english") or "",
        "nativeName": titles.get("native") or "",
        "altNames": list(media.get("synonyms") or []),
        "type": media.get("format") or "TV",
        "season": {
            "year": media.get("seasonYear"),
            "quarter": quarter.capitalize() if quarter else None,
        },
        "airedStart": _fuzzy_date(media, "startDate"),
        "score": (
            media.get("averageScore") / 10.0
            if media.get("averageScore")
            else None
        ),
        "genres": media.get("genres", []),
        "description": media.get("description", ""),
        "availableEpisodes": {
            "sub": media.get("episodes", 0) or 0,
            "dub": 0,
            "raw": 0,
        },
        "status": media.get("status"),
        "episodeCount": media.get("episodes"),
        "thumbnail": (media.get("coverImage") or {}).get("large"),
    }
    if entry:
        show["_anilist_progress"] = entry.get("progress")
        if entry.get("updatedAt") is not None:
            show["_anilist_updated_at"] = entry.get("updatedAt")
        show["_anilist_started_at"] = entry.get("startedAt") or {}
        show["_anilist_completed_at"] = entry.get("completedAt") or {}
    if list_name:
        show["_anilist_list"] = list_name
    list_entry = media.get("mediaListEntry")
    if list_entry:
        show["_anilist_list"] = list_entry.get("status")
        show["_anilist_progress"] = list_entry.get("progress")
        show["_anilist_score"] = list_entry.get("score")
        show["_anilist_entry_id"] = list_entry.get("id")
        show["_anilist_started_at"] = list_entry.get("startedAt") or {}
        show["_anilist_completed_at"] = list_entry.get("completedAt") or {}
    elif not entry and not list_name:
        show["_anilist_progress"] = 0
        show["_anilist_list"] = ""
    next_airing = media.get("nextAiringEpisode")
    if next_airing:
        set_next_airing_fields(show, next_airing)
    return show


def media_id(anime):
    candidate = anime.get("aniListId")
    if candidate:
        return candidate
    candidate = anime.get("_id")
    return candidate if str(candidate or "").isdigit() else None


def apply_media_update(anime, media):
    if not anime or not media:
        return False
    anime["aniListId"] = str(
        media.get("id") or media_id(anime) or ""
    )
    titles = media.get("title") or {}
    if titles.get("english"):
        anime["englishName"] = titles["english"]
        anime["_display_english_name"] = titles["english"]
    if titles.get("romaji"):
        anime["romajiName"] = titles["romaji"]
        anime["_display_name"] = titles["romaji"]
    if titles.get("native"):
        anime["nativeName"] = titles["native"]

    if media.get("synonyms"):
        anime["altNames"] = list(media["synonyms"])

    if media.get("description"):
        anime["description"] = media["description"]
    if media.get("format"):
        anime["type"] = media["format"]
    anime["status"] = media.get("status") or anime.get("status")
    if media.get("episodes") is not None:
        anime["episodeCount"] = media["episodes"]
    if media.get("averageScore") is not None:
        anime["score"] = media["averageScore"] / 10.0

    list_entry = media.get("mediaListEntry") or {}
    if list_entry.get("progress") is not None:
        anime["_anilist_progress"] = max(0, int(list_entry["progress"]))
    if list_entry.get("status"):
        anime["_anilist_list"] = list_entry["status"]
    if list_entry:
        anime["_anilist_entry_id"] = list_entry.get("id")
        anime["_anilist_started_at"] = list_entry.get("startedAt") or {}
        anime["_anilist_completed_at"] = list_entry.get("completedAt") or {}

    start = _fuzzy_date(media, "startDate")
    if start:
        anime["airedStart"] = start
    end = _fuzzy_date(media, "endDate")
    if end:
        anime["airedEnd"] = end
    else:
        anime.pop("airedEnd", None)

    next_airing = media.get("nextAiringEpisode")
    if next_airing:
        set_next_airing_fields(anime, next_airing)
    else:
        anime.pop("_next_airing_ep", None)
        anime.pop("_next_airing_time", None)
        anime.pop("_next_airing_at", None)
    return True
