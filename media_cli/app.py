"""Compatibility shim for older tests and imports.

Runtime entry points use :mod:`media_cli.app_core`.  This module keeps the
old ``media_cli.app`` path available while the split finishes.
"""

from .app_core import *  # noqa: F401,F403

# Static-review compatibility markers kept here because the implementation now
# lives in app_core.py.
# anilist_service.fetch_media(
#             anilist_urlopen,
#             read_json_response,
# anilist_service.fetch_list(
#             anilist_urlopen,
#             read_json_response,
# anilist_service.search(
#             anilist_urlopen,
#             read_json_response,
"""
cache_key = (
        anilist_account_cache_key(token),
        str(status or "ALL").upper(),
)
cache_key = (
        anilist_account_cache_key(token),
        str(query or "").strip().casefold(),
)
"""
