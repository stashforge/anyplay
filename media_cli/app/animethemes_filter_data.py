"""Tab-specific filter snapshots from loaded AnimeThemes results only."""
from ..ui.smart_filter import FilterField

_SCHEMAS = {
    'anime': ('title', 'type', 'year', 'season', 'themes'),
    'animethemes': ('title', 'artist', 'anime', 'type', 'year', 'season'),
    'artists': ('title', 'group'),
    'series': ('title',),
    'studios': ('title',),
    'playlists': ('title', 'owner', 'tracks'),
}


def search_filter_data(tab, rows):
    schema = tuple(FilterField(name, 'number' if name in ('year', 'themes', 'tracks') else 'text')
                   for name in _SCHEMAS.get(tab, ('title',)))
    values = []
    for row in rows:
        raw = row.get('raw') or {}
        anime = (raw.get('anime') or {}) if tab == 'animethemes' else raw
        song = raw.get('song') or {}
        title = song.get('title') if tab == 'animethemes' else raw.get('name')
        titles = tuple(value for value in (title, raw.get('englishName'), raw.get('nativeName')) if isinstance(value, str) and value)
        item = dict(title=titles or (row.get('label', ''),),
                    year=anime.get('year'), season=anime.get('season') or '',
                    type=(raw.get('type') if tab == 'animethemes' else raw.get('media_format')) or '',
                    artist=tuple(artist.get('name') for artist in song.get('artists') or [] if artist.get('name')),
                    anime=tuple(anime.get(key) for key in ('name', 'englishName', 'nativeName') if anime.get(key)),
                    group=tuple(group.get('name') for group in raw.get('groups') or [] if group.get('name')),
                    owner=(raw.get('user') or {}).get('name') or '', tracks=raw.get('tracksCount'))
        themes = raw.get('animethemes')
        item['themes'] = len(themes) if isinstance(themes, list) else None
        values.append(tuple((field.name, item[field.name]) for field in schema))
    return dict(filter_schema=schema, filter_values=tuple(values))


SONG_FIELDS = tuple(FilterField(name, 'number' if name == 'year' else 'text')
                    for name in _SCHEMAS['animethemes'])


def song_filter_values(row):
    """One projection for collection menus and artist-performance rows."""
    raw = row.get('raw') or {}
    theme = raw.get('theme') or raw
    values = dict(search_filter_data('animethemes', [dict(raw=theme, label=row.get('label', ''))])['filter_values'][0])
    if isinstance(row.get('song'), str) and row['song']:
        values['title'] = (row['song'],)
    if not values['artist'] and isinstance(row.get('artists'), str):
        values['artist'] = tuple(name.strip() for name in row['artists'].split(',') if name.strip())
    if not values['artist'] and row.get('artist'):
        values['artist'] = (row['artist'],)
    if not values['anime'] and isinstance(row.get('anime'), str):
        values['anime'] = (row['anime'],)
    if not values['type']:
        # The row's theme label is provider data (OP1/ED2), not a guessed episode.
        import re
        match = re.match(r'^(OP|ED)(?:\d|$)', str(row.get('theme') or ''), re.I)
        values['type'] = match[1].upper() if match else ''
    return tuple(values.items())
