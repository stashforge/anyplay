"""Native video extractors based on Aniyomi / Anikku architecture."""

from __future__ import annotations

from .base import BaseExtractor
from .dailymotion import DailymotionExtractor
from .doodstream import DoodExtractor
from .filemoon import FilemoonExtractor
from .kwik import KwikExtractor
from .misterdonghua import MisterDonghuaExtractor
from .mixdrop import MixDropExtractor
from .mp4upload import Mp4UploadExtractor
from .okru import OkruExtractor
from .streamtape import StreamTapeExtractor
from .streamwish import StreamWishExtractor
from .unpack import JsUnpacker
from .voe import VoeExtractor

EXTRACTORS: list[BaseExtractor] = [
    StreamWishExtractor(),
    FilemoonExtractor(),
    StreamTapeExtractor(),
    DoodExtractor(),
    Mp4UploadExtractor(),
    VoeExtractor(),
    KwikExtractor(),
    DailymotionExtractor(),
    OkruExtractor(),
    MixDropExtractor(),
    MisterDonghuaExtractor(),
]


def find_extractor(url: str) -> BaseExtractor | None:
    """Find a registered extractor that can handle the given URL."""
    if not url:
        return None
    for extractor in EXTRACTORS:
        if extractor.can_handle(url):
            return extractor
    return None


__all__ = [
    "BaseExtractor",
    "DailymotionExtractor",
    "DoodExtractor",
    "EXTRACTORS",
    "FilemoonExtractor",
    "JsUnpacker",
    "KwikExtractor",
    "MixDropExtractor",
    "MisterDonghuaExtractor",
    "Mp4UploadExtractor",
    "OkruExtractor",
    "StreamTapeExtractor",
    "StreamWishExtractor",
    "VoeExtractor",
    "find_extractor",
]
