"""Provider interface for title, episode, and source discovery."""

from __future__ import annotations

from typing import Any, Protocol


class Provider(Protocol):
    id: str
    name: str

    def search(self, query: str, ttype: str = "sub") -> list[dict[str, Any]]:
        """Return normalized title search results."""

    def get_title(self, provider_id: str) -> dict[str, Any] | None:
        """Return title metadata for a provider-specific title id."""

    def episode_catalog(self, provider_id: str, ttype: str = "sub") -> dict[str, Any]:
        """Return provider episode catalog state and episode ids."""

    def episode_sources(
        self,
        provider_id: str,
        episode: str,
        ttype: str = "sub",
    ) -> dict[str, Any] | None:
        """Return raw episode source payload."""

    def browser_url(
        self,
        provider_id: str,
        episode: str | None = None,
        ttype: str = "sub",
        cfg: dict[str, Any] | None = None,
    ) -> str:
        """Return a provider browser URL for web fallback."""

