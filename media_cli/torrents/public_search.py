"""Public indexes used by standalone torrent search; no peer or payload access."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
import logging
import re
from urllib.parse import quote, parse_qs, urlsplit
from urllib.request import Request, urlopen

from . import clients
from .models import TorrentResult, TorrentSearchConfig
from .search import parse_nyaa_rss, merge_results, _parse_size_text, _group_from_name

_log = logging.getLogger(__name__)


def _magnet(value):
    query = parse_qs(urlsplit(value).query)
    hashes = [part[9:] for part in query.get('xt', ()) if part.lower().startswith('urn:btih:')]
    if not hashes or not re.fullmatch(r'[a-fA-F0-9]{40}', hashes[0]):
        return '', ()
    return hashes[0].lower(), tuple(dict.fromkeys(query.get('tr', ())))


class _KnabenTable(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.cells, self.text, self.magnet, self.title = [], [], [], '', ''
        self.in_cell = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'tr':
            self.cells, self.magnet, self.title = [], '', ''
        if tag in ('td', 'th'):
            self.in_cell, self.text = True, []
        if tag == 'a' and attrs.get('href', '').startswith('magnet:'):
            self.magnet = attrs['href']
            self.title = attrs.get('title', '')

    def handle_data(self, data):
        if self.in_cell:
            self.text.append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.in_cell:
            self.cells.append(' '.join(''.join(self.text).split()))
            self.in_cell = False
        if tag == 'tr' and self.magnet and len(self.cells) >= 6:
            self.rows.append((self.cells[:], self.magnet, self.title))


def parse_knaben(html):
    parser = _KnabenTable()
    parser.feed(html)
    if '<tbody' not in html.lower():
        raise ValueError('Knaben did not return a results table (blocked or changed response)')
    results = []
    for cells, magnet, title in parser.rows:
        infohash, trackers = _magnet(magnet)
        if not infohash:
            continue
        def count(value):
            value = value.replace(',', '').replace(' ', '')
            return int(value) if value.isdigit() else None
        name = title or cells[1]
        results.append(TorrentResult(source='knaben', name=name, infohash=infohash,
            size_bytes=_parse_size_text(cells[2]), seeders=count(cells[4]), leechers=count(cells[5]),
            group=_group_from_name(name), tracker_urls=trackers))
    return results


def fetch_knaben(query, limit, offset):
    request = Request('https://knaben.org/search/' + quote(query, safe='') + '/0/1/seeders',
                      headers={'User-Agent': 'Mozilla/5.0 Anyplay'})
    with urlopen(request, timeout=20) as response:
        return parse_knaben(response.read(8 * 1024 * 1024).decode('utf-8', errors='replace'))[:limit]


def parse_yts(data):
    if not isinstance(data, dict) or data.get('status') != 'ok' or not isinstance(data.get('data'), dict):
        raise ValueError('YTS did not return a valid search response')
    results = []
    for movie in data['data'].get('movies') or []:
        for torrent in movie.get('torrents') or []:
            infohash = str(torrent.get('hash', '')).lower()
            if not re.fullmatch(r'[0-9a-f]{40}', infohash):
                continue
            name = ' '.join(str(part) for part in (movie.get('title_long') or movie.get('title'),
                            torrent.get('quality'), torrent.get('type'), '[YTS]') if part)
            results.append(TorrentResult(source='yts', name=name, infohash=infohash,
                size_bytes=int(torrent.get('size_bytes') or _parse_size_text(torrent.get('size'))),
                seeders=torrent.get('seeds'), leechers=torrent.get('peers'), group='YTS'))
    return results


def fetch_yts(query, limit, offset):
    from urllib.parse import urlencode
    last_error = None
    for domain in ('yts.mx', 'yts.rs', 'yts.lt', 'yts.am'):
        try:
            return parse_yts(clients.fetch_json('https://' + domain + '/api/v2/list_movies.json?' +
                urlencode({'query_term': query, 'limit': min(50, limit)}), timeout=10))
        except Exception as exc:
            last_error = exc
    raise ValueError('YTS search unavailable on its mirrors') from last_error


def search_public_torrents(query, *, config=None, fetchers=None, progress=None):
    config = config or TorrentSearchConfig()
    fetchers = fetchers or {'knaben': fetch_knaben,
        'nyaa': lambda q, n, o: parse_nyaa_rss(clients.fetch_nyaa_rss(q, n, o)), 'yts': fetch_yts}
    collected, outcomes = [], {}
    def eligible(item):
        return ((item.seeders is None and config.allow_unknown_seeders) or
                (item.seeders is not None and item.seeders >= config.min_seeders))
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix='torrent-index') as pool:
        tasks = {pool.submit(fetch, query, config.limit, config.offset): name for name, fetch in fetchers.items()}
        for task in as_completed(tasks):
            name = tasks[task]
            try:
                batch = task.result()
                outcomes[name] = {'count': len(batch), 'state': 'ready'}
                collected.extend(batch)
            except Exception as exc:
                _log.warning('Torrent index %s failed: %s', name, exc)
                outcomes[name] = {'count': 0, 'state': 'failed'}
            merged = merge_results(collected)
            from .ranking import sort_key
            merged.sort(key=lambda item: sort_key(item, query))
            if progress:
                progress([item for item in merged if eligible(item)], dict(outcomes))
    # Apply the configured seeder floor without penalising complete season batches by size.
    if outcomes and all(outcome['state'] == 'failed' for outcome in outcomes.values()):
        raise RuntimeError('Torrent search unavailable: Knaben, Nyaa and YTS requests failed. Retry or inspect debug logs.')
    result = [item for item in merged if eligible(item)]
    return result, outcomes
