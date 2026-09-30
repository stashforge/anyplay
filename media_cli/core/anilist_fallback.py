"""AniList metadata fallback search providers and orchestration.

Provides standalone functions for:
1. Public unauthenticated AniList search (`search_anilist_public`)
2. Authenticated AniList search with user token (`search_anilist_authenticated`)
3. Tenrai / MAL search with MalSync AniList ID translation (`search_tenrai`)
4. Unified fallback chain combining all three (`search_anilist_with_fallback`)
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .api import SearchFailure

logger = logging.getLogger(__name__)

ANILIST_URL = "https://graphql.anilist.co"

DEFAULT_GQL = """
query ($search: String, $page: Int, $perPage: Int) {
    Page(page: $page, perPage: $perPage) {
        media(search: $search, type: ANIME, sort: SEARCH_MATCH) {
            id
            idMal
            title { romaji english native }
            description(asHtml: false)
            coverImage { large }
            bannerImage
            format
            status
            season
            seasonYear
            episodes
            averageScore
            genres
        }
    }
}
"""


def _extract_ani_id(data: Any) -> int | None:
    """Recursively extract AniList ID from MalSync mapping response."""
    if isinstance(data, dict):
        if "aniId" in data and isinstance(data["aniId"], int):
            return data["aniId"]
        for value in data.values():
            res = _extract_ani_id(value)
            if res is not None:
                return res
    elif isinstance(data, list):
        for item in data:
            res = _extract_ani_id(item)
            if res is not None:
                return res
    return None


def _post_anilist_graphql(
    query: str,
    raw_gql_query: str = "",
    variables: dict | None = None,
    token: str = "",
) -> dict:
    """Low-level POST to AniList GraphQL endpoint."""
    gql = raw_gql_query.strip() if raw_gql_query and raw_gql_query.strip() else DEFAULT_GQL
    vars_payload = dict(variables) if variables else {"search": query, "page": 1, "perPage": 20}
    if "search" not in vars_payload and query:
        vars_payload["search"] = query

    req_data = json.dumps({"query": gql, "variables": vars_payload}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(ANILIST_URL, data=req_data, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def search_anilist_public(
    query: str,
    raw_gql_query: str = "",
    variables: dict | None = None,
) -> dict:
    """Execute an unauthenticated (anonymous) search against AniList GraphQL.

    Raises urllib.error.HTTPError or urllib.error.URLError on failure.
    """
    logger.debug("Attempting unauthenticated AniList search for '%s'", query)
    return _post_anilist_graphql(query, raw_gql_query, variables, token="")


def search_anilist_authenticated(
    query: str,
    token: str = "",
    raw_gql_query: str = "",
    variables: dict | None = None,
) -> dict:
    """Execute an authenticated search against AniList GraphQL using a user token.

    If token is not supplied, attempts to load the stored token from configuration.
    Raises ValueError if no token is available, or network/HTTP errors on failure.
    """
    if not token:
        try:
            from ..services.anilist_auth import stored_anilist_token
            from .storage import load_config
            token = stored_anilist_token(load_config())
        except Exception:
            token = ""

    if not token:
        raise ValueError("No AniList token available for authenticated search")

    logger.debug("Attempting authenticated AniList search for '%s'", query)
    return _post_anilist_graphql(query, raw_gql_query, variables, token=token)


MAL_API_ENDPOINT = "https://api.tenrai.org/v1/anime?q={query}&sfw=true&limit=10"


def search_mal(query: str) -> dict:
    """Execute a search against MyAnimeList (MAL) compatible API (Tenrai)
    and resolve AniList IDs via MalSync.

    Returns the results shaped in standard AniList GraphQL Page format:
    {"data": {"Page": {"media": [...]}}}
    """
    logger.debug("Attempting MAL search for '%s'", query)
    encoded_query = urllib.parse.quote(query)
    url = MAL_API_ENDPOINT.format(query=encoded_query)
    req = urllib.request.Request(url, headers={"User-Agent": "allmanga-cli"})
    with urllib.request.urlopen(req, timeout=10) as response:
        mal_data = json.loads(response.read().decode("utf-8"))

    media_list = []
    for item in mal_data.get("data", []):
        mal_id = item.get("mal_id")
        if not mal_id:
            continue

        ani_id = mal_id  # Fallback to mal_id if MalSync resolution is unavailable
        try:
            ms_req = urllib.request.Request(
                f"https://api.malsync.moe/mal/anime/{mal_id}",
                headers={"User-Agent": "allmanga-cli"},
            )
            with urllib.request.urlopen(ms_req, timeout=5) as m_res:
                ms_data = json.loads(m_res.read().decode("utf-8"))
                found_ani_id = _extract_ani_id(ms_data)
                if found_ani_id:
                    ani_id = found_ani_id
        except Exception:
            pass

        title_dict = {
            "english": item.get("title_english"),
            "romaji": item.get("title"),
            "native": item.get("title_japanese"),
        }

        format_mapping = {
            "TV": "TV",
            "Movie": "MOVIE",
            "OVA": "OVA",
            "ONA": "ONA",
            "Special": "SPECIAL",
            "Music": "MUSIC",
        }
        fmt = format_mapping.get(item.get("type"), "TV")

        status_mapping = {
            "Finished Airing": "FINISHED",
            "Currently Airing": "RELEASING",
            "Not yet aired": "NOT_YET_RELEASED",
        }
        status = status_mapping.get(item.get("status"), "FINISHED")

        season = item.get("season")
        if season:
            season = season.upper()

        genres = [g.get("name") for g in item.get("genres", [])]

        media_list.append({
            "id": ani_id,
            "idMal": mal_id,
            "title": title_dict,
            "description": item.get("synopsis"),
            "coverImage": {"large": item.get("images", {}).get("jpg", {}).get("large_image_url")},
            "bannerImage": None,
            "format": fmt,
            "status": status,
            "season": season,
            "seasonYear": item.get("year"),
            "episodes": item.get("episodes"),
            "averageScore": int(item.get("score", 0) * 10) if item.get("score") else None,
            "genres": genres,
        })

    return {"data": {"Page": {"media": media_list}}}


# Aliases for backward compatibility
search_tenrai = search_mal
search_mal_fallback = search_mal


def _fetch_jikan_fallback(query: str) -> tuple[bool, dict]:
    """Legacy helper returning (success, data) tuple for backward compatibility."""
    try:
        data = search_mal(query)
        return True, data
    except Exception as e:
        logger.debug("MAL fallback failed: %s", e)
        return False, {"data": {"Page": {"media": []}}}


def search_anilist_with_fallback(
    query: str,
    raw_gql_query: str = "",
    variables: dict | None = None,
    token: str = "",
) -> dict:
    """Unified search pipeline with automatic fallbacks:

    1. Attempt unauthenticated AniList search.
    2. On failure (e.g. 403 Forbidden / 429 / 5xx / timeout), try authenticated AniList if token exists.
    3. On failure (or if no token), fall back to MyAnimeList (MAL) APIs with MalSync AniList ID translation.
    4. If all fail, raise SearchFailure.
    """
    # 1. Unauthenticated AniList
    try:
        return search_anilist_public(query, raw_gql_query, variables)
    except (urllib.error.URLError, TimeoutError) as e:
        logger.debug("Public AniList search failed: %s. Trying authenticated fallback.", e)

    # 2. Authenticated AniList (if token available or provided)
    try:
        return search_anilist_authenticated(query, token, raw_gql_query, variables)
    except (ValueError, urllib.error.URLError, TimeoutError) as e:
        logger.debug("Authenticated AniList search unavailable/failed: %s. Trying MAL fallback.", e)

    # 3. MAL Fallback (with MalSync AniList ID mapping)
    try:
        return search_mal(query)
    except Exception as e:
        logger.debug("MAL fallback failed: %s", e)
        raise SearchFailure("API Error: AniList and fallback search providers are currently unavailable.") from e


