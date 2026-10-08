"""AniDB provider adapter using the current Anilab/AniDB APIs."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .shared.base import Provider
from .shared.hls import resolved_hls_streams_to_sources
from .shared.models import (
    normalize_episode_catalog,
    normalize_episode_sources,
    normalize_title,
    normalize_titles,
)

log = logging.getLogger(__name__)

API_BASE = "https://anilab2.amdapi.click"
PLAY_BASE = "https://play.anidb.app"
POST_ID_OFFSET = 1_000_000_000

NAV_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
}

PLAY_HEADERS = {
    **NAV_HEADERS,
    "Referer": "https://play.app/",
    "X-Requested-With": "PLAY",
}

LANGUAGE_NAMES = {
    "jpn": "Japanese",
    "eng": "English",
    "chi": "Chinese",
    "kor": "Korean",
}


def _status(value: str | None) -> str:
    text = str(value or "").casefold()
    if "airing" in text and "finished" not in text:
        return "RELEASING"
    if "finished" in text or "completed" in text:
        return "FINISHED"
    return ""


def _genres(value: str | None) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _year_from_premiered(value: str | None) -> int | None:
    import re

    match = re.search(r"\b(19\d{2}|20\d{2})\b", str(value or ""))
    return int(match.group(1)) if match else None


def _post_id(provider_id: str | int) -> str:
    raw = str(provider_id or "").strip().strip("/")
    import re

    match = re.search(r"(\d+)$", raw)
    if not match:
        raise ValueError(f"Invalid AniDB id: {provider_id}")
    value = int(match.group(1))
    if value < POST_ID_OFFSET:
        value += POST_ID_OFFSET
    return str(value)


def _episode_number(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _post_to_title(post: dict[str, Any], provider_name: str) -> dict[str, Any] | None:
    if not isinstance(post, dict) or not post.get("id"):
        return None
    year = _year_from_premiered(post.get("premiered"))
    payload = {
        "id": str(post.get("id")),
        "name": post.get("title") or str(post.get("id")),
        "thumbnail": post.get("poster") or "",
        "description": post.get("overview") or "",
        "type": post.get("type") or "TV",
        "status": _status(post.get("status")),
        "score": post.get("score"),
        "genres": _genres(post.get("genres")),
    }
    if year:
        payload["airedStart"] = {"year": year}
    return normalize_title(payload, provider_id="anidb", provider_name=provider_name, id_key="id")


class AniDBApp(Provider):
    id = "anidb"
    aliases = ["anidbapp"]
    audio_mode = "separate_catalogs"

    def __init__(self, request_json_fn=None):
        self._request_json = request_json_fn
        if not hasattr(self, "metadata"):
            self.metadata = {}
        if not hasattr(self, "domains"):
            self.domains = []

    @property
    def base_url(self) -> str:
        return self.domains[0] if getattr(self, "domains", None) else API_BASE

    @property
    def name(self) -> str:
        return self.metadata.get("name", "AniDB")

    def _fetch(self, url: str, headers: dict[str, str]):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                return response.status, response.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", errors="ignore")
        except Exception as e:
            return 500, str(e)

    def _fetch_json(self, url: str, headers: dict[str, str] | None = None) -> dict[str, Any] | None:
        status, text = self._fetch(url, headers or NAV_HEADERS)
        if status != 200:
            log.debug("AniDB request failed: HTTP %s %s", status, url)
            return None
        try:
            data = json.loads(text)
        except Exception:
            log.debug("AniDB JSON parse failed for %s", url)
            return None
        return data if isinstance(data, dict) else None

    def _fetch_post(self, provider_id: str | int) -> dict[str, Any] | None:
        post_id = _post_id(provider_id)
        return self._fetch_json(f"{self.base_url}/api/post?id={post_id}")

    def search(self, query: str, ttype: str = "sub") -> list[dict[str, Any]]:
        url = f"{self.base_url}/api/search?{urllib.parse.urlencode({'query': query, 'page': 1})}"
        data = self._fetch_json(url)
        posts = data.get("posts", []) if data else []
        results = []
        for item in posts[:30]:
            post_id = item.get("id") if isinstance(item, dict) else None
            if not post_id:
                continue
            post = self._fetch_post(post_id) or item
            title = _post_to_title(post, self.name)
            if title:
                results.append(title)
        return normalize_titles(results, provider_id=self.id, provider_name=self.name, id_key="_provider_id")

    def get_title(self, provider_id: str) -> dict[str, Any] | None:
        post = self._fetch_post(provider_id)
        return _post_to_title(post, self.name) if post else None

    def _episodes(self, provider_id: str) -> list[dict[str, Any]]:
        post_id = _post_id(provider_id)
        data = self._fetch_json(f"{PLAY_BASE}/api/anime/{post_id}/episodes")
        episodes = data.get("list", []) if data else []
        return [ep for ep in episodes if isinstance(ep, dict)]

    def _servers(self, episode_id: str) -> list[dict[str, Any]]:
        data = self._fetch_json(f"{PLAY_BASE}/api/episode/{episode_id}/servers")
        servers = data.get("list", []) if data else []
        unique = []
        seen = set()
        for server in servers:
            if not isinstance(server, dict):
                continue
            sid = str(server.get("id") or "")
            if sid and sid not in seen:
                seen.add(sid)
                unique.append(server)
        return unique

    def episode_catalog(self, provider_id: str, ttype: str = "sub") -> dict[str, Any]:
        try:
            episodes = self._episodes(provider_id)
        except Exception as exc:
            return normalize_episode_catalog({"state": "error", "error": str(exc)}, provider_id=self.id, provider_title_id=provider_id)

        ids = []
        labels = {}
        internal_map = {}
        for ep in episodes:
            number = _episode_number(ep.get("number"))
            ep_id = str(ep.get("id") or "")
            if not number or not ep_id:
                continue
            ids.append(number)
            internal_map[number] = ep_id
            labels[number] = ep.get("name") or f"Episode {number}"

        sub_ids = list(ids)
        dub_ids = []
        first_ep_id = internal_map.get(ids[0]) if ids else ""
        if first_ep_id:
            servers = self._servers(first_ep_id)
            if any(str(s.get("id") or "").endswith("/eng") or str(s.get("lang") or "").casefold() == "dub" for s in servers):
                dub_ids = list(ids)

        requested_ids = dub_ids if ttype == "dub" else sub_ids
        return normalize_episode_catalog(
            {
                "state": "loaded",
                "ids": requested_ids,
                "labels": labels,
                "detail": {"sub": sub_ids, "dub": dub_ids, "raw": []},
                "_internal_map": internal_map,
            },
            provider_id=self.id,
            provider_title_id=provider_id,
        )

    def episode_sources(
        self,
        provider_id: str,
        episode: str,
        ttype: str = "sub",
    ) -> dict[str, Any] | None:
        catalog = self.episode_catalog(provider_id, ttype)
        internal_map = catalog.get("_internal_map", {})
        ep_id = internal_map.get(str(episode))
        if not ep_id:
            log.debug("Episode %s not found in AniDB catalog for %s", episode, provider_id)
            return None

        desired_lang = "eng" if ttype == "dub" else "jpn"
        source_urls: list[dict[str, Any]] = []
        for server in self._servers(ep_id):
            server_id = str(server.get("id") or "")
            lang_code = server_id.rsplit("/", 1)[-1].casefold()
            if lang_code != desired_lang:
                continue
            iframe = self._fetch_json(f"{PLAY_BASE}/api/episode/{server_id}/iframe", PLAY_HEADERS)
            stream_url = str((iframe or {}).get("link") or "")
            if not stream_url:
                continue
            name = LANGUAGE_NAMES.get(lang_code, lang_code.upper())
            try:
                from ..extractors.base import BaseExtractor
                expanded = BaseExtractor().extract_m3u8(
                    stream_url,
                    name=name,
                    headers=NAV_HEADERS,
                    priority=0,
                )
                source_urls.extend(resolved_hls_streams_to_sources(expanded))
            except Exception:
                source_urls.append({
                    "sourceName": name,
                    "link": stream_url,
                    "type": "hls",
                    "resolution": "auto",
                    "priority": 0,
                    "headers": dict(NAV_HEADERS),
                })

        return normalize_episode_sources(
            {"episode": {"sourceUrls": source_urls}},
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
        return f"https://anidb.app/anime/{provider_id}"


PROVIDER_CLASS = AniDBApp
