"""AnimeThemes API helpers."""

from __future__ import annotations

from urllib.parse import quote, urlencode
from ..core.workers import DaemonExecutor
from threading import Lock, Thread

from .http import request_json

API_BASE = "https://api.animethemes.moe"
ANIMETHEMES_SEARCH_TABS = (
    "anime",
    "animethemes",
    "artists",
    "series",
    "studios",
    "playlists",
)
ANIME_INCLUDE = "images,resources,animethemes.song.artists,animethemes.animethemeentries.videos"


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


def _anime_name(value) -> str:
    if isinstance(value, dict):
        return _text(value.get("name") or value.get("title"))
    return _text(value)


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


def _anime_detail_by_slug(slug: str) -> dict | None:
    if not slug:
        return None
    url = f"{API_BASE}/anime/{quote(slug)}?{urlencode({'include': ANIME_INCLUDE})}"
    return _first_anime(request_json(url, extra_hdrs={"User-Agent": "anyplay-cli"}))


def _merge_search_anime_details(search: dict) -> dict:
    anime_items = search.get("anime")
    if not isinstance(anime_items, list) or not anime_items:
        return search

    enriched_anime = []
    enriched_themes = []
    seen_theme_ids = set()
    for item in anime_items:
        if not isinstance(item, dict):
            continue
        detail = None
        try:
            detail = _anime_detail_by_slug(_text(item.get("slug")))
        except Exception:
            detail = None
        anime = detail if isinstance(detail, dict) else item
        enriched_anime.append(anime)
        for theme in anime.get("animethemes") or []:
            if not isinstance(theme, dict):
                continue
            theme_id = theme.get("id")
            if theme_id and theme_id in seen_theme_ids:
                continue
            if theme_id:
                seen_theme_ids.add(theme_id)
            enriched_themes.append({**theme, "anime": anime})

    if enriched_anime:
        search["anime"] = enriched_anime
    if enriched_themes:
        search["animethemes"] = enriched_themes
    return search


def search(query: str) -> dict:
    payload = request_json(
        f"{API_BASE}/search?{urlencode({'q': query})}",
        extra_hdrs={"User-Agent": "anyplay-cli"},
    )
    search_payload = payload.get("search") or {}
    if isinstance(search_payload, dict):
        result = _merge_search_anime_details(search_payload)
        def enrich_artist(item):
            try:
                return {**item, **get_artist(item.get("slug", ""), summary=True)}
            except Exception:
                return item
        with DaemonExecutor(max_workers=2) as pool:
            result["artists"] = list(pool.map(enrich_artist, result.get("artists") or []))
        return result
    return {}


def get_artist(slug: str, *, summary=False) -> dict:
    include = "images,groups" if summary else "images,groups,songs.animethemes.anime,memberPerformances.song"
    url = f"{API_BASE}/artist/{quote(slug, safe='')}?{urlencode({'include': include})}"
    return request_json(url, extra_hdrs={"User-Agent": "anyplay-cli"}).get("artist") or {}


def artist_theme_rows(artist: dict, owner: dict | None = None) -> list[dict]:
    rows = []
    seen = set()
    member_songs = None
    if owner is not None:
        member_songs = {p.get("song", {}).get("id") for p in owner.get("memberPerformances") or []}
    for song in artist.get("songs") or []:
        if member_songs is not None and song.get("id") not in member_songs:
            continue
        for theme in song.get("animethemes") or []:
            if theme.get("id") in seen:
                continue
            seen.add(theme.get("id"))
            label = _text(theme.get("type")) + str(theme.get("sequence") or "")
            rows.append({
                "kind": "theme", "bucket": "animethemes",
                "label": f"{label}  {_text(song.get('title')) or 'Unknown song'}",
                "raw": {**theme, "song": song},
                "credit": song.get("artistsong") or {},
                "artist": (owner or artist).get("name", "Unknown artist"),
            })
    return rows


def artist_sections(artist: dict) -> list[dict]:
    sections = []
    direct = artist_theme_rows(artist)
    if direct:
        sections.append({"label": "Song Performances", "rows": direct, "credit": ""})

    def group_section(group):
        detail = get_artist(group["slug"])
        rows = artist_theme_rows(detail, artist)
        return {"label": f"In {group['name']}", "rows": rows,
                "credit": _text((group.get("artistmember") or {}).get("as")),
                "group": group["name"]}

    groups = artist.get("groups") or []
    results = [None] * len(groups)
    pending = iter(enumerate(groups))
    lock = Lock()
    errors = []

    def worker():
        while True:
            with lock:
                try:
                    index, group = next(pending)
                except StopIteration:
                    return
            try:
                results[index] = group_section(group)
            except Exception as error:
                with lock:
                    errors.append(error)

    # Group requests belong to a cancellable browsing session, not process exit.
    workers = [Thread(target=worker, daemon=True, name="artist-group") for _ in range(min(2, len(groups)))]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join()
    if errors:
        raise errors[0]
    sections.extend(section for section in results if section and section["rows"])
    return sections


def enrich_artist_theme(row: dict) -> dict:
    theme = row["raw"]
    song = theme["song"]
    song_url = f"{API_BASE}/song/{song['id']}?{urlencode({'include': 'artists'})}"
    song_detail = request_json(song_url, extra_hdrs={"User-Agent": "anyplay-cli"}).get("song") or song
    anime = theme.get("anime") or {}
    anime_detail = _anime_detail_by_slug(anime.get("slug", "")) or anime
    full_theme = next((t for t in anime_detail.get("animethemes") or [] if t.get("id") == theme.get("id")), theme)
    return {**row, "raw": {**full_theme, "song": {**song, **song_detail}, "anime": anime_detail}}


def _theme_song(theme: dict) -> dict:
    song = theme.get("song")
    return song if isinstance(song, dict) else {}


def theme_video_rows(theme: dict) -> list[dict]:
    anime = theme.get("anime")
    if not isinstance(anime, dict):
        anime = {"name": _anime_name(anime) or "AnimeThemes"}
    return flatten_anime_videos({**anime, "animethemes": [theme]})


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
            kind = "anime"
            label = title
        elif bucket == "animethemes":
            song = _theme_song(item)
            title = _text(song.get("title")) or _text(item.get("slug") or item.get("type"))
            artists = _artist_names(song)
            label = f"{title} by {artists}" if title and artists else title
            kind = "theme"
        elif bucket == "artists":
            label = _text(item.get("name"))
            kind = "artist"
        elif bucket == "series":
            label = _text(item.get("name"))
            kind = "series"
        elif bucket == "studios":
            label = _text(item.get("name"))
            kind = "studio"
        elif bucket == "playlists":
            label = _text(item.get("name"))
            kind = "playlist"
        else:
            continue
        if label:
            rows.append({"kind": kind, "bucket": bucket, "label": label, "raw": item})
    return rows
