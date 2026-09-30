"""AllAnime provider adapter."""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
from typing import Any

from ..core.api import SearchFailure
from ..domain.episodes import normalize_episode_ids
from ..media.decryption import decrypt_tobeparsed, encrypt_aa_req
from ..media.urls import validate_http_url
from ..services.http import API_BASE, CLOCK_BASE, request_json
from .shared.models import (
    normalize_episode_catalog,
    normalize_episode_sources,
    normalize_title,
    normalize_titles,
)

_logger = logging.getLogger(__name__)

ALLANIME_QUERY_HASH = "1c836a5028e04275c6bc618aa4d1f0ea2290a73bc056ba6a8b93fe72ef42fd04"
ALLANIME_EPOCH = 2958
ALLANIME_BUILD_ID = "168"
ALLANIME_LANE = "k7"

SEARCH_QUERY = (
    "query($search:SearchInput $limit:Int $page:Int "
    "$translationType:VaildTranslationTypeEnumType "
    "$countryOrigin:VaildCountryOriginEnumType){"
    "shows(search:$search limit:$limit page:$page "
    "translationType:$translationType countryOrigin:$countryOrigin)"
    "{edges{_id name englishName nativeName altNames thumbnail description type "
    "season score genres availableEpisodes status episodeCount airedStart "
    "aniListId malId}}}"
)

def search_anime(request_json, query, ttype="sub"):
    response = request_json(
        API_BASE,
        json.dumps({
            "query": SEARCH_QUERY,
            "variables": {
                "search": {
                    "allowAdult": False,
                    "allowUnknown": False,
                    "query": query,
                },
                "limit": 40,
                "page": 1,
                "translationType": ttype,
                "countryOrigin": "ALL",
            },
        }).encode(),
    )
    if response.get("errors"):
        raise SearchFailure("AllAnime rejected the search request.")
    return response.get("data", {}).get("shows", {}).get("edges", [])

def get_show(request_json, show_id):
    query = (
        "query($showId:String!){show(_id:$showId)"
        "{_id name englishName nativeName altNames thumbnail description type season "
        "score genres availableEpisodes status episodeCount airedStart "
        "aniListId malId}}"
    )
    response = request_json(
        API_BASE,
        json.dumps({
            "query": query,
            "variables": {"showId": show_id},
        }).encode(),
    )
    return response.get("data", {}).get("show")

def fetch_episode_catalog(request_json, show_id, ttype="sub"):
    query = (
        "query($showId:String!){"
        "show(_id:$showId){availableEpisodesDetail}}"
    )
    try:
        response = request_json(
            API_BASE,
            json.dumps({
                "query": query,
                "variables": {"showId": show_id},
            }).encode(),
        )
        if response.get("errors"):
            return {
                "state": "unavailable",
                "ids": [],
                "error": "Provider rejected the episode catalog request.",
            }
        show_data = response.get("data", {}).get("show")
        if not isinstance(show_data, dict):
            return {
                "state": "unavailable",
                "ids": [],
                "error": "Provider returned no episode catalog.",
            }
        detail = show_data.get("availableEpisodesDetail")
        if not isinstance(detail, dict):
            return {
                "state": "unavailable",
                "ids": [],
                "error": "Provider returned an invalid episode catalog.",
            }
        episodes = list(detail.get(ttype) or [])
        episodes.reverse()
        return {
            "state": "loaded",
            "ids": normalize_episode_ids(episodes),
            "detail": detail,
            "error": "",
        }
    except Exception as exc:
        _logger.debug(
            "fetch_episode_catalog(%r, %r) failed: %s",
            show_id,
            ttype,
            exc,
            exc_info=True,
        )
        return {
            "state": "unavailable",
            "ids": [],
            "error": f"Could not load the provider episode catalog: {exc}",
        }

def get_episode_data(request_json, show_id, episode, ttype="sub"):
    variables = {
        "showId": show_id,
        "translationType": ttype,
        "episodeString": str(episode),
    }
    ts = int(time.time() // 300 * 300 * 1000)
    aa_req_payload = json.dumps(
        {
            "v": 1,
            "ts": ts,
            "epoch": ALLANIME_EPOCH,
            "buildId": ALLANIME_BUILD_ID,
            "qh": ALLANIME_QUERY_HASH,
            "k": ALLANIME_LANE,
        },
        separators=(",", ":"),
    )
    aa_req_iv_seed = (
        f"{ALLANIME_EPOCH}:{ALLANIME_BUILD_ID}:"
        f"{ALLANIME_QUERY_HASH}:{ts}:{ALLANIME_LANE}"
    )
    extensions = {
        "persistedQuery": {"version": 1, "sha256Hash": ALLANIME_QUERY_HASH},
        "k": ALLANIME_LANE,
        "aaReq": encrypt_aa_req(aa_req_payload, aa_req_iv_seed),
    }
    variables_json = json.dumps(variables, separators=(",", ":"))
    extensions_json = json.dumps(extensions, separators=(",", ":"))
    url = (
        f"{API_BASE}?variables={urllib.parse.quote(variables_json)}"
        f"&extensions={urllib.parse.quote(extensions_json)}"
    )
    response = request_json(
        url,
        extra_hdrs={
            "Origin": "https://mkissa.to",
            "Referer": "https://mkissa.to/",
            "x-build-id": ALLANIME_BUILD_ID,
        },
    )
    if not response or response.get("errors"):
        return get_episode_cdn_data(request_json, show_id, episode, ttype)
    raw = response.get("data", {}).get("tobeparsed")
    if not raw:
        episode_data = response.get("data", {}).get("episode") or {}
        raw = episode_data.get("sourceUrls")
    if not raw:
        return get_episode_cdn_data(request_json, show_id, episode, ttype)
    decoded = decrypt_tobeparsed(raw)
    return json.loads(decoded) if decoded else get_episode_cdn_data(request_json, show_id, episode, ttype)


def get_episode_cdn_data(request_json, show_id, episode, ttype="sub"):
    field_by_type = {
        "sub": "vidInforssub",
        "dub": "vidInforsdub",
        "raw": "vidInforsraw",
    }
    info_field = field_by_type.get(str(ttype or "sub").lower(), "vidInforssub")
    try:
        episode_num = float(str(episode).strip())
    except ValueError:
        episode_num = 0.0
    if episode_num <= 0:
        return None

    query = (
        "{episodeInfos(showId:\""
        + str(show_id)
        + "\",episodeNumStart:"
        + str(episode_num)
        + ",episodeNumEnd:"
        + str(episode_num)
        + "){_id episodeIdNum vidInforssub vidInforsdub vidInforsraw}}"
    )
    response = request_json(
        API_BASE,
        json.dumps({"query": query}).encode(),
        extra_hdrs={
            "Origin": "https://mkissa.to",
            "Referer": "https://mkissa.to/",
        },
    )
    episodes = response.get("data", {}).get("episodeInfos") or []
    match = next(
        (
            item
            for item in episodes
            if str(item.get("episodeIdNum")) == str(int(episode_num))
            or str(item.get("episodeIdNum")) == str(episode_num)
        ),
        episodes[0] if episodes else None,
    )
    if not isinstance(match, dict):
        return None

    info = match.get(info_field)
    if not isinstance(info, dict) or not info.get("vidPath"):
        return None

    stream_url = "https://allanimenews.com/" + str(info["vidPath"]).lstrip("/")
    resolution = str(info.get("vidResolution") or "1080")
    if resolution.isdigit():
        resolution = f"{resolution}p"
    return {
        "episode": {
            "episodeString": str(episode),
            "sourceUrls": [
                {
                    "sourceName": "AllAnime CDN",
                    "sourceUrl": stream_url,
                    "type": "mp4",
                    "resolution": resolution,
                    "priority": 1,
                    "referer": "https://allanimenews.com/",
                    "headers": {
                        "Referer": "https://allanimenews.com/",
                        "Origin": "https://allanimenews.com",
                    },
                    "android_safe": True,
                }
            ],
        }
    }

def get_clock_links(request_json, path):
    return request_json(f"https://{CLOCK_BASE}{path}").get("links", [])


class AllAnimeProvider:
    id = "mkissa"
    aliases = ["allanime"]
    audio_mode = "separate_catalogs"

    def __init__(self, request_json_fn=request_json):
        self._request_json = request_json_fn
        if not hasattr(self, 'metadata'):
            self.metadata = {}
        if not hasattr(self, 'domains'):
            self.domains = []

    @property
    def name(self) -> str:
        return self.metadata.get("name", "Mkissa")

    def search(self, query: str, ttype: str = "sub") -> list[dict[str, Any]]:
        results = search_anime(self._request_json, query, ttype)
        return normalize_titles(
            results,
            provider_id=self.id,
            provider_name=self.name,
        )

    def get_title(self, provider_id: str) -> dict[str, Any] | None:
        return normalize_title(
            get_show(self._request_json, provider_id),
            provider_id=self.id,
            provider_name=self.name,
        )

    def episode_catalog(self, provider_id: str, ttype: str = "sub") -> dict[str, Any]:
        return normalize_episode_catalog(
            fetch_episode_catalog(
                self._request_json,
                provider_id,
                ttype,
            ),
            provider_id=self.id,
            provider_title_id=provider_id,
        )

    def episode_sources(
        self,
        provider_id: str,
        episode: str,
        ttype: str = "sub",
    ) -> dict[str, Any] | None:
        data = get_episode_data(
            self._request_json,
            provider_id,
            episode,
            ttype,
        )
        if data and isinstance(data.get("sourceUrls"), list):
            for src in data["sourceUrls"]:
                name = src.get("sourceName", "").strip().casefold()
                url = src.get("sourceUrl", "")

                if "yt-mp4" in name or "fast4speed" in url or "wixstatic" in url:
                    src["priority"] = 2
                elif name == "default":
                    src["priority"] = 3
                elif name == "ak":
                    src["priority"] = 4
                elif name in {"mp4", "mp4upload"}:
                    src["priority"] = 5
                elif name == "ok" or "ok.ru" in name:
                    src["priority"] = 6
                elif url.startswith("--"):
                    src["priority"] = 6
                elif any(val in name for val in ("fm-hls", "filemoon")):
                    src["priority"] = 7
                else:
                    src["priority"] = 8

        return normalize_episode_sources(
            data,
            provider_id=self.id,
            provider_title_id=provider_id,
            episode=episode,
        )

    def browser_url(
        self,
        provider_id: str,
        episode: str | None = None,
        ttype: str = "sub",
        cfg: dict[str, Any] | None = None,
    ) -> str:
        base = self.domains[0] if self.domains else _frontend_domain(cfg)
        provider_id = str(provider_id or "").strip()
        if not provider_id:
            return base
        encoded_id = urllib.parse.quote(provider_id, safe="")
        if episode is None or str(episode).strip() == "":
            return f"{base}/anime/{encoded_id}"
        safe_ttype = str(ttype or "sub").strip().lower()
        if safe_ttype not in ("sub", "dub", "raw"):
            safe_ttype = "sub"
        encoded_episode = urllib.parse.quote(str(episode).strip(), safe="")
        return f"{base}/anime/{encoded_id}/p-{encoded_episode}-{safe_ttype}"


PROVIDER_CLASS = AllAnimeProvider


def _frontend_domain(cfg: dict[str, Any] | None = None) -> str:
    default = "https://mkissa.to"
    candidate = str((cfg or {}).get("allanime_frontend_domain") or default).strip()
    try:
        return validate_http_url(candidate).rstrip("/")
    except ValueError:
        return default
