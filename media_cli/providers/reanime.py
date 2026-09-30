"""Re:ANIME provider adapter (reanime.to / reanime.cz / reanime.wtf)."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import re
import urllib.parse
import urllib.request
from typing import Any

from ..media.sources import parse_resolution_height
from ..services.http import SSL_CTX, UA
from .shared.base import Provider
from .shared.models import (
    normalize_episode_catalog,
    normalize_episode_sources,
    normalize_titles,
)

_logger = logging.getLogger(__name__)

try:
    from curl_cffi import requests as cffi_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    cffi_requests = None
    CURL_CFFI_AVAILABLE = False


def _reanime_src_sort_key(s: dict, ttype: str = "sub") -> tuple:
    """Sort Re:ANIME sources: matching audio mode first, then highest resolution, then direct MKV first."""
    res_str = s.get("resolution") or s.get("sourceName", "")
    h = parse_resolution_height(res_str)
    sname = (s.get("sourceName") or "").lower()
    audio = (s.get("audio") or "").lower()
    is_dub = "dub" in audio or "[dub]" in sname
    audio_penalty = 1 if (ttype == "sub" and is_dub) or (ttype == "dub" and not is_dub) else 0
    direct_bonus = 0 if "mkv" in sname or s.get("type") == "mp4" else 1
    prio = s.get("priority", 2)
    return (audio_penalty, direct_bonus, prio, -h)


class ReAnimeProvider(Provider):
    """Re:ANIME provider using internal REST API and FlixCloud streams."""

    id = "reanime"
    audio_mode = "embedded_multi_audio"

    def __init__(self, request_json_fn=None):
        self._request_json = request_json_fn
        if not hasattr(self, "metadata"):
            self.metadata = {}
        if not hasattr(self, "domains") or not self.domains:
            self.domains = [
                "https://reanime.to",
                "https://reanime.cz",
                "https://reanime.wtf",
            ]
        self._anime_meta: dict[str, dict[str, Any]] = {}
        self._session = None

    @property
    def base_url(self) -> str:
        return self.domains[0] if self.domains else "https://reanime.to"

    @property
    def name(self) -> str:
        return self.metadata.get("name", "Re:ANIME")

    def _get_session(self):
        if self._session is None and CURL_CFFI_AVAILABLE and cffi_requests is not None:
            self._session = cffi_requests.Session(impersonate="chrome")
        return self._session

    def _fetch_page(self, url: str, headers: dict[str, str] | None = None, timeout: int = 12) -> str:
        req_headers = {
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"{self.base_url}/",
        }
        if headers:
            req_headers.update(headers)

        sess = self._get_session()
        if sess is not None:
            try:
                resp = sess.get(url, headers=req_headers, timeout=timeout)
                if resp.status_code == 200:
                    return resp.text
            except Exception as e:
                _logger.debug("Re:ANIME cffi fetch failed on %s: %s", url, e)

        req = urllib.request.Request(url, headers=req_headers)
        try:
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="ignore")
        except Exception as e:
            _logger.debug("Re:ANIME urllib fetch failed on %s: %s", url, e)
            return ""

    def _fetch_json(self, path: str, referer: str | None = None, timeout: int = 12) -> dict | None:
        headers = {"Referer": referer or f"{self.base_url}/"}
        for domain in self.domains:
            url = f"{domain}{path}" if path.startswith("/") else f"{domain}/{path}"
            raw = self._fetch_page(url, headers=headers, timeout=timeout)
            if not raw:
                continue
            try:
                data = json.loads(raw)
                if isinstance(data, (dict, list)):
                    return data
            except Exception:
                pass
        return None

    def search(self, query: str, ttype: str = "sub") -> list[dict[str, Any]]:
        if not query or not query.strip():
            return []

        q_clean = query.strip()
        path = f"/api/v1/search?q={urllib.parse.quote(q_clean)}&limit=36&offset=0"
        data = self._fetch_json(path, referer=f"{self.base_url}/search")

        if not data or not isinstance(data, dict):
            return []

        results = []
        for item in data.get("results") or []:
            anime_id = item.get("anime_id")
            if not anime_id:
                continue

            anilist_id = item.get("anilist_id")
            title_obj = item.get("title") or {}
            eng_title = title_obj.get("english")
            romaji_title = title_obj.get("romaji")
            native_title = title_obj.get("native")
            display_title = eng_title or romaji_title or native_title or anime_id

            cover_obj = item.get("cover_image") or {}
            poster = cover_obj.get("extra_large") or cover_obj.get("large") or cover_obj.get("medium") or ""

            ep_count = item.get("episodes") or 0
            sub_count = item.get("subbed") or 0
            dub_count = item.get("dubbed") or 0
            if not ep_count:
                ep_count = max(sub_count, dub_count)

            status_raw = str(item.get("status") or "").lower()
            status = "FINISHED" if status_raw in ("finished", "completed") else "RELEASING"
            start_year = item.get("season_year")

            self._anime_meta[anime_id] = {
                "anilist_id": anilist_id,
                "subbed": sub_count,
                "dubbed": dub_count,
                "title": display_title,
            }

            results.append({
                "_id": anime_id,
                "name": display_title,
                "englishName": eng_title,
                "romajiName": romaji_title,
                "nativeName": native_title,
                "thumbnail": poster,
                "episodeCount": ep_count,
                "availableEpisodes": {"sub": sub_count, "dub": dub_count, "raw": 0},
                "status": status,
                "type": str(item.get("format") or "TV").upper(),
                "airedStart": {"year": start_year} if start_year else None,
                "anilist_id": anilist_id,
            })

        return normalize_titles(results, provider_id=self.id, provider_name=self.name, id_key="_id")

    def get_title(self, provider_id: str) -> dict[str, Any] | None:
        path = f"/api/v1/anime/{provider_id}"
        data = self._fetch_json(path, referer=f"{self.base_url}/anime/{provider_id}")
        if not data or not isinstance(data, dict):
            return None

        title_obj = data.get("title") or {}
        display_title = title_obj.get("english") or title_obj.get("romaji") or title_obj.get("native") or provider_id
        cover_obj = data.get("cover_image") or {}
        poster = cover_obj.get("extra_large") or cover_obj.get("large") or cover_obj.get("medium") or ""
        banner = data.get("banner_image") or ""
        if not banner and data.get("artworks"):
            for art in data.get("artworks", []):
                if art.get("image_type") == "banner" and art.get("url"):
                    banner = art.get("url")
                    break

        anilist_id = data.get("anilist_id")
        self._anime_meta[provider_id] = {
            "anilist_id": anilist_id,
            "subbed": data.get("subbed") or 0,
            "dubbed": data.get("dubbed") or 0,
            "title": display_title,
        }

        return {
            "_id": provider_id,
            "name": display_title,
            "thumbnail": poster,
            "banner": banner,
            "description": data.get("description"),
            "anilist_id": anilist_id,
        }

    def episode_catalog(self, provider_id: str, ttype: str = "sub") -> dict[str, Any]:
        path = f"/api/v1/anime/{provider_id}/episodes?limit=2000"
        data = self._fetch_json(path, referer=f"{self.base_url}/anime/{provider_id}")
        if not data or not isinstance(data, dict):
            return normalize_episode_catalog({"state": "empty", "ids": [], "labels": {}}, provider_id=self.id, provider_title_id=provider_id)

        items = data.get("data") or []
        if not items:
            return normalize_episode_catalog({"state": "empty", "ids": [], "labels": {}}, provider_id=self.id, provider_title_id=provider_id)

        # Sort episodes by episode number
        def _ep_num(it):
            num = it.get("episode_number")
            try:
                return float(num)
            except Exception:
                return 0.0

        items.sort(key=_ep_num)

        ids = []
        labels = {}
        sub_eps = []
        dub_eps = []

        for it in items:
            num = it.get("episode_number")
            if num is None:
                continue
            ep_id = str(int(num)) if isinstance(num, (int, float)) and int(num) == num else str(num)
            label = ep_id
            title = it.get("title") or ""
            if title and not title.lower().startswith("episode"):
                label = f"{ep_id} - {title}"

            ids.append(ep_id)
            labels[ep_id] = ep_id

            if it.get("subbed", True):
                sub_eps.append({"id": ep_id, "label": label})
            if it.get("dubbed", False):
                dub_eps.append({"id": ep_id, "label": label})

        return normalize_episode_catalog({
            "state": "loaded" if ids else "empty",
            "ids": ids,
            "labels": labels,
            "episodes": {
                "sub": sub_eps if sub_eps else [{"id": eid, "label": labels[eid]} for eid in ids],
                "dub": dub_eps,
                "raw": [],
            },
        }, provider_id=self.id, provider_title_id=provider_id)

    def _resolve_direct_mkv(self, server: dict) -> dict[str, Any] | None:
        """Resolve unencrypted master MKV stream from FlixCloud download endpoint."""
        link = server.get("dataLink") or ""
        aid_match = re.search(r"/e/([a-z0-9]+)", link)
        if not aid_match:
            return None
        aid = aid_match.group(1)

        url = f"https://flixcloud.cc/d/{aid}/__data.json"
        text = self._fetch_page(url, headers={"Referer": "https://flixcloud.cc/"}, timeout=8)
        if not text:
            return None

        fid_m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)
        tok_m = re.search(r"eyJ[\w-]+\.[\w-]+\.[\w-]+", text)
        base_m = re.search(r"https://fetch\d*\.flixcloud\.cc", text)

        if not (fid_m and tok_m):
            return None

        file_id = fid_m.group(0)
        token = tok_m.group(0)
        base = base_m.group(0) if base_m else "https://flixcloud.cc"
        res_m = re.search(r"(\d{3,4}p)", text)
        resolution = res_m.group(1) if res_m else "1080p"

        stream_url = f"{base}/download/{file_id}?token={token}"
        server_name = server.get("serverName") or "HD-1"
        data_type = (server.get("dataType") or "sub").lower()

        return {
            "sourceName": f"Re:ANIME ({server_name} · {resolution} MKV [{data_type.upper()}])",
            "link": stream_url,
            "streamUrl": stream_url,
            "sourceUrl": stream_url,
            "url": stream_url,
            "type": "mp4",
            "resolution": resolution,
            "priority": 1,
            "audio": data_type,
            "headers": {"Referer": "https://flixcloud.cc/", "User-Agent": UA},
            "android_safe": True,
        }

    def episode_sources(
        self,
        provider_id: str,
        episode: str,
        ttype: str = "sub",
    ) -> dict[str, Any] | None:
        # Determine anilist_id
        meta = self._anime_meta.get(provider_id)
        anilist_id = meta.get("anilist_id") if meta else None

        if not anilist_id:
            if provider_id.isdigit():
                anilist_id = int(provider_id)
            else:
                title_data = self.get_title(provider_id)
                if title_data and title_data.get("anilist_id"):
                    anilist_id = title_data.get("anilist_id")

        if not anilist_id:
            _logger.debug("Could not determine anilist_id for %s", provider_id)
            return None

        # Clean episode number
        ep_num = str(episode).removeprefix("ep-").split("/")[-1].strip()

        # Call Re:ANIME flix endpoint
        path = f"/api/flix/{anilist_id}/{ep_num}"
        referer = f"{self.base_url}/watch/{provider_id}?ep={ep_num}"
        data = self._fetch_json(path, referer=referer)

        if not data or not isinstance(data, dict):
            return None

        servers = data.get("servers") or []
        if not servers:
            return None

        source_urls = []

        # 1. Concurrently resolve direct MKV streams
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(servers), 4)) as executor:
            mkv_results = list(executor.map(self._resolve_direct_mkv, servers))

        for stream in mkv_results:
            if stream:
                source_urls.append(stream)

        # 2. Add embed streams only as fallback if no direct MKV stream was resolved
        if not source_urls:
            for s in servers:
                link = s.get("dataLink")
                if not link:
                    continue
                server_name = s.get("serverName") or "HD"
                data_type = (s.get("dataType") or "sub").lower()
                source_urls.append({
                    "sourceName": f"Re:ANIME ({server_name} [{data_type.upper()}])",
                    "sourceUrl": link,
                    "type": "embed",
                    "resolution": "1080p",
                    "priority": 3,
                    "audio": data_type,
                    "headers": {"Referer": f"{self.base_url}/", "User-Agent": UA},
                })

        # Sort sources using _reanime_src_sort_key: matching ttype first, high resolution first, direct MKV first
        source_urls.sort(key=lambda s: _reanime_src_sort_key(s, ttype=ttype))

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
        if not episode:
            return f"{self.base_url}/anime/{provider_id}"
        ep_num = str(episode).removeprefix("ep-").split("/")[-1].strip()
        return f"{self.base_url}/watch/{provider_id}?ep={ep_num}"


PROVIDER_CLASS = ReAnimeProvider
