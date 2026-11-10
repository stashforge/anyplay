"""AnimeThemes API helpers."""

from __future__ import annotations

import re
import json
from threading import Lock, Thread

from .http import request_json

ANIMETHEMES_SEARCH_TABS = (
    "anime",
    "animethemes",
    "artists",
    "series",
    "studios",
    "playlists",
)


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


GRAPHQL_URL = "https://rs.animethemes.moe/graphql"
_THEME_FIELDS = """
    id slug type sequence
    song { id title { romaji native } staff { role artist { id slug name { main native } } } }
    entries { id version episodes notes nsfw spoiler
        videos { nodes { id link resolution source nc subbed lyrics overlap } }
    }
"""
_ANIME_FIELDS = """
id slug title { romaji english native } format season year synopsis
images { nodes { id link facet } }
themes {
""" + _THEME_FIELDS + "}"


def _graphql(query, variables):
    from ..core.api import SearchFailure

    payload = request_json(
        GRAPHQL_URL, data=json.dumps({"query": query, "variables": variables}).encode(),
        extra_hdrs={"User-Agent": "anyplay-cli", "Accept": "application/json"},
    )
    if not isinstance(payload, dict) or payload.get("errors") or not isinstance(payload.get("data"), dict):
        raise SearchFailure("AnimeThemes returned an invalid GraphQL response.")
    return payload["data"]


def _normalize_graphql_anime(anime):
    if not isinstance(anime, dict):
        return None
    result = dict(anime)
    title = anime.get("title") or {}
    result["name"] = title.get("romaji") or title.get("english") or ""
    result["englishName"] = title.get("english") or ""
    result["nativeName"] = title.get("native") or ""
    result["media_format"] = anime.get("format")
    result["images"] = (anime.get("images") or {}).get("nodes") or []
    themes = []
    for theme in anime.get("themes") or []:
        song = theme.get("song") or {}
        song_title = song.get("title") or {}
        artists = [
            {**credit["artist"], "name": credit["artist"]["name"]["main"]}
            for credit in song.get("staff") or []
            if credit.get("role", "").casefold() == "performance" and credit.get("artist")
        ]
        entries = [
            {**entry, "videos": (entry.get("videos") or {}).get("nodes") or []}
            for entry in theme.get("entries") or []
        ]
        themes.append({**theme, "song": {**song, "title": song_title.get("romaji") or "",
                                       "artists": artists}, "animethemeentries": entries})
    result["animethemes"] = themes
    return result


def get_anime_for_show(show: dict) -> dict | None:
    al_id = _text(show.get("aniListId") or show.get("_anilist_id"))
    mal_id = _text(show.get("malId") or show.get("_mal_id"))
    if al_id or mal_id:
        query = "query($site: ResourceSite!, $ids: [Int!]) { filterAnimeByExternalSite(site: $site, id: $ids) { " + _ANIME_FIELDS + " } }"
        data = _graphql(query, {"site": "ANILIST" if al_id else "MAL", "ids": [int(al_id or mal_id)]})
        rows = data.get("filterAnimeByExternalSite") or []
        return _normalize_graphql_anime(rows[0]) if rows else None
    slug = _text(show.get("slug") or show.get("_animethemes_slug"))
    if slug:
        return _anime_detail_by_slug(slug)
    query = "query($search: String!) { search(search: $search, first: 1, page: 1) { anime { data { " + _ANIME_FIELDS + " } } } }"
    data = _graphql(query, {"search": _text(show.get("name") or show.get("englishName"))})
    rows = (data.get("search") or {}).get("anime", {}).get("data") or []
    return _normalize_graphql_anime(rows[0]) if rows else None


def _anime_detail_by_slug(slug: str) -> dict | None:
    if not slug:
        return None
    data = _graphql("query($slug: String!) { anime(slug: $slug) { " + _ANIME_FIELDS + " } }", {"slug": slug})
    return _normalize_graphql_anime(data.get("anime"))


def search(query: str) -> dict:
    # Fetch all visible buckets together, retaining our existing internal names.
    selection = """
        anime { data { ANIME_FIELDS } }
        themes { data { THEME_FIELDS anime { id slug title { romaji english native } format season year synopsis images { nodes { id link facet } } } } }
        artists { data { id slug name { main native } images { nodes { id link facet } }
            groups { nodes { id slug name { main native } } } } }
        series { data { id slug title { romaji } } }
        studios { data { id slug name images { nodes { id link facet } } } }
        playlists { data { id name description tracksCount user { name } } }
    """.replace("ANIME_FIELDS", _ANIME_FIELDS).replace("THEME_FIELDS", _THEME_FIELDS)
    data = _graphql(
        "query($search: String!) { search(search: $search, first: 20, page: 1) { " + selection + " } }",
        {"search": query},
    ).get("search") or {}
    result = {}
    for bucket in ANIMETHEMES_SEARCH_TABS:
        source = "themes" if bucket == "animethemes" else bucket
        rows = (data.get(source) or {}).get("data") or []
        normalized = []
        for row in rows:
            if bucket == "anime":
                row = _normalize_graphql_anime(row)
            elif bucket == "animethemes":
                anime = _normalize_graphql_anime({**(row.get("anime") or {}), "themes": [row]})
                row = {**anime["animethemes"][0], "anime": {key: value for key, value in anime.items() if key != "animethemes"}}
            elif bucket == "artists":
                row = {**row, "name": row["name"]["main"],
                       "images": (row.get("images") or {}).get("nodes") or [],
                       "groups": [{**group, "name": group["name"]["main"]}
                                  for group in (row.get("groups") or {}).get("nodes") or []]}
            elif bucket == "series":
                row = {**row, "name": row["title"]["romaji"]}
            elif bucket == "studios":
                row = {**row, "images": (row.get("images") or {}).get("nodes") or []}
            normalized.append(row)
        result[bucket] = normalized
    return result


def get_theme(theme_id) -> dict:
    data = _graphql(
        "query($ids: [Int!]) { themeConnection(filter: {idIn: $ids}) { nodes { "
        + _THEME_FIELDS
        + " anime { id slug title { romaji english native } format season year synopsis images { nodes { id link facet } } } } } }",
        {"ids": [int(theme_id)]},
    )
    themes = (data.get("themeConnection") or {}).get("nodes") or []
    if len(themes) != 1 or str(themes[0].get("id")) != str(theme_id):
        raise ValueError("AnimeThemes returned a different theme identity")
    theme = themes[0]
    anime = _normalize_graphql_anime({**(theme.get("anime") or {}), "themes": [theme]})
    context = {key: value for key, value in anime.items() if key != "animethemes"}
    return {**anime["animethemes"][0], "anime": context}


def get_series(slug: str) -> dict:
    data = _graphql(
        "query($slug: String!) { series(slug: $slug) { id slug title { romaji } anime { nodes { "
        + _ANIME_FIELDS + " } } } }",
        {"slug": slug},
    )
    series = data.get("series")
    if not isinstance(series, dict):
        return {}
    return {
        **series,
        "name": (series.get("title") or {}).get("romaji") or "",
        "anime": [_normalize_graphql_anime(anime) for anime in (series.get("anime") or {}).get("nodes") or []],
    }


def get_playlist(playlist_id: str) -> dict:
    fields = """
        id name description tracksCount user { name }
        tracks {
            id position
            video { id link resolution source nc subbed lyrics overlap }
            entry { id version episodes notes nsfw spoiler
                theme { THEME_FIELDS
                    anime { id slug title { romaji english native } format season year synopsis
                        images { nodes { id link facet } } }
                }
            }
        }
    """.replace("THEME_FIELDS", _THEME_FIELDS)
    return _graphql(
        "query($id: String!) { playlist(id: $id) { " + fields + " } }",
        {"id": playlist_id},
    ).get("playlist") or {}


def playlist_song_rows(playlist: dict) -> list[dict]:
    songs = []
    for track in sorted(playlist.get("tracks") or [], key=lambda row: row["position"]):
        entry = track.get("entry") or {}
        theme = entry.get("theme") or {}
        video = track.get("video") or {}
        # Playlist entries select a specific version/video; keep that selection.
        anime = _normalize_graphql_anime({
            **(theme.get("anime") or {}),
            "themes": [{**theme, "entries": [{**entry, "videos": {"nodes": [video]}}]}],
        })
        songs.extend(anime_song_rows(anime))
    return songs


def get_studio(slug: str) -> dict:
    data = _graphql(
        "query($slug: String!) { studio(slug: $slug) { id slug name anime { nodes { "
        + _ANIME_FIELDS + " } } } }",
        {"slug": slug},
    )
    studio = data.get("studio")
    if not isinstance(studio, dict):
        return {}
    return {**studio, "anime": [
        _normalize_graphql_anime(anime)
        for anime in (studio.get("anime") or {}).get("nodes") or []
    ]}


def get_artist(slug: str, *, summary=False) -> dict:
    fields = """
        id slug name { main native } images { nodes { id link facet } }
        groups { edges { node { id slug name { main native } } alias as notes } }
    """
    if not summary:
        fields += """
            songStaff { role alias as song { id title { romaji native }
                themes { id slug type sequence anime { id slug title { romaji english native } } } } }
            memberSongStaff { role song { id } }
        """
    artist = _graphql(
        "query($slug: String!) { artist(slug: $slug) { " + fields + " } }",
        {"slug": slug},
    ).get("artist")
    if not isinstance(artist, dict):
        return {}
    result = {
        **artist, "name": artist["name"]["main"],
        "images": (artist.get("images") or {}).get("nodes") or [],
        "groups": [{**edge["node"], "name": edge["node"]["name"]["main"],
                    "artistmember": {key: edge.get(key) for key in ("alias", "as", "notes")}}
                   for edge in (artist.get("groups") or {}).get("edges") or []],
        "songs": [], "memberPerformances": [],
    }
    seen_songs = set()
    for credit in artist.get("songStaff") or []:
        if credit.get("role", "").casefold() != "performance":
            continue
        song = credit["song"]
        if song["id"] in seen_songs:
            continue
        seen_songs.add(song["id"])
        themes = [{**theme, "anime": _normalize_graphql_anime(theme.get("anime") or {})}
                  for theme in song.get("themes") or []]
        result["songs"].append({**song, "title": song["title"]["romaji"],
                                "animethemes": themes, "artistsong": credit})
    result["memberPerformances"] = [
        credit for credit in artist.get("memberSongStaff") or []
        if credit.get("role", "").casefold() == "performance"
    ]
    return result


def song_row_label(title, theme=None) -> str:
    theme = theme or {}
    slug = _text(theme.get("slug")) or (_text(theme.get("type")) + _text(theme.get("sequence")))
    match = re.fullmatch(r"(OP|ED)(\d+)?(?:v\d+)?", slug, re.IGNORECASE)
    label = " ".join(part for part in (match[1].upper(), match[2]) if part) if match else ""
    return " ".join(part for part in (_text(title) or "Unknown song", label) if part)


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
            rows.append({
                "kind": "theme", "bucket": "animethemes",
                "label": song_row_label(song.get("title"), theme),
                "raw": {**theme, "song": song},
                "credit": song.get("artistsong") or {},
                "artist": (owner or artist).get("name") or "",
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
    anime = theme.get("anime") or {}
    anime_detail = _anime_detail_by_slug(anime.get("slug", "")) or anime
    full_theme = next((t for t in anime_detail.get("animethemes") or [] if t.get("id") == theme.get("id")), theme)
    return {**row, "raw": {**full_theme, "song": full_theme.get("song") or song, "anime": anime_detail}}


def _theme_song(theme: dict) -> dict:
    song = theme.get("song")
    return song if isinstance(song, dict) else {}


def theme_video_rows(theme: dict) -> list[dict]:
    anime = theme.get("anime")
    if not isinstance(anime, dict):
        anime = {"name": _anime_name(anime) or "AnimeThemes"}
    return flatten_anime_videos({**anime, "animethemes": [theme]})


def anime_song_rows(anime: dict) -> list[dict]:
    """One choice per playable theme, retaining every version/quality as a variant."""
    rows = []
    context = {key: value for key, value in anime.items() if key != "animethemes"}
    for theme in anime.get("animethemes") or []:
        variants = tuple(flatten_anime_videos({**context, "animethemes": [theme]}))
        if not variants:
            continue
        song = _theme_song(theme)
        title = _text(song.get("title")) or "Unknown song"
        artists = _artist_names(song)
        theme_label = _text(theme.get("slug")) or (
            _text(theme.get("type")) + str(theme.get("sequence") or "")
        )
        rows.append({
            "kind": "theme",
            "bucket": "animethemes",
            "label": song_row_label(title, theme),
            "anime": _text(anime.get("name")),
            "theme": theme_label,
            "song": title,
            "artists": artists,
            "raw": {**theme, "anime": context},
            "variants": variants,
        })
    return rows


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
            label = song_row_label(title, item) if title else ""
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
