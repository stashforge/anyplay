"""Streaming provider registry."""

from __future__ import annotations

import importlib
import pkgutil
from collections import OrderedDict
from collections.abc import Iterable, Iterator, Mapping
from threading import Lock, RLock
from typing import Any

from ..provider_manifest import (
    _PROVIDER_ALIASES,
    PROVIDER_REGISTRY,
    disabled_providers_from_config,
)
from ..provider_manifest import _RegistryDict as _RegistryDict
from .shared.models import title_provider_id as title_provider_id
from .shared.models import title_provider_key as title_provider_key

_SKIPPED_MODULES = {"shared"}
_DISABLED_PROVIDERS: set[str] = set()
_DEFAULT_PROVIDER_ID = "miruro"
_SESSION_PROVIDERS: OrderedDict[tuple, Any] = OrderedDict()
_SESSION_PROVIDERS_LOCK = Lock()
_MAX_SESSION_PROVIDERS = 16

_PROVIDER_MODULES = {key: meta["module"] for key, meta in PROVIDER_REGISTRY.items() if meta.get("module")}
_LOAD_LOCK = RLock()


def _canonical_key(key: str) -> str:
    key = str(key).casefold()
    return _PROVIDER_ALIASES.get(key, key)


class _ProviderFactories(Mapping[str, type]):
    """Known factory keys are metadata; indexing imports just that factory."""

    def __init__(self):
        self._loaded: dict[str, type] = {}

    def __iter__(self) -> Iterator[str]:
        return iter((*_PROVIDER_MODULES, *_PROVIDER_ALIASES))

    def __len__(self) -> int:
        return len(_PROVIDER_MODULES) + len(_PROVIDER_ALIASES)

    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and _canonical_key(key) in _PROVIDER_MODULES

    def __getitem__(self, key: str) -> type:
        key = _canonical_key(key)
        if key not in _PROVIDER_MODULES:
            raise KeyError(key)
        with _LOAD_LOCK:
            if key not in self._loaded:
                from ..services.requirements import require_provider_operation

                require_provider_operation(key, "import", metadata=PROVIDER_REGISTRY.get(key, {}))
                module = importlib.import_module(f"{__name__}.{_PROVIDER_MODULES[key]}")
                factory = next((cls for cls in _provider_classes_from_module(module)
                                if str(getattr(cls, "id", "")).casefold() == key), None)
                if factory is None:
                    raise ImportError(f"Provider {key} has no matching factory")
                self._loaded[key] = factory
            return self._loaded[key]


class _ProviderInstances(Mapping[str, Any]):
    def __init__(self):
        self._loaded: dict[str, Any] = {}

    def __iter__(self) -> Iterator[str]:
        return iter(PROVIDER_FACTORIES)

    def __len__(self) -> int:
        return len(PROVIDER_FACTORIES)

    def __contains__(self, key: object) -> bool:
        return key in PROVIDER_FACTORIES

    def __getitem__(self, key: str):
        key = _canonical_key(key)
        with _LOAD_LOCK:
            if key not in self._loaded:
                instance = PROVIDER_FACTORIES[key]()
                instance.metadata = PROVIDER_REGISTRY.get(key, {})
                instance.domains = list(instance.metadata.get("domains") or ())
                self._loaded[key] = instance
            return self._loaded[key]


class _AvailableProviders(Mapping[str, Any]):
    """Snapshot active keys without materializing each provider value."""

    def __init__(self, keys: Iterable[str]):
        self._keys = dict.fromkeys(keys)

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    def __contains__(self, key: object) -> bool:
        return key in self._keys

    def __getitem__(self, key: str):
        if key not in self._keys:
            raise KeyError(key)
        return PROVIDERS[key]


def _provider_classes_from_module(module) -> list[type]:
    classes: list[type] = []
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
) -> Mapping[str, type]:
    """Discover provider classes from provider modules.

    Built-ins declare their module in registry.json and load on indexing. Custom
    package discovery remains an explicit, eager operation for compatibility.
    """
    if package_path is None and package_name is None:
        return PROVIDER_FACTORIES
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
            provider_id = str(getattr(provider_class, "id", "")).casefold()
            if provider_id in _DISABLED_PROVIDERS:
                continue
            factories[provider_id] = provider_class
            for alias in getattr(provider_class, "aliases", []):
                alias_id = str(alias).casefold()
                if alias_id not in _DISABLED_PROVIDERS:
                    factories[alias_id] = provider_class
    return factories


PROVIDER_FACTORIES = _ProviderFactories()
PROVIDERS = _ProviderInstances()


def __getattr__(name: str):
    if name == "ALLANIME":
        return PROVIDERS["mkissa"]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def available_providers():
    disabled = _DISABLED_PROVIDERS | disabled_providers_from_config()
    return _AvailableProviders(key for key, meta in PROVIDER_REGISTRY.items()
                               if key in PROVIDERS and key not in disabled
                               and meta.get("status", "active") not in {"broken", "disabled", "deprecated"})

def get_provider_registry() -> dict[str, Any]:
    return PROVIDER_REGISTRY


def is_provider_active(provider_id: str) -> bool:
    if not provider_id:
        return False
    key = _canonical_key(provider_id)
    if key in _DISABLED_PROVIDERS or key not in PROVIDERS:
        return False
    if key in disabled_providers_from_config():
        return False
    status = PROVIDER_REGISTRY.get(key, {}).get("status", "active")
    if status in ("broken", "disabled", "deprecated"):
        return False
    return True


def provider_key(provider_id=_DEFAULT_PROVIDER_ID):
    key = _canonical_key(provider_id or "")
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

    factory = PROVIDER_FACTORIES[key]
    meta = PROVIDER_REGISTRY.get(key, {})
    if getattr(factory, "reuse_session_instance", False):
        # The instance retains its callback, so its identity cannot be recycled
        # while cached. Other providers keep their existing instance lifecycle.
        session_key = (key, factory, id(request_json_fn), tuple(meta.get("domains") or ()))
        with _SESSION_PROVIDERS_LOCK:
            inst = _SESSION_PROVIDERS.get(session_key)
            if inst is None:
                inst = factory(request_json_fn)
                inst.metadata = meta
                inst.domains = list(meta.get("domains") or ())
                _SESSION_PROVIDERS[session_key] = inst
            _SESSION_PROVIDERS.move_to_end(session_key)
            while len(_SESSION_PROVIDERS) > _MAX_SESSION_PROVIDERS:
                _SESSION_PROVIDERS.popitem(last=False)
            return inst

    inst = factory(request_json_fn)
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
