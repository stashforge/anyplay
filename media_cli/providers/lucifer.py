"""LuciferDonghua provider."""

from __future__ import annotations

from .animexin import AnimeXinProvider


class LuciferDonghuaProvider(AnimeXinProvider):
    id = "lucifer"
    resolve_mirror_pages = True

    @property
    def base_url(self) -> str:
        return self.domains[0] if getattr(self, 'domains', None) else "https://luciferdonghua.in"

    @property
    def name(self) -> str:
        return getattr(self, 'metadata', {}).get("name", "LuciferDonghua")


PROVIDER_CLASS = LuciferDonghuaProvider
