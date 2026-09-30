"""AniList GraphQL requests — fetching only.

Response shaping (normalize_media, apply_media_update) lives in normalize.py.
"""

import json
import time
import urllib.error
import urllib.request

from ..core.api import SearchFailure

_ban_until = 0


API_URL = "https://graphql.anilist.co"
USER_AGENT = "Mozilla/5.0"
CHUNK_SIZE = 50

# Shared field selection used by every media query, so adding/removing a
# field only ever needs to happen in one place.
_MEDIA_FIELDS = """
    id
    idMal
    title { romaji english native }
    synonyms
    format
    type
    status
    episodes
    duration
    season
    seasonYear
    startDate { year month day }
    endDate { year month day }
    countryOfOrigin
    isAdult
    averageScore
    meanScore
    popularity
    genres
    description(asHtml: false)
    coverImage { large medium color }
    bannerImage
    nextAiringEpisode { episode airingAt timeUntilAiring }
    mediaListEntry {
      id
      status
      score
      progress
      progressVolumes
      repeat
      priority
      private
      notes
      startedAt { year month day }
      completedAt { year month day }
      createdAt
      updatedAt
    }
"""


def _request(token=""):
    request = urllib.request.Request(API_URL, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", USER_AGENT)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    return request


def _post(urlopen, read_json, query, variables=None, token=""):
    global _ban_until
    if time.time() < _ban_until:
        raise urllib.error.HTTPError(
            API_URL, 429, "Too Many Requests (Local Cooldown)", {}, None
        )

    payload = {"query": query}
    if variables is not None:
        payload["variables"] = variables

    try:
        with urlopen(_request(token), json.dumps(payload).encode()) as response:
            return read_json(response)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            retry_after = e.headers.get("Retry-After") if hasattr(e, "headers") else None
            wait_time = int(retry_after) if retry_after and retry_after.isdigit() else 60
            _ban_until = time.time() + wait_time
        raise e


def _run(urlopen, read_json, token, query, variables):
    """POST a query and raise on AniList-reported errors."""
    payload = _post(urlopen, read_json, query, variables, token)
    if payload.get("errors"):
        raise SearchFailure("AniList rejected the request.")
    return payload.get("data") or {}


def _chunks(items, size=CHUNK_SIZE):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _page_media(data):
    return data.get("Page", {}).get("media", [])


def _merge_ids(single, many):
    """Combine a singular id= arg and a plural ids= arg into one clean int list.

    Drops anything that isn't digit-like (None, "", non-numeric strings)
    instead of letting a bad id blow up the GraphQL call later.
    """
    combined = list(many or [])
    if single is not None:
        combined.append(single)
    return [int(i) for i in combined if str(i).isdigit()]


# ---------------------------------------------------------------------------
# Query builders — each just wraps _MEDIA_FIELDS (or a caller-supplied
# override) into the right query shape.
# ---------------------------------------------------------------------------

def _query_search(fields):
    return f"""
    query($search: String) {{
      Page(page: 1, perPage: 25) {{
        media(search: $search, type: ANIME, sort: SEARCH_MATCH) {{
          {fields}
        }}
      }}
    }}
    """


def _query_by_ids(fields):
    return f"""
    query($idIn: [Int], $idMalIn: [Int]) {{
      Page(page: 1, perPage: {CHUNK_SIZE}) {{
        media(id_in: $idIn, idMal_in: $idMalIn, type: ANIME) {{
          {fields}
        }}
      }}
    }}
    """


def _query_user_list(fields):
    return f"""
    query($userId: Int, $status: MediaListStatus) {{
      MediaListCollection(userId: $userId, type: ANIME, status: $status) {{
        lists {{
          name
          entries {{
            media {{
              {fields}
            }}
          }}
        }}
      }}
    }}
    """


def _fetch_user_list(urlopen, read_json, token, status, fields):
    viewer = _run(urlopen, read_json, token, "query{Viewer{id name}}", None)
    user_id = viewer["Viewer"]["id"]
    variables = {"userId": user_id}
    if status:
        variables["status"] = status
    data = _run(
        urlopen, read_json, token, _query_user_list(fields), variables
    )
    results = []
    for media_list in data["MediaListCollection"]["lists"]:
        for entry in media_list["entries"]:
            media = entry["media"]
            # tag with list name here since MediaListCollection groups by
            # list, unlike the Page-based queries.
            media["_list_name"] = media_list["name"]
            results.append(media)
    return results


# ---------------------------------------------------------------------------
# Single entry point for reads. Exactly one selector should be passed.
# Always returns a list of *raw* AniList media dicts — normalize with
# normalize_media() if you need the app's shape.
# ---------------------------------------------------------------------------

def fetch(
    urlopen,
    read_json,
    token="",
    *,
    search=None,
    anilist_id=None,
    anilist_ids=None,
    mal_id=None,
    mal_ids=None,
    user_list_status="",
    user_list=False,
    fields=_MEDIA_FIELDS,
):
    """Fetch media from AniList.

    search=          text search
    anilist_id(s)=   lookup by AniList id, singular or list
    mal_id(s)=       lookup by MyAnimeList id, singular or list
    user_list=True   fetch the authenticated user's full list
    user_list_status=  fetch one status of the user's list (implies user_list)
    fields=          override the GraphQL field selection (defaults to
                      _MEDIA_FIELDS)
    """
    if search:
        data = _run(urlopen, read_json, token, _query_search(fields), {"search": search})
        return _page_media(data)

    ids = _merge_ids(anilist_id, anilist_ids)
    mids = _merge_ids(mal_id, mal_ids)

    if ids or mids:
        results = []
        for chunk in _chunks(ids):
            data = _run(urlopen, read_json, token, _query_by_ids(fields), {"idIn": chunk})
            results.extend(_page_media(data))
        for chunk in _chunks(mids):
            data = _run(urlopen, read_json, token, _query_by_ids(fields), {"idMalIn": chunk})
            results.extend(_page_media(data))
        return results

    if user_list or user_list_status:
        return _fetch_user_list(urlopen, read_json, token, user_list_status or None, fields)

    return []


def fetch_one(urlopen, read_json, token="", *, anilist_id=None, mal_id=None, fields=_MEDIA_FIELDS):
    """Convenience wrapper over fetch() for the common single-id case."""
    results = fetch(urlopen, read_json, token, anilist_id=anilist_id, mal_id=mal_id, fields=fields)
    return results[0] if results else None


def search_media_id(urlopen, read_json, title):
    payload = _post(
        urlopen,
        read_json,
        "query($s:String){Media(search:$s type:ANIME){id}}",
        {"s": title},
    )
    return payload["data"]["Media"]["id"]


def update_entry(
        urlopen,
        read_json,
        token,
        media_id,
        *,
        progress=None,
        status=None,
        score=None,
        started_at=None,
        completed_at=None):
    query = """
    mutation(
      $id:Int,
      $p:Int,
      $s:MediaListStatus,
      $score:Int,
      $startedAt:FuzzyDateInput,
      $completedAt:FuzzyDateInput
    ) {
      SaveMediaListEntry(
        mediaId:$id,
        progress:$p,
        status:$s,
        scoreRaw:$score,
        startedAt:$startedAt,
        completedAt:$completedAt
      ) {
        id
        progress
        status
        score(format: POINT_100)
        startedAt { year month day }
        completedAt { year month day }
      }
    }
    """
    variables = {"id": media_id}
    if progress is not None:
        variables["p"] = progress
    if status is not None:
        variables["s"] = status
    if score is not None:
        variables["score"] = int(score)
    if started_at is not None:
        variables["startedAt"] = started_at
    if completed_at is not None:
        variables["completedAt"] = completed_at
    return _post(urlopen, read_json, query, variables, token)
