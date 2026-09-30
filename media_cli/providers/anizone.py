"""AniZone provider adapter."""

from __future__ import annotations

import logging
import urllib.parse
import urllib.request
from typing import Any

from bs4 import BeautifulSoup

_logger = logging.getLogger(__name__)

from .shared.models import (
    normalize_episode_catalog,
    normalize_episode_sources,
    normalize_title,
    normalize_titles,
)


class AniZoneProvider:
    id = "anizone"
    audio_mode = "embedded_multi_audio"

    def __init__(self, request_json_fn=None):
        self._request_json = request_json_fn
        if not hasattr(self, 'metadata'):
            self.metadata = {}
        if not hasattr(self, 'domains'):
            self.domains = []
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36',
        }

    @property
    def base_url(self) -> str:
        return self.domains[0] if getattr(self, 'domains', None) else "https://anizone.to"

    @property
    def name(self) -> str:
        return self.metadata.get("name", "AniZone")

    def search(self, query: str, ttype: str = "sub") -> list[dict[str, Any]]:
        import json
        import re
        search_url = f"{self.base_url}/anime?search={urllib.parse.quote(query)}"
        req = urllib.request.Request(search_url, headers=self.headers)
        try:
            html = urllib.request.urlopen(req, timeout=8).read().decode('utf-8', errors='ignore')
            results = []
            seen_ids = set()

            # Parse Alpine.js items JSON
            m = re.search(r'items:\s*JSON\.parse\(([\'\"])(.*?)\1\)', html, re.DOTALL)
            if m:
                raw_json = m.group(2)
                # Unescape unicode and slashes
                try:
                    cleaned_json = raw_json.encode('utf-8').decode('unicode_escape')
                except Exception:
                    cleaned_json = raw_json.replace('\\"', '"').replace('\\/', '/')

                try:
                    items = json.loads(cleaned_json)
                except Exception:
                    # Fallback standard json load
                    items = json.loads(raw_json.replace('\\/', '/'))

                for it in items:
                    anime_id = it.get("slug")
                    if not anime_id or anime_id in seen_ids:
                        continue
                    seen_ids.add(anime_id)

                    title = it.get("main_title") or "Untitled"
                    title_list = it.get("title_list") or {}
                    english_title = title_list.get("1") or title_list.get("10") or title
                    native_title = title_list.get("8") or ""

                    cover = it.get("cover") or ""
                    if cover:
                        cover = cover.replace('\\/', '/')

                    ep_count = it.get("episode_count") or 0
                    is_ongoing = it.get("is_ongoing", False)
                    start_year = it.get("start_year")

                    anime_info = {
                        "_id": anime_id,
                        "name": title,
                        "englishName": english_title,
                        "nativeName": native_title,
                        "thumbnail": cover,
                        "episodeCount": ep_count,
                        "availableEpisodes": {"sub": ep_count, "dub": 0, "raw": 0},
                        "status": "RELEASING" if is_ongoing else "FINISHED",
                        "type": str(it.get("type") or "TV").upper(),
                        "airedStart": {"year": start_year} if start_year else None,
                        "genres": [t.get("name") for t in it.get("tags", []) if isinstance(t, dict) and t.get("name")],
                    }
                    results.append(anime_info)

            return normalize_titles(results, provider_id=self.id, provider_name=self.name, id_key="_id")
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            raise
        except Exception as e:
            _logger.debug("AniZone search error: %s", e)
            return []

    def get_title(self, provider_id: str) -> dict[str, Any] | None:
        import json
        import re
        anime_url = f"{self.base_url}/anime/{provider_id}"
        req = urllib.request.Request(anime_url, headers=self.headers)
        description = ""
        title = provider_id.replace('-', ' ').title()
        cover = ""
        try:
            html = urllib.request.urlopen(req, timeout=8).read().decode('utf-8', errors='ignore')
            soup = BeautifulSoup(html, 'html.parser')

            desc_tag = soup.find(class_=lambda c: c and ('desc' in c.lower() or 'synopsis' in c.lower()))
            if desc_tag:
                description = desc_tag.get_text(separator=' ', strip=True)
            else:
                for p in soup.find_all('p'):
                    text = p.get_text(strip=True)
                    if len(text) > 80:
                        description = text
                        break

            # Check title in x-data or meta tags
            m_titles = re.search(r'anmTitles:\s*JSON\.parse\(([\'\"])(.*?)\1\)', html, re.DOTALL)
            if m_titles:
                try:
                    t_data = json.loads(m_titles.group(2).encode('utf-8').decode('unicode_escape'))
                    title = t_data.get("1") or t_data.get("5") or title
                except Exception:
                    pass

            meta_img = soup.find('meta', property='og:image')
            if meta_img and meta_img.get('content'):
                cover = meta_img['content']

        except Exception as e:
            _logger.debug("AniZone get_title error: %s", e)

        return normalize_title(
            {
                "_id": provider_id,
                "name": title,
                "description": description,
                "thumbnail": cover,
            },
            provider_id=self.id,
            provider_name=self.name,
            id_key="_id"
        )

    def episode_catalog(self, provider_id: str, ttype: str = "sub") -> dict[str, Any]:
        import re
        ep_url = f"{self.base_url}/anime/{provider_id}/1"
        req = urllib.request.Request(ep_url, headers=self.headers)
        try:
            html = urllib.request.urlopen(req, timeout=8).read().decode('utf-8', errors='ignore')
            soup = BeautifulSoup(html, 'html.parser')
            ids = []
            seen_ids = set()

            for a in soup.find_all('a', href=True):
                href = a['href']
                m = re.search(rf'/anime/{re.escape(provider_id)}/(\d+)', href)
                if m:
                    ep_num = m.group(1)
                    if ep_num not in seen_ids:
                        ids.append(ep_num)
                        seen_ids.add(ep_num)

            if not ids:
                anime_url = f"{self.base_url}/anime/{provider_id}"
                req2 = urllib.request.Request(anime_url, headers=self.headers)
                html2 = urllib.request.urlopen(req2, timeout=8).read().decode('utf-8', errors='ignore')
                for a in BeautifulSoup(html2, 'html.parser').find_all('a', href=True):
                    m = re.search(rf'/anime/{re.escape(provider_id)}/(\d+)', a['href'])
                    if m:
                        ep_num = m.group(1)
                        if ep_num not in seen_ids:
                            ids.append(ep_num)
                            seen_ids.add(ep_num)

            if ids:
                ids.sort(key=lambda x: int(x) if x.isdigit() else x)
            else:
                ids = ['1']

            detail = {
                "sub": ids,
                "dub": [],
                "raw": [],
            }
            return normalize_episode_catalog({"ids": ids, "detail": detail}, provider_id=self.id, provider_title_id=provider_id)
        except Exception as e:
            _logger.debug("AniZone episode catalog error: %s", e)
            return normalize_episode_catalog({"ids": ["1"]}, provider_id=self.id, provider_title_id=provider_id)

    def episode_sources(
        self,
        provider_id: str,
        episode: str,
        ttype: str = "sub",
    ) -> dict[str, Any] | None:
        import json
        import re
        ep_url = f"{self.base_url}/anime/{provider_id}/{episode}"
        req = urllib.request.Request(
            ep_url,
            headers={
                **self.headers,
                "Referer": f"{self.base_url}/",
            }
        )
        try:
            html = urllib.request.urlopen(req, timeout=8).read().decode('utf-8', errors='ignore')
            source_urls = []

            # 1. Look for vidstackPlayer JSON
            m = re.search(r'vidstackPlayer\(JSON\.parse\(([\'\"])(.*?)\1\)', html, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(2).encode('utf-8').decode('unicode_escape'))
                    stream_url = data.get('src', '').replace('\\/', '/')
                    if stream_url:
                        subtitles = []
                        for sub in data.get("subtitles", []) or []:
                            s_file = str(sub.get("file") or "").replace('\\/', '/')
                            if s_file:
                                subtitles.append({
                                    "url": s_file,
                                    "label": sub.get("title") or (sub.get("language") or "Subtitle").title(),
                                    "language": sub.get("language", ""),
                                    "default": bool(sub.get("default")),
                                })
                        src_dict = {
                            "sourceName": "AniZone (VidStack)",
                            "streamUrl": stream_url,
                            "type": "hls",
                            "priority": 1,
                            "resolution": "auto",
                            "headers": {
                                "Referer": f"{self.base_url}/",
                                "Origin": self.base_url,
                                "User-Agent": self.headers['User-Agent'],
                            }
                        }
                        if subtitles:
                            src_dict["subtitles"] = subtitles
                            def_sub = next((s["url"] for s in subtitles if s.get("default")), subtitles[0]["url"])
                            src_dict["subtitle_url"] = def_sub
                        source_urls.append(src_dict)
                except Exception as e:
                    _logger.debug("AniZone vidstackPlayer JSON parse error: %s", e)

            # 2. Look for media-player tag
            if not source_urls:
                soup = BeautifulSoup(html, 'html.parser')
                media_player = soup.find('media-player')
                if media_player and media_player.get('src'):
                    stream_url = media_player.get('src').replace('\\/', '/')
                    source_urls.append({
                        "sourceName": "AniZone",
                        "streamUrl": stream_url,
                        "type": "hls",
                        "priority": 1,
                        "resolution": "auto",
                        "headers": {
                            "Referer": f"{self.base_url}/",
                            "Origin": self.base_url,
                            "User-Agent": self.headers['User-Agent'],
                        }
                    })

            # 3. Regex search for any direct master.m3u8 on vid-cdn or similar
            if not source_urls:
                m3u8_matches = re.findall(r'(https?://[^\s\"\'<>]+\.m3u8[^\s\"\'<>]*)', html)
                for u in m3u8_matches:
                    clean_u = u.replace('\\/', '/')
                    source_urls.append({
                        "sourceName": "AniZone (Direct)",
                        "streamUrl": clean_u,
                        "type": "hls",
                        "priority": 2,
                        "resolution": "auto",
                        "headers": {
                            "Referer": f"{self.base_url}/",
                            "Origin": self.base_url,
                            "User-Agent": self.headers['User-Agent'],
                        }
                    })

            return normalize_episode_sources(
                {"episode": {"sourceUrls": source_urls}},
                provider_id=self.id,
                provider_title_id=provider_id,
                episode=episode,
            )
        except Exception as e:
            _logger.debug("AniZone sources error: %s", e)
            return None

    def browser_url(
        self,
        provider_id: str,
        episode: str | None = None,
        ttype: str = "sub",
        cfg: dict[str, Any] | None = None,
    ) -> str:
        if not episode:
            return f"{self.base_url}/anime/{provider_id}"
        return f"{self.base_url}/anime/{provider_id}/{episode}"

PROVIDER_CLASS = AniZoneProvider

