"""AnimeKhor provider."""

from __future__ import annotations

from .animexin import AnimeXinProvider
from .shared.models import normalize_titles
from .shared.wordpress import WordPressAnimeProvider


class AnimeKhorProvider(AnimeXinProvider):
    id = "animekhor"

    @property
    def base_url(self) -> str:
        domains = getattr(self, "domains", None) or []
        for domain in domains:
            if "animekhor.org" in domain:
                return domain
        return domains[0] if domains else "https://animekhor.org"

    @property
    def name(self) -> str:
        return getattr(self, "metadata", {}).get("name", "AnimeKhor")

    def search(self, query: str, ttype: str = "sub") -> list[dict]:
        return normalize_titles(
            WordPressAnimeProvider.search(self, query, ttype),
            provider_id=self.id,
            provider_name=self.name,
        )


PROVIDER_CLASS = AnimeKhorProvider
