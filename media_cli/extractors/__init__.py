"""Select native extractors from lightweight host metadata before importing them."""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from threading import RLock
from typing import TYPE_CHECKING, Any

from .routing import ROUTES, ExtractorRoute

if TYPE_CHECKING:
    from .base import BaseExtractor

_INSTANCES: dict[str, BaseExtractor] = {}
_LOAD_LOCK = RLock()


def _get_extractor(route: ExtractorRoute) -> BaseExtractor:
    with _LOAD_LOCK:
        if route.module not in _INSTANCES:
            if route.module in {"filemoon", "streamwish", "mp4upload", "voe", "kwik"}:
                from ..services.requirements import require_python

                require_python("bs4", f"{route.class_name.removesuffix('Extractor')} extraction")
            module = importlib.import_module(f"{__name__}.{route.module}")
            _INSTANCES[route.module] = getattr(module, route.class_name)()
        return _INSTANCES[route.module]


class _Extractors(Sequence):
    def __len__(self) -> int:
        return len(ROUTES)

    def __getitem__(self, index: int | slice) -> Any:
        routes = tuple(ROUTES.values())
        if isinstance(index, slice):
            return [_get_extractor(route) for route in routes[index]]
        return _get_extractor(routes[index])


EXTRACTORS = _Extractors()


def find_extractor(url: str) -> BaseExtractor | None:
    for route in ROUTES.values():
        if route.matches(url):
            return _get_extractor(route)
    return None


_EXPORTS = {route.class_name: (route.module, route.class_name) for route in ROUTES.values()}
_EXPORTS.update(BaseExtractor=("base", "BaseExtractor"), JsUnpacker=("unpack", "JsUnpacker"))


def __getattr__(name: str):
    if name in _EXPORTS:
        module_name, attribute = _EXPORTS[name]
        return getattr(importlib.import_module(f"{__name__}.{module_name}"), attribute)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["EXTRACTORS", "find_extractor", *_EXPORTS]
