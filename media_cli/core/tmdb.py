"""TMDB API client for searching and retrieving movie metadata."""

import json
import logging
import urllib.parse
import urllib.request
from typing import Any

from media_cli.core.storage import load_config

logger = logging.getLogger(__name__)

class TMDBError(Exception):
    pass


class TMDBClient:
    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(self, token: str | None = None):
        if not token:
            from media_cli.state import secrets as secret_state
            token = secret_state.get_secret(secret_state.TMDB_KEY)

        if not token:
            cfg = load_config()
            token = cfg.get("tmdb_token", "").strip()

        if not token:
            raise TMDBError("TMDB token not found in config. Please set 'tmdb_token'.")

        self.token = token
        self.headers = {
            "accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
        }

        # v4 tokens are long JWT strings
        if len(self.token) > 100 or "eyJ" in self.token:
            self.headers["Authorization"] = f"Bearer {self.token}"
            self.api_key_query = ""
        else:
            self.api_key_query = f"&api_key={self.token}"

    def _fetch(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        """Make a GET request to the TMDB API."""
        query = urllib.parse.urlencode(params or {})
        if query:
            url = f"{self.BASE_URL}{path}?{query}{self.api_key_query}"
        else:
            url = f"{self.BASE_URL}{path}?{self.api_key_query.lstrip('&')}"

        last_error = None
        import time
        for attempt in range(3):
            req = urllib.request.Request(url, headers=self.headers)
            try:
                with urllib.request.urlopen(req, timeout=10) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as e:
                last_error = e
                time.sleep(0.5)

        raise TMDBError(f"Failed to fetch data from TMDB after 3 attempts: {last_error}")

    def search_multi(self, query: str) -> list[dict[str, Any]]:
        """Search for both movies and TV shows."""
        data = self._fetch("/search/multi", {"query": query, "language": "en-US", "page": "1"})
        return data.get("results", [])

    def get_tv_details(self, tv_id: str) -> dict[str, Any]:
        """Get full details for a TV show (including seasons count)."""
        return self._fetch(f"/tv/{tv_id}", {"language": "en-US"})

    def get_tv_season(self, tv_id: str, season_number: int) -> dict[str, Any]:
        """Get full details for a specific TV season (including episodes)."""
        return self._fetch(f"/tv/{tv_id}/season/{season_number}", {"language": "en-US"})

    def get_movie_details(self, tmdb_id: str | int) -> dict[str, Any]:
        """Get full metadata for a specific movie ID."""
        return self._fetch(f"/movie/{tmdb_id}", {"language": "en-US"})
