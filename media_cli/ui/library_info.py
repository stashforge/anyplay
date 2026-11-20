"""Five fixed library information slots, independent of legacy header fallbacks."""
from ..core.terminal import sanitize_terminal_text, truncate_display, display_width
from ..domain.metadata import format_info_metadata_parts, prepare_show_display_state, genre_text
from ..domain.titles import get_display_titles, get_show_display_title
from ..state.theme import MINT_SCREEN_THEME
from .screen_chrome import RESET, role_prefix
from .metadata_styles import metadata_content
from .styled_text import StyledText, TextRun, render_styled_text


def finish_info_panel(base, genres, context, width, *, theme=MINT_SCREEN_THEME):
    """Shared five-slot contract: title, alternative, metadata, genres, context."""
    genres = sanitize_terminal_text(genres) or 'No genres available'
    genre_runs = []
    for index, genre in enumerate(genres.split(' • ')):
        if index:
            genre_runs.append(TextRun(' • ', 'library.separator'))
        genre_runs.append(TextRun(genre, 'muted' if genres == 'No genres available' else 'library.genre'))
    lines = list(base[:3])
    lines.append(render_styled_text(StyledText(tuple(genre_runs)), width=width, theme=theme))
    if context:
        from .covers import info_cover_size
        context = sanitize_terminal_text(context)
        budget = width - info_cover_size()[0] - 2
        if display_width(context) > budget:
            context = context.replace('Last activity ', 'Activity ').replace('Continue EP ', 'EP ')
        if display_width(context) > budget:
            context = context.replace('Updated ', 'Upd ')
        lines.append(render_styled_text(context_content(context), width=width, theme=theme))
    return tuple(lines)


def library_info_lines(show, ttype, width, *, theme=MINT_SCREEN_THEME, local_only=True, context=""):
    if not show:
        return []
    if show.get("_torrent"):
        from ..core.storage import get_local_progress
        from .torrent_results import information
        prepared = dict(show, _local_progress=get_local_progress(show, ttype))
        return list(information(prepared, width, theme))
    prepare_show_display_state(show, ttype, prefer_watched_episodes=local_only)
    title = get_show_display_title(show)
    alternative = get_display_titles(show, title) or "No alternative title"
    metadata_parts = format_info_metadata_parts(show, ttype, local_only=local_only)
    metadata = render_styled_text(metadata_content(metadata_parts), width=width, theme=theme)
    genres = genre_text(show)
    def line(value, role):
        return role_prefix(theme, role) + truncate_display(sanitize_terminal_text(value), max(1, width - 1)) + RESET
    return list(finish_info_panel((line(title, 'primary'), line(alternative, 'muted' if alternative == 'No alternative title' else 'secondary'),
                                  metadata), genres, context, width, theme=theme))


def context_content(context):
    """Quiet field labels with secondary values; no extra accent colors."""
    import re
    runs = []
    for index, field in enumerate(re.split(r'\s{2,}|\s+[·•]\s+', sanitize_terminal_text(context))):
        if not field:
            continue
        if runs:
            runs.append(TextRun('  ', 'library.separator'))
        match = re.match(r'^(Updated|Upd|Last activity|Activity|Continue EP|EP) (.+)$', field)
        downloaded = re.fullmatch(r'(\d+) (downloaded|downloads?)', field)
        if match:
            runs.extend((TextRun(match[1] + ' ', 'library.label'), TextRun(match[2], 'secondary')))
        elif downloaded:
            runs.extend((TextRun(downloaded[1] + ' ', 'secondary'), TextRun(downloaded[2], 'library.label')))
        else:
            runs.append(TextRun(field, 'secondary'))
    return StyledText(tuple(runs))
