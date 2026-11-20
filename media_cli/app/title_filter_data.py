"""Primitive title filter snapshots from already-loaded display metadata."""
import re

from ..core.terminal import sanitize_terminal_text, strip_ansi
from ..domain.metadata import anime_status_label
from ..ui.smart_filter import FilterField

TITLE_FIELDS = (
    FilterField('title'), FilterField('genre', aliases=('genres',)),
    FilterField('status'), FilterField('type'), FilterField('year', 'number'),
    FilterField('season'),
)
ANILIST_FIELDS = TITLE_FIELDS + (FilterField('list'), FilterField('progress', 'number'))
LIBRARY_FIELDS = TITLE_FIELDS + (FilterField('provider'), FilterField('downloaded', 'number'))
HISTORY_FIELDS = TITLE_FIELDS + (
    FilterField('provider'), FilterField('watched', 'number'),
    FilterField('downloaded', 'number'), FilterField('new', 'bool'),
    FilterField('translation'),
)


def title_filter_values(show):
    titles = tuple(sanitize_terminal_text(show[key]) for key in
                   ('name', 'englishName', 'romajiName', 'nativeName') if show.get(key))
    genres = show.get('genres') or show.get('_provider_genres') or ()
    if isinstance(genres, str):
        genres = tuple(part.strip() for part in re.split(r'[,•]', genres) if part.strip())
    else:
        genres = tuple(str(value) for value in genres if value)
    start = show.get('airedStart')
    year = start.get('year') if isinstance(start, dict) else None
    if year is None and isinstance(start, str):
        match = re.search(r'\b(?:19|20)\d{2}\b', start)
        year = int(match[0]) if match else None
    return dict(title=titles, genre=genres, status=strip_ansi(anime_status_label(show)).strip(),
                type=str(show.get('type') or ''), year=year,
                season=str((show.get('season') or {}).get('quarter') or '') if isinstance(show.get('season'), dict) else str(show.get('season') or ''))


def library_filter_values(show, title, downloaded):
    from ..providers.shared.models import title_provider_key
    from .footer import provider_label
    values = title_filter_values(show)
    values['title'] = tuple(dict.fromkeys((title, *values['title'])))
    values.update(provider=(title_provider_key(show), provider_label(show)), downloaded=downloaded)
    return tuple(values.items())


def anilist_filter_values(show):
    values = title_filter_values(show)
    status = str(show.get('_anilist_list') or '')
    label = {'CURRENT': 'watching', 'PLANNING': 'planning', 'COMPLETED': 'completed',
             'REPEATING': 'rewatching', 'PAUSED': 'paused', 'DROPPED': 'dropped'}.get(status, status)
    values.update(list=(status, label) if status else (), progress=show.get('_anilist_progress'))
    return tuple(values.items())
