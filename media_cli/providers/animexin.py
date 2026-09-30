"""AnimeXin provider."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

from ..services.http import UA
from .shared.models import normalize_episode_catalog, normalize_episode_sources, normalize_titles
from .shared.schema import build_title
from .shared.wordpress import WordPressAnimeProvider, fetch_html


class AnimeXinProvider(WordPressAnimeProvider):
    id = "animexin"
    audio_mode = "sub_only"
    blocked_mirror_label_pattern = r"\b(?:indo|indonesia|indonesian)\b"

    @property
    def base_url(self) -> str:
        return self.domains[0] if getattr(self, 'domains', None) else "https://animexin.dev"

    @property
    def name(self) -> str:
        return getattr(self, "metadata", {}).get("name", "AnimeXin")

    def __init__(self, request_json_fn=None, fetch=None, ajax_fetch=None):
        del request_json_fn
        super().__init__(fetch=fetch or fetch_html)
        self._ajax_fetch = ajax_fetch or self._fetch_ajax

    def search(self, query: str, ttype: str = "sub") -> list[dict]:
        del ttype
        return normalize_titles(
            self._search_ajax(query) or super().search(query, "sub"),
            provider_id=self.id,
            provider_name=self.name,
        )



    def episode_catalog(self, provider_id: str, ttype: str = "sub") -> dict:
        return normalize_episode_catalog(
            super().episode_catalog(provider_id, ttype),
            provider_id=self.id,
            provider_title_id=provider_id,
        )

    def episode_sources(self, provider_id: str, episode: str, ttype: str = "sub") -> dict | None:
        return normalize_episode_sources(
            super().episode_sources(provider_id, episode, ttype),
            provider_id=self.id,
            provider_title_id=provider_id,
            episode=episode,
        )

    def _fetch_ajax(self, query: str) -> dict:
        data = urllib.parse.urlencode({
            "action": "ts_ac_do_search",
            "ts_ac_query": query,
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/wp-admin/admin-ajax.php",
            data=data,
            headers={
                "User-Agent": UA,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return json.loads(response.read().decode(charset, errors="replace"))

    def _search_ajax(self, query: str) -> list[dict]:
        if not query or not query.strip():
            return []
        try:
            payload = self._ajax_fetch(query)
        except Exception:
            return []
        rows = []
        for group in payload.get("anime") or []:
            for item in group.get("all") or []:
                title = str(item.get("post_title") or "").strip()
                link = str(item.get("post_link") or "").strip()
                if not title or not link:
                    continue
                rows.append(self._title_from_ajax(item, title, link))
        return rows

    def _title_from_ajax(self, item: dict, title: str, link: str) -> dict:
        sub_type = str(item.get("post_sub") or "sub").strip().casefold()
        latest = str(item.get("post_latest") or "").strip()
        available = _latest_episode_count(latest)
        import re
        is_ended = bool(re.search(r"\b\[?END\]?\b", latest, re.I))
        status = "FINISHED" if is_ended else "RELEASING"
        ttype = "dub" if sub_type == "dub" else "sub"
        return build_title(
            provider=self.id,
            provider_name=self.name,
            provider_id=link,
            name=title,
            thumbnail=(item.get("post_image") or "").split("?resize=")[0],
            media_type=str(item.get("post_type") or "ONA").strip() or "ONA",
            media_format=str(item.get("post_type") or "ONA").strip() or "ONA",
            status=status,
            episode_count=available if is_ended else None,
            available_sub=available if ttype == "sub" else 0,
            available_dub=available if ttype == "dub" else 0,
            genres=item.get("post_genres") or "",
            extra={
                "_provider_latest": latest,
                "_provider_genres": str(item.get("post_genres") or "").strip(),
                "_provider_wp_id": str(item.get("ID") or ""),
                "_provider_sub_type": sub_type.upper(),
            },
        )


def _latest_episode_count(value: str) -> int:
    clean_val = re.sub(r"\[.*?\]|\(.*?\)", "", str(value or ""))
    clean_val = re.sub(r"(?i)\b(4k|1080p?|720p?|480p?|2k|8k)\b", "", clean_val)
    matches = re.findall(r"\d+", clean_val)
    return int(matches[-1]) if matches else 0


PROVIDER_CLASS = AnimeXinProvider
