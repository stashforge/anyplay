"""Streaming provider registry."""

from __future__ import annotations

import importlib
import json
import os
import pkgutil
from collections.abc import Iterable
from typing import Any, Dict

_PROVIDER_ALIASES = {
    "anidbapp": "anidb",
    "allanime": "mkissa",
}

class _RegistryDict(dict):
    def __getitem__(self, key):
        k = str(key).casefold() if isinstance(key, str) else key
        if k not in self and k in _PROVIDER_ALIASES:
            k = _PROVIDER_ALIASES[k]
        return super().__getitem__(k)

    def get(self, key, default=None):
        k = str(key).casefold() if isinstance(key, str) else key
        if k not in self and k in _PROVIDER_ALIASES:
            k = _PROVIDER_ALIASES[k]
        return super().get(k, default)

# Load the JSON registry
_REGISTRY_PATH = os.path.join(os.path.dirname(__file__), "registry.json")
try:
    with open(_REGISTRY_PATH, encoding="utf-8") as _f:
        PROVIDER_REGISTRY = _RegistryDict(json.load(_f).get("providers", {}))
except Exception:
    PROVIDER_REGISTRY = _RegistryDict()


_SKIPPED_MODULES = {"shared"}
_DISABLED_PROVIDERS = set()
_DEFAULT_PROVIDER_ID = "miruro"

from .shared.models import (
    title_provider_id,
    title_provider_key,
)


def _provider_classes_from_module(module) -> list[type]:
    classes = []
    provider_class = getattr(module, "PROVIDER_CLASS", None)
    if provider_class is not None:
        if isinstance(provider_class, (list, tuple)):
            classes.extend(provider_class)
        else:
            classes.append(provider_class)
    provider_classes = getattr(module, "PROVIDER_CLASSES", None)
    if provider_classes:
        classes.extend(provider_classes)
    return [
        cls for cls in classes
        if getattr(cls, "id", None) and callable(cls)
    ]


def discover_provider_factories(
    package_path: Iterable[str] | None = None,
    package_name: str | None = None,
) -> dict[str, type]:
    """Discover provider classes from provider modules.

    A built-in provider module only needs to expose ``PROVIDER_CLASS`` or
    ``PROVIDER_CLASSES``.  The registry handles the rest.
    """
    package_path = __path__ if package_path is None else package_path
    package_name = __name__ if package_name is None else package_name

    factories: dict[str, type] = {}
    for module_info in pkgutil.iter_modules(package_path):
        name = module_info.name
        if name.startswith("_") or name in _SKIPPED_MODULES:
            continue
        try:
            module = importlib.import_module(f"{package_name}.{name}")
        except Exception as exc:
            import logging
            logging.getLogger(__name__).debug("Failed to load provider module %s: %s", name, exc)
            continue
        for provider_class in _provider_classes_from_module(module):
            provider_id = str(provider_class.id).casefold()
            if provider_id in _DISABLED_PROVIDERS:
                continue
            factories[provider_id] = provider_class
            for alias in getattr(provider_class, "aliases", []):
                alias_id = str(alias).casefold()
                if alias_id not in _DISABLED_PROVIDERS:
                    factories[alias_id] = provider_class
    return factories


PROVIDER_FACTORIES = discover_provider_factories()
if _DEFAULT_PROVIDER_ID not in PROVIDER_FACTORIES:
    try:
        from .miruro import MiruroProvider
        PROVIDER_FACTORIES[_DEFAULT_PROVIDER_ID] = MiruroProvider
    except Exception:
        pass

# Order PROVIDERS according to registry.json ordering
PROVIDERS: dict[str, Any] = {}
for p_id in PROVIDER_REGISTRY:
    if p_id in PROVIDER_FACTORIES:
        try:
            PROVIDERS[p_id] = PROVIDER_FACTORIES[p_id]()
        except Exception:
            pass
for p_id, factory in PROVIDER_FACTORIES.items():
    if p_id not in PROVIDERS and p_id not in _DISABLED_PROVIDERS:
        try:
            PROVIDERS[p_id] = factory()
        except Exception:
            pass

# Attach metadata directly to instances for backward compatibility,
# and so providers can self-reference their JSON domains.
for p_id, p_inst in PROVIDERS.items():
    meta = PROVIDER_REGISTRY.get(p_id, {})
    p_inst.metadata = meta
    p_inst.domains = meta.get("domains", [])

ALLANIME = PROVIDERS.get("mkissa") or PROVIDERS[_DEFAULT_PROVIDER_ID]


def _split_provider_list(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raw_items = str(value).replace(";", ",").split(",")
    out = set()
    for item in raw_items:
        key = str(item or "").strip().casefold()
        if not key:
            continue
        out.add(_PROVIDER_ALIASES.get(key, key))
    return out


def disabled_providers_from_config(config: dict[str, Any] | None = None) -> set[str]:
    if config is None:
        try:
            from ..state import paths
            from ..state.config import load_config_file
            config = load_config_file(paths.CONFIG_PATH, disabled=True)
        except Exception:
            config = {}
    return _split_provider_list((config or {}).get("disabled_providers", ""))


def available_providers():
    return {k: v for k, v in PROVIDERS.items() if k in PROVIDER_REGISTRY and is_provider_active(k)}

def get_provider_registry() -> dict[str, Any]:
    return PROVIDER_REGISTRY


def is_provider_active(provider_id: str) -> bool:
    if not provider_id:
        return False
    key = str(provider_id).casefold()
    if key not in PROVIDERS and key in _PROVIDER_ALIASES:
        key = _PROVIDER_ALIASES[key]
    if key in _DISABLED_PROVIDERS or key not in PROVIDERS:
        return False
    if key in disabled_providers_from_config():
        return False
    status = PROVIDER_REGISTRY.get(key, {}).get("status", "active")
    if status in ("broken", "disabled", "deprecated"):
        return False
    return True


def provider_key(provider_id=_DEFAULT_PROVIDER_ID):
    key = str(provider_id or "").casefold()
    if is_provider_active(key):
        return key
    alias = _PROVIDER_ALIASES.get(key)
    if alias and is_provider_active(alias):
        return alias
    for k, v in _PROVIDER_ALIASES.items():
        if key == v and is_provider_active(k):
            return k
    if is_provider_active(_DEFAULT_PROVIDER_ID):
        return _DEFAULT_PROVIDER_ID
    active = available_providers()
    return next(iter(active), _DEFAULT_PROVIDER_ID)


def get_provider(provider_id=_DEFAULT_PROVIDER_ID, request_json_fn=None):
    key = provider_key(provider_id)
    if key not in PROVIDERS and key in _PROVIDER_ALIASES:
        key = _PROVIDER_ALIASES[key]
    if request_json_fn is None:
        return PROVIDERS[key]

    inst = PROVIDER_FACTORIES[key](request_json_fn)
    meta = PROVIDER_REGISTRY.get(key, {})
    inst.metadata = meta
    inst.domains = meta.get("domains", [])
    return inst


def provider_display_name(provider_id=_DEFAULT_PROVIDER_ID) -> str:
    key = provider_key(provider_id)
    reg = PROVIDER_REGISTRY.get(key, {})
    name = reg.get("name") or reg.get("display_name")
    if name:
        return str(name)
    prov = PROVIDERS.get(key)
    if prov and hasattr(prov, "name") and prov.name:
        return str(prov.name)
    return str(key).title()


def provider_translation_capability(
    provider_id: str,
    show: dict[str, Any] | None = None,
    current_ttype: str = "sub",
    target_ttype: str = "dub",
) -> tuple[bool, str | None]:
    prov = get_provider(provider_id)
    if hasattr(prov, "translation_switch_capability") and callable(prov.translation_switch_capability):
        return prov.translation_switch_capability(show, current_ttype, target_ttype)

    audio_mode = getattr(prov, "audio_mode", "separate_catalogs")
    p_name = getattr(prov, "name", str(provider_id).title())
    if audio_mode == "embedded_multi_audio":
        return False, "Multi-audio / dubs embedded in stream. Switch audio inside player."
    if audio_mode == "sub_only":
        return False, f"Releases are sub-only on {p_name}."
    return True, None
