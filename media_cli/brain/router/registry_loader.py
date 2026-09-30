import json
import os
from dataclasses import dataclass


@dataclass
class ProviderCapabilities:
    name: str
    domains: list[str]
    type: str # "anime" | "donghua" | "movie"
    languages: list[str] # ["sub", "dub"]
    status: str # "active" | "broken"
    features: list[str] # ["anilist_sync", "search", "fallback_servers"]

class RegistryLoader:
    """Loads and validates the capabilities of all providers."""

    @classmethod
    def load(cls, registry_path: str) -> dict[str, ProviderCapabilities]:
        if not os.path.exists(registry_path):
            raise FileNotFoundError(f"Registry not found at {registry_path}")

        with open(registry_path, encoding="utf-8") as f:
            data = json.load(f)

        providers = {}
        for pid, pdata in data.get("providers", {}).items():
            providers[pid] = ProviderCapabilities(
                name=pdata.get("name", pid),
                domains=pdata.get("domains", []),
                type=pdata.get("type", "anime"),
                languages=pdata.get("languages", ["sub"]),
                status=pdata.get("status", "broken"),
                features=pdata.get("features", [])
            )

        return providers
