"""AnimeKhor provider."""

from __future__ import annotations

from .animexin import AnimeXinProvider


class AnimeKhorProvider(AnimeXinProvider):
    id = "animekhor"

    @property
    def base_url(self) -> str:
        return self.domains[0] if getattr(self, 'domains', None) else "https://animekhor.org"

    @property
    def name(self) -> str:
        return getattr(self, "metadata", {}).get("name", "AnimeKhor")


PROVIDER_CLASS = AnimeKhorProvider
