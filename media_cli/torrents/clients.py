"""Small HTTP clients for torrent search APIs."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TSUKIHIME_SEARCH_URL = "https://api.tsukihime.org/v1/search/torrents"
TSUKIHIME_TORRENT_URL = "https://api.tsukihime.org/v1/torrents/{id}"
TORRENTS_CSV_SEARCH_URL = "https://torrents-csv.com/service/search"
NYAA_RSS_URL = "https://nyaa.si/"


def fetch_json(url: str, *, timeout: float = 20.0) -> Any:
    req = Request(url, headers={"User-Agent": "AnyPlay torrent backend"})
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def fetch_tsukihime_search(query: str, limit: int, offset: int) -> list[dict[str, Any]]:
    params = urlencode({"q": query, "limit": limit, "offset": offset})
    data = fetch_json(f"{TSUKIHIME_SEARCH_URL}?{params}")
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        return data["results"]
    return []


def fetch_tsukihime_detail(tsukihime_id: int) -> dict[str, Any]:
    data = fetch_json(TSUKIHIME_TORRENT_URL.format(id=int(tsukihime_id)))
    return data if isinstance(data, dict) else {}


def fetch_torrents_csv(query: str, limit: int, after: int) -> list[dict[str, Any]]:
    params = urlencode({"q": query, "size": limit, "after": after})
    data = fetch_json(f"{TORRENTS_CSV_SEARCH_URL}?{params}")
    if isinstance(data, dict) and isinstance(data.get("torrents"), list):
        return data["torrents"]
    return []


def fetch_nyaa_rss(query: str, limit: int, offset: int) -> str:
    params = urlencode({"page": "rss", "q": query, "c": "0_0", "f": "0"})
    req = Request(f"{NYAA_RSS_URL}?{params}", headers={"User-Agent": "AnyPlay torrent backend"})
    with urlopen(req, timeout=20.0) as response:
        return response.read().decode("utf-8", errors="replace")
