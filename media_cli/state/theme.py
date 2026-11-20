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
        ("episode.label", TextStyle(252)),
        ("episode.watched", TextStyle(244)),
        ("episode.downloaded", TextStyle((166, 227, 161))),
        ("episode.missing", TextStyle(167)),
        ("history.new_release", TextStyle((166, 227, 161))),
        ("player.title", TextStyle((145, 185, 245), bold=True)),
        ("player.section", TextStyle((190, 180, 235), bold=True)),
        ("player.text", TextStyle((170, 180, 195))),
        ("player.status", TextStyle(BasicColor.CYAN, bold=True)),
        ("player.log", TextStyle(BasicColor.BRIGHT_BLUE)),
        ("player.progress", TextStyle(115)),
        ("player.progress_empty", TextStyle(240)),
        ("player.episode", TextStyle(252)),
        ("player.metadata", TextStyle(248)),
        ("player.airing", TextStyle(117)),
        ("player.preferred", TextStyle(BasicColor.YELLOW)),
        ("player.incognito", TextStyle((155, 125, 185))),
        ("player.shortcut", TextStyle(244)),
        ("player.time", TextStyle(250)),
        ("dialog.heading", TextStyle(bold=True)),
    ),
)

# Optional mint preset for the new search/provider screens. Existing screens
# retain APPLICATION_THEME until their layouts are reviewed.
MINT_SCREEN_THEME = replace(
    APPLICATION_THEME,
    primary=TextStyle(253), secondary=TextStyle(245), details=TextStyle(253), muted=TextStyle(240),
    separator=TextStyle(238), context=TextStyle(245), actions=TextStyle(245),
    counter=TextStyle(114),
    prompt=TextStyle(114), query=TextStyle(253),
    query_cursor=TextStyle(0, background=114),
    row_normal=TextStyle(), row_selected=TextStyle(), row_match=TextStyle(114),
    tab_active=TextStyle(114), tab_inactive=TextStyle(240),
    extensions=tuple((role, TextStyle() if role == "episode.label" else style)
                     for role, style in APPLICATION_THEME.extensions) + (
        ("screen.brand", TextStyle(114, bold=True)),
        ("screen.key", TextStyle(108)),
        ("screen.column", TextStyle(240)),
        ("table.secondary", TextStyle(245)),
        ("torrent.knaben", TextStyle(114)),
        ("torrent.yts", TextStyle(114)),
        ("torrent.nyaa", TextStyle(176)),
        ("torrent.amber", TextStyle(179)),
        ("torrent.rose", TextStyle(174)),
        ("table.active", TextStyle(114)),
        ("info.category", TextStyle(114)),
        ("engine.api", TextStyle(114)),
        ("engine.hybrid", TextStyle(183)),
        ("engine.scraper", TextStyle(179)),
        ("screen.home_selected", TextStyle(253, bold=True)),
        ("screen.logo_high", TextStyle(157, bold=True)),
        ("screen.logo_low", TextStyle(65, bold=True)),
        ("library.value", TextStyle()),
        ("library.genre", TextStyle()),
        ("library.progress", TextStyle()),
        ("library.label", TextStyle(240)),
        ("library.separator", TextStyle(240)),
        ("library.airing", TextStyle(117)),
        ("library.finished", TextStyle(114)),
        ("library.pending", TextStyle(179)),
        ("library.cancelled", TextStyle(174)),
    ),
)

# Presets share semantic roles; screen layouts never choose preset colors.
MINT_THEME = MINT_SCREEN_THEME
_LAVENDER_COLORS = {
    None: (216, 211, 233),
    253: (234, 226, 249), 252: (216, 211, 233), 250: (216, 211, 233),
    248: (188, 178, 207), 245: (163, 151, 183), 244: (146, 133, 169),
    240: (104, 92, 124), 238: (72, 63, 87),
    114: 183, 108: (170, 143, 199), 157: 225, 65: (112, 84, 147),
    176: 211, 111: 153,
}


def _lavender_style(style):
    colors = {**_LAVENDER_COLORS, (145, 185, 245): (200, 175, 235),
              (190, 180, 235): (215, 185, 240)}
    return replace(style, foreground=colors.get(style.foreground, style.foreground),
                   background=(_LAVENDER_COLORS.get(style.background, style.background)
                               if style.background is not None else None))


def _lavender_theme(base):
    from dataclasses import fields
    return replace(base, **{
        field.name: _lavender_style(getattr(base, field.name))
        for field in fields(base) if isinstance(getattr(base, field.name), TextStyle)
        and field.name not in {'success', 'warning', 'error', 'selected_count'}
    }, extensions=tuple((role, _lavender_style(style)) for role, style in base.extensions))


LAVENDER_THEME = _lavender_theme(MINT_THEME)
# Keep success/failure meanings independent of decorative accent colors.
LAVENDER_THEME = replace(LAVENDER_THEME, extensions=tuple(
    (role, dict(MINT_THEME.extensions)[role] if role in {
        'library.finished', 'library.progress', 'history.new_release', 'episode.downloaded',
        'engine.api', 'torrent.knaben', 'torrent.yts',
    } else style) for role, style in LAVENDER_THEME.extensions
))
# Ember changes the full text hierarchy while inheriting the terminal canvas.
_EMBER_COLORS = {
    None: (224, 211, 190),
    253: (238, 222, 199), 252: (224, 211, 190), 250: (224, 211, 190),
    248: (189, 171, 150), 245: (166, 147, 128), 244: (150, 133, 117),
    240: (113, 96, 81), 238: (79, 65, 55),
    114: (224, 167, 97), 108: (190, 143, 91),
    157: (255, 215, 158), 65: (135, 91, 57),
    (145, 185, 245): (238, 195, 139),
    (190, 180, 235): (216, 173, 126),
    (170, 180, 195): (201, 183, 162),
}


def _ember_theme(base):
    from dataclasses import fields

    def warm(style):
        return replace(style, foreground=_EMBER_COLORS.get(style.foreground, style.foreground),
                       background=(_EMBER_COLORS.get(style.background, style.background)
                                   if style.background is not None else None))

    semantic = {
        'library.finished', 'history.new_release', 'episode.downloaded',
        'engine.api', 'torrent.knaben', 'torrent.yts',
    }
    return replace(base, **{
        item.name: warm(getattr(base, item.name))
        for item in fields(base) if isinstance(getattr(base, item.name), TextStyle)
        and item.name not in {'success', 'warning', 'error', 'selected_count'}
    }, extensions=tuple((role, style if role in semantic else warm(style))
                        for role, style in base.extensions))


EMBER_THEME = _ember_theme(MINT_THEME)
MIDNIGHT_THEME = replace(LAVENDER_THEME, background=(24, 24, 37),
    muted=TextStyle((129, 117, 150)), tab_inactive=TextStyle((129, 117, 150)))
HEARTH_THEME = replace(EMBER_THEME, background=(33, 26, 23),
    muted=TextStyle((139, 119, 100)), tab_inactive=TextStyle((139, 119, 100)))
THEME_PRESETS = {'mint': MINT_THEME, 'lavender': LAVENDER_THEME, 'ember': EMBER_THEME,
                 'midnight': MIDNIGHT_THEME, 'hearth': HEARTH_THEME}


def selected_theme_name():
    """Read once at startup; never modify config or print from theme loading."""
    import os
    name = os.environ.get('ANYPLAY_THEME')
    if name is None:
        from .config import load_config_file
        from .paths import CONFIG_PATH
        name = load_config_file(CONFIG_PATH, disabled=True).get('theme', 'mint')
    name = str(name).strip().lower()
    return name if name in THEME_PRESETS else 'mint'


_SELECTED_THEME = selected_theme_name()
MINT_SCREEN_THEME = THEME_PRESETS[_SELECTED_THEME]
if _SELECTED_THEME in {'lavender', 'midnight'}:
    APPLICATION_THEME = _lavender_theme(APPLICATION_THEME)

if _SELECTED_THEME in {'ember', 'hearth'}:
    APPLICATION_THEME = _ember_theme(APPLICATION_THEME)

if MINT_SCREEN_THEME.background is not None:
    APPLICATION_THEME = replace(APPLICATION_THEME, background=MINT_SCREEN_THEME.background)
