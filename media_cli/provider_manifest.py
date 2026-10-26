"""Provider names and availability without importing provider implementations."""

import json
import os
import sys
from typing import Any

from .constants import APP_NAME, LEGACY_APP_NAMES

_PROVIDER_ALIASES = {"anidbapp": "anidb", "allanime": "mkissa"}


class _RegistryDict(dict):
    def __getitem__(self, key):
        key = key.casefold() if isinstance(key, str) else key
        if key not in self:
            key = _PROVIDER_ALIASES.get(key, key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        key = key.casefold() if isinstance(key, str) else key
        if key not in self:
            key = _PROVIDER_ALIASES.get(key, key)
        return super().get(key, default)


_REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "providers", "registry.json")
try:
    with open(_REGISTRY_PATH, encoding="utf-8") as handle:
        PROVIDER_REGISTRY = _RegistryDict(json.load(handle).get("providers", {}))
except (OSError, ValueError, AttributeError):
    PROVIDER_REGISTRY = _RegistryDict()


def _split_provider_list(value: Any) -> set[str]:
    if value is None:
        return set()
    raw_items = value if isinstance(value, (list, tuple, set)) else str(value).replace(";", ",").split(",")
    return {_PROVIDER_ALIASES.get(key, key) for item in raw_items if (key := str(item or "").strip().casefold())}


def _read_provider_config() -> dict:
    from .state.config import load_config_file

    # Do not import state.paths here: that module migrates directories on import.
    paths = sys.modules.get("media_cli.state.paths")
    if paths is not None:
        path = paths.CONFIG_PATH
    else:
        directory = os.path.expanduser(f"~/.config/{APP_NAME}")
        if not os.path.exists(directory):
            for legacy in LEGACY_APP_NAMES:
                candidate = os.path.expanduser(f"~/.config/{legacy}")
                if os.path.isdir(candidate):
                    directory = candidate
                    break
        path = os.path.join(directory, "config.conf")
    config = load_config_file(path, disabled=True)
    return config if isinstance(config, dict) else {}


def disabled_providers_from_config(config: dict[str, Any] | None = None) -> set[str]:
    if config is None:
        try:
            config = _read_provider_config()
        except Exception:
            config = {}
    return _split_provider_list((config or {}).get("disabled_providers", ""))


def available_provider_metadata(config: dict[str, Any] | None = None) -> dict[str, dict]:
    disabled = disabled_providers_from_config(config)
    return {key: meta for key, meta in PROVIDER_REGISTRY.items()
            if key not in disabled and not meta.get("source")
            and meta.get("status", "active") not in {"broken", "disabled", "deprecated"}}
