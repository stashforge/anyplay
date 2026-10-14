"""AnimeThemes API helpers."""

from __future__ import annotations

from urllib.parse import quote, urlencode

from .http import request_json

API_BASE = "https://api.animethemes.moe"
ANIMETHEMES_SEARCH_TABS = ("anime", "songs", "videos", "artists")
ANIME_INCLUDE = "resources,animethemes.song.artists,animethemes.animethemeentries.videos"


def _text(value) -> str:
    return str(value or "").strip()


def _artist_names(song_or_item: dict) -> str:
    artists = song_or_item.get("artists") or []
    names = [_text(artist.get("name")) for artist in artists if isinstance(artist, dict)]
    return ", ".join(name for name in names if name)


def _video_flags(video: dict, entry: dict | None = None) -> list[str]:
    flags = []
    source = _text(video.get("source"))
    if source:
        flags.append(source)
    if video.get("nc"):
        flags.append("NC")
    if video.get("subbed"):
        flags.append("Subbed")
    if video.get("lyrics"):
        flags.append("Lyrics")
    if entry:
        if entry.get("spoiler"):
            flags.append("Spoiler")
        if entry.get("nsfw"):
            flags.append("NSFW")
    return flags


def anime_lookup_url(show: dict) -> str:
    al_id = _text(show.get("aniListId") or show.get("_anilist_id"))
    mal_id = _text(show.get("malId") or show.get("_mal_id"))
    params = {"include": ANIME_INCLUDE}
    if al_id:
        params.update({
            "filter[has]": "resources",
            "filter[site]": "AniList",
            "filter[external_id]": al_id,
        })
        return f"{API_BASE}/anime?{urlencode(params)}"
    if mal_id:
        params.update({
            "filter[has]": "resources",
            "filter[site]": "MyAnimeList",
            "filter[external_id]": mal_id,
        })
        return f"{API_BASE}/anime?{urlencode(params)}"
    slug = _text(show.get("slug") or show.get("_animethemes_slug"))
    if slug:
        return f"{API_BASE}/anime/{quote(slug)}?{urlencode(params)}"
    query = _text(show.get("name") or show.get("englishName"))
    return f"{API_BASE}/search?{urlencode({'q': query})}"


def _first_anime(payload: dict) -> dict | None:
    anime = payload.get("anime")
    if isinstance(anime, dict):
        return anime
    if isinstance(anime, list) and anime:
        return anime[0]
    search = payload.get("search") or {}
    results = search.get("anime") if isinstance(search, dict) else None
    if isinstance(results, list) and results:
        return results[0]
    return None


def get_anime_for_show(show: dict) -> dict | None:
    payload = request_json(anime_lookup_url(show), extra_hdrs={"User-Agent": "anyplay-cli"})
    anime = _first_anime(payload)
    if not anime:
        return None
    if anime.get("animethemes"):
        return anime
    slug = _text(anime.get("slug"))
    if not slug:
        return anime
    detail_url = f"{API_BASE}/anime/{quote(slug)}?{urlencode({'include': ANIME_INCLUDE})}"
    return _first_anime(request_json(detail_url, extra_hdrs={"User-Agent": "anyplay-cli"}))


def search(query: str) -> dict:
    payload = request_json(
        f"{API_BASE}/search?{urlencode({'q': query})}",
        extra_hdrs={"User-Agent": "anyplay-cli"},
    )
    return payload.get("search") or {}


def flatten_anime_videos(anime: dict) -> list[dict]:
    rows = []
    anime_name = _text(anime.get("name"))
    for theme in anime.get("animethemes") or []:
        song = theme.get("song") or {}
        title = _text(song.get("title")) or "Unknown"
        artists = _artist_names(song)
        theme_slug = _text(theme.get("slug") or theme.get("type"))
        for entry in theme.get("animethemeentries") or []:
            version = entry.get("version")
            entry_slug = theme_slug
            if version and int(version) > 1 and "v" not in entry_slug.lower():
                entry_slug = f"{entry_slug}v{version}"
            for video in entry.get("videos") or []:
                link = _text(video.get("link"))
                if not link:
                    continue
                res = _text(video.get("resolution"))
                parts = [entry_slug, title]
                if artists:
                    parts.append(artists)
                tail = " ".join(part for part in [res, *_video_flags(video, entry)] if part)
                if tail:
                    parts.append(tail)
                label = "  ".join(part for part in parts if part)
                rows.append({
                    "kind": "video",
                    "label": label,
                    "anime": anime_name,
                    "theme": entry_slug,
                    "song": title,
                    "artists": artists,
                    "episodes": _text(entry.get("episodes")),
                    "stream": {
                        "link": link,
                        "resolution": res or "WEB",
                        "source": video.get("source") or "AnimeThemes",
                        "type": "webm",
                    },
                    "raw": {"theme": theme, "entry": entry, "video": video},
                })
    return rows


def flatten_search_bucket(bucket: str, items: list[dict]) -> list[dict]:
    rows = []
    for item in items or []:
        if bucket == "anime":
            title = _text(item.get("name"))
            tail = " ".join(part for part in (_text(item.get("year")), _text(item.get("media_format"))) if part)
            label = f"{title}  {tail}".rstrip()
            kind = "anime"
        elif bucket == "songs":
            title = _text(item.get("title"))
            artists = _artist_names(item)
            label = f"{title}  {artists}".rstrip()
            kind = "song"
        elif bucket == "videos":
            title = _text(item.get("basename") or item.get("filename"))
            res = _text(item.get("resolution"))
            tail = " ".join(part for part in (res, *_video_flags(item)) if part)
            label = f"{title}  {tail}".rstrip()
            kind = "video"
        elif bucket == "artists":
            label = _text(item.get("name"))
            kind = "artist"
        else:
            continue
        if label:
            rows.append({"kind": kind, "bucket": bucket, "label": label, "raw": item})
    return rows
