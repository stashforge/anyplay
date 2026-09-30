"""AnimePahe provider adapter."""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import sqlite3
import urllib.parse
from typing import Any

from bs4 import BeautifulSoup

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


def _find_browser_cookies() -> dict[str, str]:
    """Search for existing animepahe or kwik cookies from local Firefox profiles."""
    cookies: dict[str, str] = {}
    for p in glob.glob(os.path.expanduser("~/.mozilla/firefox/*/cookies.sqlite")):
        conn = None
        try:
            conn = sqlite3.connect(f"file:{p}?immutable=1", uri=True)
            cur = conn.cursor()
            cur.execute(
                "SELECT name, value FROM moz_cookies "
                "WHERE host LIKE '%animepahe%' OR host LIKE '%kwik%'"
            )
            for name, val in cur.fetchall():
                cookies[name] = val
        except Exception:
            pass
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
    return cookies


def _detect_firefox_user_agent() -> str:
    import subprocess
    try:
        out = subprocess.check_output(["firefox", "--version"], stderr=subprocess.DEVNULL).decode().strip()
        m = re.search(r"(\d+)", out)
        if m:
            v = m.group(1)
            return f"Mozilla/5.0 (X11; Linux x86_64; rv:{v}.0) Gecko/20100101 Firefox/{v}.0"
    except Exception:
        pass
    return "Mozilla/5.0 (X11; Linux x86_64; rv:155.0) Gecko/20100101 Firefox/155.0"


def _pahe_src_sort_key(s: dict, ttype: str = "sub") -> tuple:
    from ..media.sources import parse_resolution_height
    res_str = s.get("resolution") or s.get("sourceName", "")
    h = parse_resolution_height(res_str)
    sname = (s.get("sourceName") or "").lower()
    is_dub = " eng" in sname or "dub" in sname
    audio_penalty = 1 if (ttype == "sub" and is_dub) or (ttype == "dub" and not is_dub) else 0
    return (audio_penalty, -h)


class AnimePaheProvider(Provider):
    """AnimePahe provider using internal API and Kwik extractors."""

    id = "animepahe"
    audio_mode = "sub_only"

    def __init__(self, request_json_fn=None):
        self._request_json = request_json_fn
        if not hasattr(self, "metadata"):
            self.metadata = {}
        if not hasattr(self, "domains") or not self.domains:
            self.domains = [
                "https://animepahe.pw",
                "https://animepahe.org",
                "https://animepahe.com",
                "https://animepahe.ru",
            ]
        self._session = None
        self._cookies: dict[str, str] = {}
        self._user_agent = os.environ.get("ANIMEPAHE_USER_AGENT", "").strip()
        self._init_cookies()

    @property
    def base_url(self) -> str:
        return self.domains[0] if getattr(self, "domains", None) else "https://animepahe.pw"

    @property
    def name(self) -> str:
        return self.metadata.get("name", "AnimePahe")

    def _init_cookies(self):
        # 1. Environment variable
        env_cookie = os.environ.get("ANIMEPAHE_COOKIE", "").strip()
        if env_cookie:
            for part in env_cookie.split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    self._cookies[k] = v

        # 2. Browser cookies
        if "cf_clearance" not in self._cookies:
            browser_cookies = _find_browser_cookies()
            self._cookies.update(browser_cookies)

        if not self._user_agent:
            self._user_agent = _detect_firefox_user_agent()

    def _get_session(self):
        if self._session is None and CURL_CFFI_AVAILABLE and cffi_requests is not None:
            if "Firefox" in self._user_agent:
                self._session = cffi_requests.Session(impersonate="firefox147")
            else:
                self._session = cffi_requests.Session(impersonate="chrome")
            if self._cookies:
                self._session.cookies.update(self._cookies)
        return self._session

    def _fetch_page(self, url: str, headers: dict[str, str] | None = None, timeout: int = 15) -> str:
        req_headers = {
            "User-Agent": self._user_agent or UA,
            "Referer": f"{self.base_url}/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        }
        if headers:
            req_headers.update(headers)

        sess = self._get_session()
        if sess is not None:
            try:
                resp = sess.get(url, headers=req_headers, timeout=timeout)
                if resp.status_code == 200:
                    return resp.text
                if resp.status_code == 403 and "Just a moment..." in resp.text:
                    self._cf_blocked = True
                    _logger.debug("AnimePahe returned Cloudflare challenge on %s", url)
            except Exception as e:
                _logger.debug("AnimePahe session GET error on %s: %s", url, e)

        # Fallback to urllib
        import urllib.request
        cookie_header = "; ".join(f"{k}={v}" for k, v in self._cookies.items())
        if cookie_header:
            req_headers["Cookie"] = cookie_header
        req = urllib.request.Request(url, headers=req_headers)
        try:
            with urllib.request.urlopen(req, context=SSL_CTX, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="ignore")
        except Exception as e:
            _logger.debug("AnimePahe urllib fetch failed on %s: %s", url, e)
            return ""

    def _fetch_json(self, url: str, timeout: int = 15) -> dict | None:
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.base_url}/",
        }
        raw = self._fetch_page(url, headers=headers, timeout=timeout)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    def search(self, query: str, ttype: str = "sub") -> list[dict[str, Any]]:
        self._cf_blocked = False
        if not query or not query.strip():
            return []

        search_url = f"{self.base_url}/api?m=search&q={urllib.parse.quote(query.strip())}"
        data = self._fetch_json(search_url)

        # If primary domain failed and not CF blocked, try fallback domains
        if data is None and not self._cf_blocked and len(self.domains) > 1:
            for alt_domain in self.domains[1:]:
                alt_url = f"{alt_domain}/api?m=search&q={urllib.parse.quote(query.strip())}"
                data = self._fetch_json(alt_url)
                if data is not None:
                    break

        if not data or not isinstance(data, dict):
            if getattr(self, "_cf_blocked", False):
                from ..core.api import SearchFailure
                raise SearchFailure(
                    "AnimePahe is protected by Cloudflare. Open https://animepahe.pw in Firefox once, or set ANIMEPAHE_COOKIE."
                )
            return []

        results = []
        for item in data.get("data") or []:
            session = item.get("session") or str(item.get("id"))
            title = item.get("title") or "Unknown"
            poster = item.get("poster") or ""
            ep_count = item.get("episodes") or 0
            status_raw = item.get("status", "")
            status = "FINISHED" if "Finished" in status_raw else "RELEASING"
            start_year = item.get("year")

            results.append({
                "_id": session,
                "name": title,
                "thumbnail": poster,
                "episodeCount": ep_count,
                "availableEpisodes": {"sub": ep_count, "dub": 0, "raw": 0},
                "status": status,
                "type": str(item.get("type") or "TV").upper(),
                "airedStart": {"year": start_year} if start_year else None,
            })

        return normalize_titles(results, provider_id=self.id, provider_name=self.name, id_key="_id")

    def get_title(self, provider_id: str) -> dict[str, Any] | None:
        url = f"{self.base_url}/anime/{provider_id}"
        html = self._fetch_page(url)
        if not html:
            return None
        soup = BeautifulSoup(html, "html.parser")
        title_el = soup.select_one("div.title-wrapper h1 span")
        title = title_el.text.strip() if title_el else provider_id
        poster_el = soup.select_one("div.anime-poster img")
        poster = poster_el.get("src") or poster_el.get("data-src") or "" if poster_el else ""

        return {
            "_id": provider_id,
            "name": title,
            "thumbnail": poster,
        }

    def episode_catalog(self, provider_id: str, ttype: str = "sub") -> dict[str, Any]:
        session = provider_id
        page = 1
        episodes_list = []
        labels = {}

        while True:
            api_url = f"{self.base_url}/api?m=release&id={session}&sort=episode_asc&page={page}"
            data = self._fetch_json(api_url)
            if not data or not isinstance(data, dict):
                break

            items = data.get("data") or []
            if not items:
                break

            for it in items:
                ep_session = it.get("session")
                ep_num = it.get("episode")
                if ep_session is None:
                    continue

                ep_id = f"{session}/{ep_session}"
                label_str = str(int(ep_num)) if isinstance(ep_num, (int, float)) and int(ep_num) == ep_num else str(ep_num)
                episodes_list.append(ep_id)
                labels[ep_id] = label_str

            last_page = data.get("last_page") or 1
            current_page = data.get("current_page") or page
            if current_page >= last_page:
                break
            page += 1

        return normalize_episode_catalog({
            "state": "loaded" if episodes_list else "empty",
            "ids": episodes_list,
            "labels": labels,
            "episodes": {
                "sub": [{"id": eid, "label": labels[eid]} for eid in episodes_list],
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
        play_path = episode if "/" in episode else f"{provider_id}/{episode}"
        url = f"{self.base_url}/play/{play_path}"
        html = self._fetch_page(url)
        if not html:
            return None

        soup = BeautifulSoup(html, "html.parser")
        source_urls = []

        # Find buttons in #resolutionMenu
        resolution_menu = soup.select_one("div#resolutionMenu")
        if resolution_menu:
            for btn in resolution_menu.find_all("button"):
                kwik_link = btn.get("data-src")
                if not kwik_link:
                    continue
                quality = btn.text.strip() or btn.get("data-resolution") or "720p"
                fansub = btn.get("data-fansub", "")
                label = f"{quality} ({fansub})" if fansub else quality

                source_urls.append({
                    "sourceName": f"Kwik ({label})",
                    "sourceUrl": kwik_link,
                    "type": "embed",
                    "resolution": quality,
                    "referer": f"{self.base_url}/",
                    "priority": 1,
                    "headers": {
                        "Referer": f"{self.base_url}/",
                        "User-Agent": self._user_agent or UA,
                    },
                })

        # Also check #pickDownload as fallback
        if not source_urls:
            download_div = soup.select_one("div#pickDownload")
            if download_div:
                for a in download_div.find_all("a", href=True):
                    link = a["href"]
                    text = a.text.strip()
                    source_urls.append({
                        "sourceName": f"AnimePahe ({text})",
                        "sourceUrl": link,
                        "type": "embed",
                        "resolution": "Adaptive",
                        "referer": f"{self.base_url}/",
                        "priority": 2,
                    })


        source_urls.sort(key=lambda s: _pahe_src_sort_key(s, ttype=ttype))

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
        play_path = episode if "/" in episode else f"{provider_id}/{episode}"
        return f"{self.base_url}/play/{play_path}"


PROVIDER_CLASS = AnimePaheProvider
