"""Miruro provider adapter."""

from __future__ import annotations

import base64
import gzip
import json
import urllib.parse
import urllib.request
from typing import Any

from ..core import reporting
from ..services.http import SSL_CTX_SECURE, UA, request_json
from .shared.models import (
    normalize_episode_catalog,
    normalize_episode_sources,
    normalize_title,
    normalize_titles,
)

ACTIVE_DOMAIN = ""

ANILIST_URL = "https://graphql.anilist.co"

def _get_headers(domain: str) -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36",
        "Referer": f"{domain}/",
        "Origin": domain,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "sec-fetch-site": "same-origin",
        "sec-fetch-mode": "cors",
        "sec-fetch-dest": "empty",
        "sec-ch-ua": '"Chromium";v="110", "Not A(Brand";v="24", "Google Chrome";v="110"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }

def _encode_pipe_request(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')

def _decode_pipe_response(encoded_str: str) -> dict:
    encoded_str += '=' * (4 - len(encoded_str) % 4)
    compressed = base64.urlsafe_b64decode(encoded_str)
    return json.loads(gzip.decompress(compressed).decode('utf-8'))


def _load_curl_requests():
    try:
        from curl_cffi import requests
        return requests
    except Exception as exc:
        from ..core.api import ProviderDependencyError
        raise ProviderDependencyError(
            f"\n\033[91m[ERROR] Miruro requires the 'curl_cffi' library.\n"
            f"        Failed to import 'curl_cffi': {exc}\n\n"
            f"        How to install 'curl_cffi':\n"
            f"          pipx:    pipx inject allmanga-cli curl_cffi\n"
            f"          pip:     pip install curl_cffi\n"
            f"          termux:  pkg install python-curl-cffi\n\n"
            f"        Alternatively, switch to another provider:\n"
            f"          allmanga-cli --provider anikoto\033[0m\n"
        )


def _fetch_pipe_urllib(encoded_req: str, domain: str, headers: dict[str, str]) -> dict | None:
    safe_headers = {**headers, "Accept-Encoding": "identity"}
    request = urllib.request.Request(
        f"{domain}/api/secure/pipe?e={encoded_req}",
        headers=safe_headers,
        method="GET",
    )
    try:
        with urllib.request.urlopen(
            request,
            context=SSL_CTX_SECURE,
            timeout=8,
        ) as response:
            if response.status != 200:
                return None
            return _decode_pipe_response(response.read().decode("utf-8").strip())
    except Exception:
        return None


def _fetch_pipe(payload: dict, domains: list[str]) -> dict | None:
    global ACTIVE_DOMAIN
    encoded_req = _encode_pipe_request(payload)
    curl_requests = _load_curl_requests()

    if not domains:
        domains = ["https://www.miruro.tv"]

    if ACTIVE_DOMAIN not in domains:
        ACTIVE_DOMAIN = domains[0]

    # Put the active domain first
    domains_to_try = [ACTIVE_DOMAIN] + [d for d in domains if d != ACTIVE_DOMAIN]

    for domain in domains_to_try:
        headers = _get_headers(domain)
        if curl_requests is not None:
            for browser in ["chrome110", "chrome116"]:
                try:
                    res = curl_requests.get(
                        f"{domain}/api/secure/pipe?e={encoded_req}",
                        headers=headers,
                        impersonate=browser,
                        timeout=5,
                    )
                    if res.status_code == 200:
                        data = _decode_pipe_response(res.text.strip())
                        if data:
                            ACTIVE_DOMAIN = domain
                            return data
                    elif res.status_code in (403, 503):
                        # Cloudflare challenge or block; trying other impersonations won't help
                        break
                except Exception:
                    pass
        else:
            data = _fetch_pipe_urllib(encoded_req, domain, headers)
            if data:
                ACTIVE_DOMAIN = domain
                return data
    return None

class MiruroProvider:
    id = "miruro"
    audio_mode = "separate_catalogs"

    def __init__(self, request_json_fn=request_json):
        self._request_json = request_json_fn

        if not hasattr(self, 'domains'):
            self.domains = []
        if not hasattr(self, 'metadata'):
            self.metadata = {}

    def validate_environment(self) -> None:
        _load_curl_requests()

    @property
    def base_url(self) -> str:
        return self.domains[0] if getattr(self, 'domains', None) else "https://www.miruro.tv"

    @property
    def name(self) -> str:
        return self.metadata.get("name", "Miruro")
# ... [skipping search and get_title]
    def search(self, query: str, ttype: str = "sub") -> list[dict[str, Any]]:
        gql = """
        query ($search: String, $page: Int, $perPage: Int) {
            Page(page: $page, perPage: $perPage) {
                media(search: $search, type: ANIME, sort: SEARCH_MATCH) {
                    id
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
        from ..core.anilist_fallback import search_anilist_with_fallback
        res = search_anilist_with_fallback(query, gql, {"search": query, "page": 1, "perPage": 20})
        media_list = res.get("data", {}).get("Page", {}).get("media", [])

        results = []
        for media in media_list:
            results.append({
                "_id": str(media["id"]),
                "name": media["title"].get("romaji") or media["title"].get("english"),
                "romajiName": media["title"].get("romaji"),
                "englishName": media["title"].get("english"),
                "nativeName": media["title"].get("native"),
                "altNames": list(media.get("synonyms") or []),
                "thumbnail": (media.get("coverImage") or {}).get("large"),
                "banner": media.get("bannerImage"),
                "description": media.get("description"),
                "type": media.get("format"),
                "status": media.get("status"),
                "season": {"year": media.get("seasonYear"), "name": media.get("season")},
                "episodeCount": media.get("episodes"),
                "score": media.get("averageScore"),
                "genres": media.get("genres"),
                "aniListId": media["id"],
            })

        return normalize_titles(results, provider_id=self.id, provider_name=self.name)

    def get_title(self, provider_id: str) -> dict[str, Any] | None:
        gql = """
        query ($id: Int) {
            Media(id: $id, type: ANIME) {
                id
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
        """
        body = {"query": gql, "variables": {"id": int(provider_id)}}
        body_bytes = json.dumps(body).encode()
        try:
            from ..core.storage import load_config
            from ..services.anilist_auth import stored_anilist_token
            token = stored_anilist_token(load_config())
        except Exception:
            token = ""
        headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "Mozilla/5.0"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(ANILIST_URL, data=body_bytes, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                res = json.loads(r.read().decode("utf-8"))
        except Exception:
            res = {}
        media = res.get("data", {}).get("Media")
        if not media:
            return None

        title_dict = {
            "_id": str(media["id"]),
            "name": media["title"].get("romaji") or media["title"].get("english"),
            "romajiName": media["title"].get("romaji"),
            "englishName": media["title"].get("english"),
            "nativeName": media["title"].get("native"),
            "altNames": list(media.get("synonyms") or []),
            "thumbnail": (media.get("coverImage") or {}).get("large"),
            "banner": media.get("bannerImage"),
            "description": media.get("description"),
            "type": media.get("format"),
            "status": media.get("status"),
            "season": {"year": media.get("seasonYear"), "name": media.get("season")},
            "episodeCount": media.get("episodes"),
            "score": media.get("averageScore"),
            "genres": media.get("genres"),
            "aniListId": media["id"],
        }
        return normalize_title(title_dict, provider_id=self.id, provider_name=self.name)

    def episode_catalog(self, provider_id: str, ttype: str = "sub") -> dict[str, Any]:
        payload = {
            "path": "episodes",
            "method": "GET",
            "query": {"anilistId": int(provider_id)},
            "body": None,
            "version": "0.1.0",
        }

        data = _fetch_pipe(payload, self.domains)

        if not data:
            return normalize_episode_catalog({"state": "error", "error": "Miruro API blocked request or failed"}, provider_id=self.id, provider_title_id=provider_id)

        providers = data.get("providers", {})

        episodes_map = {}
        for p, p_data in providers.items():
            for ep in p_data.get("episodes", {}).get(ttype, []):
                ep_num = str(ep.get("number", ""))
                if ep_num and ep_num not in episodes_map:
                    episodes_map[ep_num] = ep

        # Sort episodes by number
        sorted_eps = sorted(episodes_map.values(), key=lambda x: float(x.get("number", 0)))

        ids = []
        labels = {}
        eps_formatted = []

        for ep in sorted_eps:
            ep_num = str(ep.get("number", ""))
            ids.append(ep_num)
            labels[ep_num] = ep_num
            eps_formatted.append({"id": ep_num, "label": ep_num})

        catalog = {
            "state": "loaded",
            "ids": ids,
            "labels": labels,
            "episodes": {ttype: eps_formatted}
        }
        return normalize_episode_catalog(catalog, provider_id=self.id, provider_title_id=provider_id)

    def episode_sources(
        self,
        provider_id: str,
        episode: str,
        ttype: str = "sub",
    ) -> dict[str, Any] | None:
        # 1. Fetch episodes catalog to get the raw_ids across all providers for this episode number
        payload = {
            "path": "episodes",
            "method": "GET",
            "query": {"anilistId": int(provider_id)},
            "body": None,
            "version": "0.1.0",
        }

        data = _fetch_pipe(payload, self.domains)

        if not data:
            return None

        providers = data.get("providers", {})
        fetch_tasks = []

        for p, p_data in providers.items():
            for ep in p_data.get("episodes", {}).get(ttype, []):
                if str(ep.get("number", "")) == str(episode):
                    fetch_tasks.append((p, ep["id"]))
                    break

        # Prioritize 'pewe' provider as default since it has higher success rate
        fetch_tasks.sort(key=lambda x: 0 if x[0].lower() == 'pewe' else 1)

        grouped_streams = {}
        for p, raw_id in fetch_tasks:

            try:
                padded_raw_id = raw_id + '=' * (4 - len(raw_id) % 4)
                decoded_id = base64.urlsafe_b64decode(padded_raw_id).decode('utf-8', errors='ignore')
                scraper_name = decoded_id.split(':')[0]
            except Exception:
                scraper_name = raw_id.split('/')[-1].rsplit('-', 1)[0]

            if not scraper_name:
                scraper_name = p
            scraper_name = scraper_name.title()
            reporting.info(f"[Miruro] Querying source: {scraper_name}...")

            spayload = {
                "path": "sources",
                "method": "GET",
                "query": {
                    "episodeId": raw_id,
                    "provider": p,
                    "anilistId": int(provider_id),
                    "category": ttype
                },
                "body": None,
                "version": "0.1.0"
            }
            sdata = _fetch_pipe(spayload, self.domains)

            if sdata:
                subtitles = []
                for sub in sdata.get("subtitles", []):
                    if sub.get("file"):
                        subtitles.append({
                            "url": sub["file"],
                            "label": sub.get("label", "Unknown"),
                            "kind": sub.get("kind", "captions")
                        })

                streams_found = 0
                for idx, stream in enumerate(sdata.get("streams", [])):
                    if stream.get("url"):
                        url = stream["url"]
                        is_direct = ".m3u8" in url or ".mp4" in url
                        server = stream.get("server", "Video")

                        parts = [p.title()]
                        if scraper_name.casefold() != p.casefold():
                            parts.append(scraper_name)
                        if server.casefold() != scraper_name.casefold() and server.casefold() != p.casefold():
                            parts.append(server)
                        parts.append("Direct" if is_direct else "Embed")

                        server_key = " ".join(parts)
                        if server_key in grouped_streams:
                            continue

                        source_dict = {
                            "sourceName": server_key,
                            "priority": len(grouped_streams),
                            "resolution": stream.get("quality", "1080p"),
                            "type": stream.get("type", "hls"),
                            "vtt": subtitles,
                            "headers": {
                                "User-Agent": UA
                            }
                        }
                        if stream.get("referer"):
                            source_dict["referer"] = stream.get("referer")

                        if is_direct:
                            source_dict["link"] = url
                        else:
                            source_dict["sourceUrl"] = url
                        grouped_streams[server_key] = source_dict
                        streams_found += 1

                if streams_found > 0:
                    reporting.ok(f"[Miruro] {scraper_name}: Found {streams_found} stream(s)")
                else:
                    reporting.warn(f"[Miruro] {scraper_name}: No valid streams found")
            else:
                reporting.warn(f"[Miruro] {scraper_name}: Unavailable / returned 0 streams")

        sourceUrls = list(grouped_streams.values())

        def _miruro_sort(src):
            name = src.get("sourceName", "").lower()
            if "pewe" in name or "anidbapp" in name:
                return 0
            if "allmanga" in name and "ok.ru" in name:
                return 1
            if "allmanga" in name:
                return 2
            return 3

        sourceUrls.sort(key=_miruro_sort)
        for idx, src in enumerate(sourceUrls):
            src["priority"] = idx

        return normalize_episode_sources(
            {"episode": {"sourceUrls": sourceUrls}},
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
        base = self.base_url
        if not provider_id:
            return base
        return f"{base}/anime/{provider_id}"

PROVIDER_CLASS = MiruroProvider
