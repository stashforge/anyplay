"""Torrent records projected into shared table and five-line information panel."""
from ..core.terminal import sanitize_terminal_text, truncate_display
from ..torrents.formatting import quality_from_name
from ..torrents.ranking import decimal_size
from .styled_text import StyledText, TextRun, render_styled_text
from .table import Column, Table


def styled(*parts):
    def clean(text):
        text = str(text)
        if not text.strip():
            return ' ' * len(text)
        return (' ' * (len(text)-len(text.lstrip(' '))) + sanitize_terminal_text(text)
                + ' ' * (len(text)-len(text.rstrip(' '))))
    return StyledText(tuple(TextRun(clean(text), role) for text, role in parts))


def source_role(source):
    return 'torrent.' + source if source in ('knaben', 'nyaa', 'yts') else 'table.secondary'


def make_table(shows, width):
    rows = []
    for show in shows:
        data = show.get('_torrent') or {}
        seeds = data.get('seeders')
        dot_role = 'success' if seeds is not None and seeds >= 50 else 'torrent.amber' if seeds is not None and seeds >= 5 else 'torrent.rose' if seeds else 'muted'
        source = str(data.get('source') or 'unknown')
        rows.append(dict(title=styled(('● ' if seeds else '○ ', dot_role), (show.get('name', ''), 'row_normal')),
            size=decimal_size(data.get('size_bytes') or 0) if data.get('size_bytes') else '—',
            seeds=str(seeds) if seeds is not None else '—',
            peers=str(data['leechers']) if data.get('leechers') is not None else '—',
            res=quality_from_name(show.get('name', '')) or '—',
            source=styled((truncate_display(source, 6), source_role(source.split('+')[0])))))
    table = Table((Column('title','title',12,optional=False), Column('size','size',8,align='right'),
        Column('seeds','S',3,role='success',align='right'), Column('peers','L',3,role='torrent.rose',align='right'),
        Column('res','res',5,role='muted',align='right'), Column('source','prov',6,align='right')), rows, max(1,width-3),gap=2)
    return table, rows


def source_summary(shows):
    outcomes = next((show.get('_torrent_sources') for show in shows if show.get('_torrent_sources')), {})
    runs = []
    merged = sum(max(0, len(str((show.get('_torrent') or {}).get('source', '')).split('+'))-1) for show in shows)
    if merged:
        runs.append((f'{merged} merged', 'muted'))
    for source in ('knaben','nyaa','yts'):
        status = outcomes.get(source)
        if status and status['state'] == 'ready' and status['count']:
            if runs:
                runs.append((' · ', 'muted'))
            runs.append((f"{status['count']} {source}", source_role(source)))
    return styled(*runs)


def information_content(show, *, watched=None, context=""):
    if watched is None:
        watched = show.get('_local_progress')
    data = show.get('_torrent') or {}
    source = str(data.get('source') or 'unknown')
    from ..domain.titles import get_show_display_title
    name = get_show_display_title(show)
    from ..torrents.ranking import assess
    assessment = data.get('ranking') or assess(name, seeders=data.get('seeders'),
        leechers=data.get('leechers'), trusted=data.get('trusted'))
    trust = 'trusted' if data.get('trusted') is True else 'untrusted' if data.get('trusted') is False else 'trust unknown'
    trust_role = 'success' if data.get('trusted') is True else 'muted'
    seeds, peers = data.get('seeders'), data.get('leechers')
    stats = [('seeds ', 'success'), (str(seeds) if seeds is not None else '—', 'library.value'),
             (' · leechers ', 'torrent.rose'), (str(peers) if peers is not None else '—', 'library.value'),
             (' · '+decimal_size(data['size_bytes']), 'library.value')] if data.get('size_bytes') else [
             ('seeds '+str(seeds if seeds is not None else '—')+' · leechers '+str(peers if peers is not None else '—'), 'library.value')]
    if data.get('completed') is not None:
        stats.append((' · downloads '+str(data['completed']), 'secondary'))
    if watched is not None:
        stats = [('S ', 'success'), (str(seeds) if seeds is not None else '—', 'library.value'),
                 (' · L ', 'torrent.rose'), (str(peers) if peers is not None else '—', 'library.value')]
        if data.get('size_bytes'):
            stats.append((' · '+decimal_size(data['size_bytes']), 'library.value'))
        stats.extend([(' · Watched ', 'success'), (str(watched), 'library.value')])
    status = [('magnet ready', 'success'), (f" · {len(data['tracker_urls'])} trackers" if data.get('tracker_urls') else ' · default trackers', 'secondary'),
              (f" · score {assessment['score']:.1f}", 'library.value')]
    languages = []
    if data.get('audio_langs'):
        languages.append('audio '+', '.join(data['audio_langs']))
    if data.get('sub_langs'):
        languages.append('subs '+', '.join(data['sub_langs']))
    warnings = assessment['warnings']
    final = ('check: '+', '.join(warnings)) if warnings else 'No title warnings'
    if languages:
        final += ' · '+' · '.join(languages)
    lines = [styled((name,'primary')),
        styled(('['+source+']', source_role(source.split('+')[0])), (' · ', 'muted'), (trust, trust_role),
               (' · '+' · '.join(assessment['tags']) if assessment['tags'] else '', 'library.value')),
        styled(*stats), styled(*status), styled((final, 'torrent.amber' if warnings else 'secondary'))]
    if context:
        lines[-1] = styled((context, 'secondary'))
    return tuple(lines)


def information(show, width, theme):
    return tuple(render_styled_text(line, width=width, theme=theme) for line in information_content(show))


def filter_values(show):
    """Filter only returned index metadata or the existing release-name tags."""
    from ..torrents.ranking import tags
    import re
    data = show.get('_torrent') or {}
    name = show.get('name') or ''
    release_tags = tags(name)
    codec = next((tag for tag in release_tags if re.fullmatch(r'HEVC|AVC|AV1|x264|x265|H[.]?26[45]', tag, re.I)), None)
    aliases = {'hevc': ('hevc', 'x265', 'h265', 'h.265'),
               'avc': ('avc', 'x264', 'h264', 'h.264')}
    codecs = next((group for group in aliases.values() if codec and codec.casefold() in group), (codec,) if codec else ())
    return tuple(dict(title=tuple(filter(None, (name, show.get('englishName'), show.get('romajiName')))),
        size=data.get('size_bytes') or None, seeds=data.get('seeders'), leechers=data.get('leechers'),
        res=quality_from_name(name) or None, source=tuple(str(data.get('source') or '').split('+')),
        codec=codecs, audio=tuple(data.get('audio_langs') or ()), subs=tuple(data.get('sub_langs') or ()),
        trusted=data.get('trusted')).items())
