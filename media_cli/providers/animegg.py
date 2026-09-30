import html
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .shared.base import Provider
from .shared.models import (
    normalize_episode_catalog,
    normalize_episode_sources,
    normalize_title,
    normalize_titles,
)

log = logging.getLogger(__name__)

# Basic headers for web scraping
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
}

class AnimeGG(Provider):
    id = "animegg"
    audio_mode = "separate_catalogs"

    def __init__(self, request_json_fn=None):
        self._request_json = request_json_fn
        if not hasattr(self, 'metadata'):
            self.metadata = {}
        if not hasattr(self, 'domains'):
            self.domains = []

    @property
    def base_url(self) -> str:
        return self.domains[0] if getattr(self, 'domains', None) else "https://www.animegg.org"

    @property
    def name(self) -> str:
        return self.metadata.get("name", "AnimeGG")

    def _fetch_html(self, url: str) -> str:
        headers = dict(HEADERS)
        headers["Referer"] = self.base_url
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            log.debug(f"AnimeGG fetch failed {url}: {e.code}")
            return ""
        except Exception as e:
            log.debug(f"AnimeGG fetch failed {url}: {e}")
            return ""

    def search(self, query: str, ttype: str = "sub") -> list[dict[str, Any]]:
        url = f"{self.base_url}/search/?q={urllib.parse.quote(query)}"
        headers = dict(HEADERS)
        headers["Referer"] = self.base_url
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as response:
            html_content = response.read().decode("utf-8")

        results = []
        pattern = r'<a\b[^>]*href=["\']/series/([^/"\']+)["\'][^>]*class=["\'][^"\']*\bmse\b[^"\']*["\'][^>]*>([\s\S]*?)</a>'
        for match in re.finditer(pattern, html_content, re.IGNORECASE):
            slug = match.group(1)
            tag_content = match.group(2)

            strong_match = re.search(r'<h2[^>]*>(.*?)</h2>|<strong[^>]*>(.*?)</strong>', tag_content, re.IGNORECASE)
            if strong_match:
                title = re.sub(r'<[^>]+>', '', strong_match.group(1) or strong_match.group(2)).strip()
            else:
                title = slug.replace("-", " ")

            img_match = re.search(r'<img\b[^>]*src=["\']([^"\']+)["\']', tag_content, re.IGNORECASE)
            thumbnail = img_match.group(1) if img_match else ""
            if thumbnail and not thumbnail.startswith("http"):
                thumbnail = f"{self.base_url}{thumbnail}"

            status_match = re.search(r'Status\s*:\s*(.*?)</div>', tag_content, re.IGNORECASE)
            status = re.sub(r'<[^>]+>', '', status_match.group(1)).strip() if status_match else ""
            if status.lower() == "completed":
                status = "FINISHED"
            elif status.lower() == "ongoing":
                status = "RELEASING"

            alt_match = re.search(r'Alt Titles\s*:\s*(.*?)</div>', tag_content, re.IGNORECASE)
            alt_names = [x.strip() for x in alt_match.group(1).split(',')] if alt_match else []

            eps_match = re.search(r'Episodes\s*:\s*(\d+)', tag_content, re.IGNORECASE)
            episode_count = int(eps_match.group(1)) if eps_match else None

            t_upper = title.upper()
            if "OVA" in t_upper:
                media_type = "OVA"
            elif "MOVIE" in t_upper:
                media_type = "MOVIE"
            elif "SPECIAL" in t_upper:
                media_type = "SPECIAL"
            else:
                media_type = "UNKNOWN"

            results.append({
                "id": slug,
                "name": title,
                "type": media_type,
                "thumbnail": thumbnail,
                "status": status,
                "altNames": alt_names,
                "episodeCount": episode_count
            })

        return normalize_titles(results, provider_id=self.id, provider_name=self.name, id_key="id")

    def get_title(self, provider_id: str) -> dict[str, Any] | None:
        url = f"{self.base_url}/series/{provider_id}"
        html_content = self._fetch_html(url)

        # fallback defaults
        title_name = provider_id.replace("-", " ").title()
        thumbnail = ""
        description = ""
        alt_names = []
        status = ""
        genres = []

        if html_content:
            t_match = re.search(r'<div\b[^>]*class=["\']media-body["\'][^>]*>.*?<h1\b[^>]*>(.*?)</h1>', html_content, re.IGNORECASE | re.DOTALL)
            if t_match:
                title_name = re.sub(r'<[^>]+>', '', t_match.group(1)).strip()

            img_match = re.search(r'<img\b[^>]*src=["\']([^"\']+)["\'][^>]*class=["\']media-object', html_content, re.IGNORECASE)
            if img_match:
                thumbnail = img_match.group(1)
                if not thumbnail.startswith("http"):
                    thumbnail = f"{self.base_url}{thumbnail}"

            alt_match = re.search(r'Alternate Titles:\s*(.*?)</span>', html_content, re.IGNORECASE | re.DOTALL)
            if alt_match:
                alt_names = [x.strip() for x in alt_match.group(1).split(",")]

            stat_match = re.search(r'Status:\s*(.*?)</span>', html_content, re.IGNORECASE | re.DOTALL)
            if stat_match:
                status = stat_match.group(1).strip()
                if status.lower() == "completed":
                    status = "FINISHED"
                elif status.lower() == "ongoing":
                    status = "RELEASING"

            tag_block = re.search(r'<ul\b[^>]*class=["\']tagscat["\'][^>]*>([\s\S]*?)</ul>', html_content, re.IGNORECASE | re.DOTALL)
            if tag_block:
                for a_match in re.finditer(r'<a\b[^>]*>(.*?)</a>', tag_block.group(1), re.IGNORECASE | re.DOTALL):
                    genres.append(a_match.group(1).strip())

            desc_match = re.search(r'<p\b[^>]*class=["\']ptext["\'][^>]*>(.*?)</p>', html_content, re.IGNORECASE | re.DOTALL)
            if desc_match:
                description = re.sub(r'<[^>]+>', '', desc_match.group(1)).strip()
                if description.lower().startswith("plot summary:"):
                    description = description[13:].strip()

        return normalize_title({
            "id": provider_id,
            "name": title_name,
            "altNames": alt_names,
            "thumbnail": thumbnail,
            "description": description,
            "status": status,
            "genres": genres,
        }, provider_id=self.id, provider_name=self.name, id_key="id")

    def episode_catalog(self, provider_id: str, ttype: str = "sub") -> dict[str, Any]:
        url = f"{self.base_url}/series/{provider_id}"
        html_content = self._fetch_html(url)

        episodes = []
        pattern = r'<li\b[^>]*>([\s\S]*?)</li>'
        for match in re.finditer(pattern, html_content, re.IGNORECASE):
            block = match.group(1)
            if 'anm_det_pop' not in block:
                continue

            link_match = re.search(r'<a\b[^>]*href=["\']([^"\']+)["\']', block, re.IGNORECASE)
            if not link_match:
                continue

            href = link_match.group(1).lstrip('/')
            href = href.split('#')[0]

            strong_match = re.search(r'<strong[^>]*>([\s\S]*?)</strong>', block, re.IGNORECASE)
            if not strong_match:
                continue
            strong_text = re.sub(r'<[^>]+>', '', strong_match.group(1))

            num_match = re.search(r'(\d+)\s*$', strong_text)
            if not num_match:
                continue

            number = int(num_match.group(1))

            episodes.append({
                "number": number,
                "id": href
            })

        episodes.sort(key=lambda x: x["number"])

        ep_ids = [ep["id"] for ep in episodes]
        labels = {ep["id"]: str(ep["number"]) for ep in episodes}

        return normalize_episode_catalog({
            "state": "loaded" if ep_ids else "empty",
            "ids": ep_ids,
            "labels": labels,
            "episodes": {
                "sub": [{"id": ep["id"], "label": str(ep["number"])} for ep in episodes],
                "dub": [],
                "raw": [],
            },
        }, provider_id=self.id, provider_title_id=provider_id)

    def episode_sources(
        self,
        provider_id: str,
        episode: str,
        ttype: str = "sub",
    ) -> dict[str, Any] | None:
        url = f"{self.base_url}/{episode}"
        html_content = self._fetch_html(url)

        tabs = []
        for match in re.finditer(r'<a\b[^>]*data-toggle=["\']tab["\'][^>]*>', html_content, re.IGNORECASE):
            tag = match.group(0)

            id_match = re.search(r'data-id=[\'"]([^\'"]+)[\'"]', tag)
            if not id_match: continue
            embed_id = html.unescape(id_match.group(1))

            mirror_match = re.search(r'data-mirror=[\'"]([^\'"]+)[\'"]', tag)
            server = html.unescape(mirror_match.group(1)) if mirror_match else "AnimeGG"

            version_match = re.search(r'data-version=[\'"]([^\'"]+)[\'"]', tag)
            version = html.unescape(version_match.group(1)) if version_match else "subbed"

            tabs.append({
                "embedId": embed_id,
                "server": server,
                "version": version
            })

        all_sources = []
        for tab in tabs:
            if tab["server"].lower() == "animegg" and "sub" in tab["version"].lower():
                embed_url = f"{self.base_url}/embed/{tab['embedId']}"
                embed_html = self._fetch_html(embed_url)

                vid_match = re.search(r'var\s+videoSources\s*=\s*(\[[\s\S]*?\]);', embed_html)
                if not vid_match:
                    continue

                json_str = vid_match.group(1)
                json_str = re.sub(r'([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', json_str)
                json_str = re.sub(r':\s*\'([^\']*)\'', r': "\1"', json_str)

                try:
                    parsed = json.loads(json_str)
                except Exception as e:
                    log.debug(f"AnimeGG JSON Parse Error: {e}")
                    continue

                for s in parsed:
                    s_url = s.get("file", "")
                    if s_url and not s_url.startswith("http"):
                        s_url = f"{self.base_url}{s_url}"
                    if s_url:
                        quality_label = s.get("label", "unknown")
                        q_match = re.search(r'(\d+)', quality_label)
                        q_num = q_match.group(1) if q_match else quality_label

                        all_sources.append({
                            "sourceName": f"AnimeGG ({q_num}p)",
                            "link": s_url,
                            "resolution": q_num,
                            "type": "mp4",
                            "priority": 0,
                            "headers": {"Referer": self.base_url}
                        })

        try:
            all_sources.sort(key=lambda x: int(x["resolution"]), reverse=True)
        except:
            pass

        return normalize_episode_sources({
            "episode": {
                "sourceUrls": all_sources
            }
        }, provider_id=self.id, provider_title_id=provider_id, episode=episode)

    def browser_url(
        self,
        provider_id: str,
        episode: str | None = None,
        ttype: str = "sub",
        cfg: dict[str, Any] | None = None,
    ) -> str:
        if episode:
            return f"{self.base_url}/{episode}"
        return f"{self.base_url}/series/{provider_id}"

PROVIDER_CLASS = AnimeGG
