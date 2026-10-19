"""UI compatibility exports, loaded only when explicitly requested."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .banners import print_app_banner as print_app_banner
    from .banners import print_episode_header as print_episode_header
    from .modals import (
        confirm_auto_anilist_match as confirm_auto_anilist_match,
    )
    from .modals import (
        confirm_auto_match as confirm_auto_match,
    )
    from .modals import (
        make_shows_info_fn as make_shows_info_fn,
    )
    from .modals import (
        make_single_show_info_fn as make_single_show_info_fn,
    )
    from .modals import (
        manual_anilist_input_header as manual_anilist_input_header,
    )
    from .modals import (
        manual_match_input_header as manual_match_input_header,
    )
    from .modals import (
        search_cover_header as search_cover_header,
    )
    from .modals import (
        search_input_header as search_input_header,
    )
    from .modals import (
        search_result_header as search_result_header,
    )
    from .modals import (
        select_provider_for_match as select_provider_for_match,
    )
    from .panels import (
        render_header_card as render_header_card,
    )
    from .panels import (
        render_menu_card as render_menu_card,
    )
    from .panels import (
        render_modal_card as render_modal_card,
    )
    from .panels import (
        render_search_header as render_search_header,
    )
    from .picker import tui_pick as tui_pick
    from .player_screen import (
        _player_ui_state as _player_ui_state,
    )
    from .player_screen import (
        activate as activate,
    )
    from .player_screen import (
        add_status_line as add_status_line,
    )
    from .player_screen import (
        deactivate as deactivate,
    )
    from .player_screen import (
        render as render,
    )
    from .player_screen import (
        update_mpv_props as update_mpv_props,
    )
    from .player_screen import (
        update_stream_info as update_stream_info,
    )

_EXPORTS = {
    "print_app_banner": "banners",
    "print_episode_header": "banners",
    "confirm_auto_anilist_match": "modals",
    "confirm_auto_match": "modals",
    "make_shows_info_fn": "modals",
    "make_single_show_info_fn": "modals",
    "manual_anilist_input_header": "modals",
    "manual_match_input_header": "modals",
    "search_cover_header": "modals",
    "search_input_header": "modals",
    "search_result_header": "modals",
    "select_provider_for_match": "modals",
    "render_header_card": "panels",
    "render_menu_card": "panels",
    "render_modal_card": "panels",
    "render_search_header": "panels",
    "tui_pick": "picker",
    "_player_ui_state": "player_screen",
    "activate": "player_screen",
    "add_status_line": "player_screen",
    "deactivate": "player_screen",
    "render": "player_screen",
    "update_mpv_props": "player_screen",
    "update_stream_info": "player_screen",
}

__all__ = tuple(name for name in _EXPORTS if not name.startswith("_"))


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
