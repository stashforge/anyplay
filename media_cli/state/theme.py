"""Application-owned default style roles, separate from generic UI components."""

from dataclasses import replace

from ..ui.theme import DEFAULT_THEME, BasicColor, TextStyle

APPLICATION_THEME = replace(
    DEFAULT_THEME,
    extensions=(
        ("anilist.watching", TextStyle(BasicColor.GREEN)),
        ("anilist.completed", TextStyle(BasicColor.CYAN)),
        ("anilist.planning", TextStyle(BasicColor.YELLOW)),
        ("anilist.dropped", TextStyle(BasicColor.RED)),
        ("anilist.paused", TextStyle(BasicColor.MAGENTA)),
        ("anilist.rewatching", TextStyle(BasicColor.GREEN)),
        ("metadata.status", TextStyle(250)),
        ("metadata.airing", TextStyle(117)),
        ("metadata.offline", TextStyle((230, 80, 80))),
        ("metadata.separator", TextStyle(248)),
    ),
)
