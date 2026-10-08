import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .shared.base import Provider
from .shared.hls import parse_master_playlist_variants
from .shared.models import normalize_episode_catalog, normalize_episode_sources, normalize_title

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

class Senshi(Provider):
    id = "senshi"

    def __init__(self, request_json_fn=None, *args, **kwargs):
        self._request_json = request_json_fn
        if not hasattr(self, 'metadata'):
            self.metadata = {}
        if not hasattr(self, 'domains'):
            self.domains = []

    @property
    def base_url(self) -> str:
        return self.domains[0] if getattr(self, 'domains', None) else "https://senshi.to"

    @property
    def name(self) -> str:
        return self.metadata.get("name", "Senshi")

    def _fetch_json(self, url: str) -> Any:
        headers = dict(HEADERS)
        headers["Referer"] = f"{self.base_url}/"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=7) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            logging.debug("Senshi HTTP error: %s", e)
            return []
        except Exception as e:
            logging.debug("Senshi fetch error: %s", e)
            return []

    def search(self, query: str, ttype: str = "sub") -> list[dict[str, Any]]:
        # 1. Try Senshi native filter endpoint
        try:
            payload = json.dumps({
                "searchTerm": query,
                "types": [],
                "genres": [],
                "status": [],
                "seasons": [],
                "year": "",
                "studios": [],
                "producers": [],
                "languages": [],
                "page": 1,
                "limit": 20,
                "sortBy": "score_desc",
                "languagePreference": "EN",
            }).encode("utf-8")
            headers = dict(HEADERS)
            headers["Referer"] = f"{self.base_url}/"
            headers["Content-Type"] = "application/json"
            req = urllib.request.Request(f"{self.base_url}/anime/filter", data=payload, headers=headers)
            with urllib.request.urlopen(req, timeout=7) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                media_list = data.get("data") or []
                if media_list:
                    results = []
                    for item in media_list:
                        mal_id = item.get("id")
                        if not mal_id:
                            continue
                        name = item.get("title_english") or item.get("title") or "Unknown"
                        pic = item.get("anime_picture") or ""
                        if pic.startswith("/"):
                            pic = f"{self.base_url}{pic}"
                        results.append(normalize_title(
                            {
                                "_id": str(mal_id),
                                "malId": mal_id,
                                "name": name,
                                "romajiName": item.get("title") or "",
                                "englishName": item.get("title_english") or "",
                                "nativeName": "",
                                "thumbnail": pic,
                                "type": "anime",
                                "format": item.get("type"),
                                "status": item.get("ani_status") or "Finished",
                            },
                            provider_id=self.id,
                            provider_name=self.name,
                        ))
                    if results:
                        return results
        except Exception as e:
            logging.debug("Senshi native search error: %s", e)

        # 2. AniList GraphQL fallback to search and retrieve idMal
        graphql_query = """
        query($search: String) {
          Page(page: 1, perPage: 20) {
            media(search: $search, type: ANIME, sort: SEARCH_MATCH) {
              id
              idMal
              title { romaji english native }
              format
              status
            }
          }
        }
        """
        from ..core.anilist_fallback import search_anilist_with_fallback
        data = search_anilist_with_fallback(query, graphql_query, {"search": query})

        media_list = data.get("data", {}).get("Page", {}).get("media", [])

        results = []
        for media in media_list:
            mal_id = media.get("idMal")
            if not mal_id:
                continue

            title_obj = media.get("title", {})
            title_str = title_obj.get("romaji") or title_obj.get("english") or title_obj.get("native") or "Unknown"

            results.append(normalize_title(
                {
                    "_id": str(mal_id),
                    "malId": mal_id,
                    "aniListId": media.get("id"),
                    "name": title_str,
                    "romajiName": title_obj.get("romaji"),
                    "englishName": title_obj.get("english"),
                    "nativeName": title_obj.get("native"),
                    "type": "anime",
                    "format": media.get("format"),
                    "status": media.get("status")
                },
                provider_id=self.id,
                provider_name=self.name
            ))

        return results

    def get_title(self, provider_id: str) -> dict[str, Any]:
        return {
            "name": f"MAL ID: {provider_id}",
            "url": f"https://myanimelist.net/anime/{provider_id}",
        }

    def episode_catalog(self, provider_id: str, ttype: str = "sub") -> dict[str, Any]:
        episodes_data = self._fetch_json(f"{self.base_url}/episodes/{provider_id}")
        if not episodes_data:
            return normalize_episode_catalog(
                {"episodes": []},
                provider_id=self.id,
                provider_title_id=provider_id,
            )

        eps_formatted = []
        ids = []
        labels = {}

        for ep in episodes_data:
            ep_num = str(ep.get("ep_id"))
            if not ep_num or ep_num == "None":
                continue

            ids.append(ep_num)
            labels[ep_num] = ep_num
            eps_formatted.append({
                "id": ep_num,
                "label": ep_num
            })

        def _parse_ep(val):
            try: return float(val)
            except ValueError: return 0.0

        eps_formatted.sort(key=lambda x: _parse_ep(x["id"]))
        ids = [ep["id"] for ep in eps_formatted]

        return normalize_episode_catalog(
            {
                "state": "loaded",
                "ids": ids,
                "labels": labels,
                "episodes": {ttype: eps_formatted}
            },
            provider_id=self.id,
            provider_title_id=provider_id,
        )

    _sources_cache: dict[tuple[str, str, str], Any] = {}

    def _extract_embed_sources(self, matching_embeds: list, is_dub: bool) -> list[dict]:
        all_sources = []
        for emb in matching_embeds:
            status = emb.get("status") or ("Dub" if is_dub else "Sub")
            status_label = "Dub" if str(status).lower() == "dub" else "HardSub"
            remote_id = emb.get("remote_source_id") or emb.get("remoteSourceId")

            # 1. Direct Vidcloud source via remote_source_id
            if remote_id:
                try:
                    vheaders = {
                        "User-Agent": HEADERS["User-Agent"],
                        "Referer": f"{self.base_url}/",
                        "Origin": self.base_url,
                        "Accept": "application/json",
                    }
                    vreq = urllib.request.Request(
                        f"https://s.vidcloud.se/_v1/sources?id={remote_id}",
                        headers=vheaders,
                    )
                    with urllib.request.urlopen(vreq, timeout=7) as vresp:
                        entries = json.loads(vresp.read().decode("utf-8"))
                        for entry in entries:
                            src_info = entry.get("source") or {}
                            src_url = src_info.get("src")
                            if src_url:
                                tracks = entry.get("tracks") or []
                                subtitles = [
                                    {
                                        "url": t.get("vtt_url") or t.get("url"),
                                        "file": t.get("vtt_url") or t.get("url"),
                                        "label": t.get("label") or "English",
                                        "default": t.get("default", False),
                                    }
                                    for t in tracks
                                    if t.get("url") or t.get("vtt_url")
                                ]
                                quality = src_info.get("quality") or "1080p"

                                variants = []
                                try:
                                    mreq = urllib.request.Request(src_url, headers=vheaders)
                                    with urllib.request.urlopen(mreq, timeout=5) as mresp:
                                        m_body = mresp.read().decode("utf-8", errors="replace")
                                        if m_body.startswith("EM3U8v1:"):
                                            from ..media.decryption import decrypt_em3u8
                                            dec = decrypt_em3u8(m_body)
                                            if dec:
                                                m_body = dec
                                        variants = parse_master_playlist_variants(src_url, m_body)
                                except Exception:
                                    pass

                                if not variants:
                                    variants = [{"url": src_url, "resolution": quality}]

                                seen_variant_urls = set()
                                for variant in variants:
                                    variant_url = variant.get("url") or src_url
                                    if variant_url in seen_variant_urls:
                                        continue
                                    seen_variant_urls.add(variant_url)
                                    res = variant.get("resolution") or quality
                                    all_sources.append({
                                        "sourceName": f"[{status_label}] {res}",
                                        "link": variant_url,
                                        "sourceUrl": variant_url,
                                        "resolution": res,
                                        "type": "hls",
                                        "priority": 1 if res == "1080p" else 2,
                                        "referer": f"{self.base_url}/",
                                        "headers": {
                                            "Referer": f"{self.base_url}/",
                                            "Origin": self.base_url,
                                            "User-Agent": HEADERS["User-Agent"],
                                        },
                                        "requires_proxy": True,
                                        "subtitles": subtitles,
                                    })
                except Exception as exc:
                    logging.debug("Senshi vidcloud fetch error: %s", exc)

            direct_url = emb.get("url") or emb.get("embed_url") or emb.get("embedUrl")
            if direct_url and not any(
                direct_url in {src.get("streamUrl"), src.get("link"), src.get("sourceUrl")}
                for src in all_sources
            ):
                all_sources.append({
                    "sourceName": f"[{status_label}] auto",
                    "link": direct_url,
                    "streamUrl": direct_url,
                    "sourceUrl": direct_url,
                    "resolution": "auto",
                    "type": "hls" if ".m3u8" in str(direct_url) else "mp4",
                    "priority": 2,
                    "referer": f"{self.base_url}/",
                    "headers": {
                        "Referer": f"{self.base_url}/",
                        "Origin": self.base_url,
                        "User-Agent": HEADERS["User-Agent"],
                    },
                    "requires_proxy": True,
                })

            # 2. Server 2 (StreamNin)
            server2 = emb.get("server2")
            if server2:
                all_sources.append({
                    "sourceName": f"StreamNin ({status_label})",
                    "link": server2,
                    "resolution": "auto",
                    "type": "embed",
                    "priority": 3,
                    "headers": {"Referer": f"{self.base_url}/"},
                })

            # 3. Server FM (FileMoon)
            serverFM = emb.get("serverFM")
            if serverFM:
                all_sources.append({
                    "sourceName": f"FileMoon ({status_label})",
                    "link": serverFM,
                    "resolution": "auto",
                    "type": "embed",
                    "priority": 4,
                    "headers": {"Referer": f"{self.base_url}/"},
                })
        return all_sources

    def episode_sources(self, provider_id: str, episode: str, ttype: str = "sub") -> dict[str, Any] | None:
        cache_key = (str(provider_id), str(episode), str(ttype).lower())
        if cache_key in self._sources_cache:
            return self._sources_cache[cache_key]

        embeds = self._fetch_json(f"{self.base_url}/episode-embeds/{provider_id}/{episode}")
        if not embeds or not isinstance(embeds, list):
            self._sources_cache[cache_key] = None
            return None

        want_dub = (str(ttype).lower() == "dub")
        sub_embeds = [
            emb for emb in embeds
            if str(emb.get("status") or "").lower() != "dub"
        ]
        dub_embeds = [
            emb for emb in embeds
            if str(emb.get("status") or "").lower() == "dub"
        ]

        if not dub_embeds:
            self._sources_cache[(str(provider_id), str(episode), "dub")] = None
        if not sub_embeds:
            self._sources_cache[(str(provider_id), str(episode), "sub")] = None

        target_embeds = dub_embeds if want_dub else sub_embeds
        other_embeds = sub_embeds if want_dub else dub_embeds
        other_ttype = "sub" if want_dub else "dub"
        other_key = (str(provider_id), str(episode), other_ttype)

        if other_embeds and other_key not in self._sources_cache:
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                fut_target = executor.submit(self._extract_embed_sources, target_embeds, want_dub) if target_embeds else None
                fut_other = executor.submit(self._extract_embed_sources, other_embeds, not want_dub)

                if fut_target:
                    sources = fut_target.result()
                    res_target = normalize_episode_sources({
                        "episode": {"sourceUrls": sources}
                    }, provider_id=self.id, provider_title_id=provider_id, episode=episode) if sources else None
                else:
                    res_target = None
                self._sources_cache[cache_key] = res_target

                try:
                    oth_sources = fut_other.result()
                    self._sources_cache[other_key] = normalize_episode_sources({
                        "episode": {"sourceUrls": oth_sources}
                    }, provider_id=self.id, provider_title_id=provider_id, episode=episode) if oth_sources else None
                except Exception:
                    pass
        else:
            if not target_embeds:
                self._sources_cache[cache_key] = None
                res_target = None
            else:
                sources = self._extract_embed_sources(target_embeds, is_dub=want_dub)
                res_target = normalize_episode_sources({
                    "episode": {"sourceUrls": sources}
                }, provider_id=self.id, provider_title_id=provider_id, episode=episode) if sources else None
                self._sources_cache[cache_key] = res_target

        return res_target

    def browser_url(self, *, provider_id: str = "", episode: str = "") -> str:
        if provider_id and episode:
            return f"{self.base_url}/watch/{provider_id}/{episode}"
        if provider_id:
            return f"{self.base_url}/watch/{provider_id}/1"
        return self.base_url

PROVIDER_CLASS = Senshi
