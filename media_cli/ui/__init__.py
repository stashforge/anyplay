"""Terminal UI components."""

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
