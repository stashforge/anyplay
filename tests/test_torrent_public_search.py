from media_cli.torrents.public_search import parse_knaben, parse_yts, search_public_torrents
from media_cli.torrents.models import TorrentResult, TorrentSearchConfig
from media_cli.ui.torrent_results import make_table, information, source_summary
from media_cli.state.theme import MINT_SCREEN_THEME
import pytest

HASH = 'a'*40


def test_knaben_magnet_trackers_and_columns():
    records = parse_knaben('<table><tbody><tr data-id="1"><td>Anime</td><td><a title="Example 1080p" href="magnet:?xt=urn:btih:'+HASH+'&amp;tr=udp%3A%2F%2Ftracker">Example</a></td><td>2 GiB</td><td>today</td><td>123</td><td>4</td></tr></tbody></table>')
    assert records[0].infohash == HASH
    assert records[0].size_bytes == 2*1024**3
    assert records[0].seeders == 123
    assert records[0].tracker_urls == ('udp://tracker',)
    with pytest.raises(ValueError):
        parse_knaben('<html>Just a moment</html>')
    assert parse_knaben('<tbody></tbody>') == []


def test_public_indexes_merge_hash_trackers_and_report_failure():
    updates = []
    def fail(*args):
        raise ValueError('unavailable')
    records, outcomes = search_public_torrents('example', config=TorrentSearchConfig(), fetchers={
        'knaben': lambda *a: [TorrentResult('knaben','Example',HASH,seeders=5,tracker_urls=('one',))],
        'nyaa': lambda *a: [TorrentResult('nyaa','Example 1080p',HASH,seeders=10,tracker_urls=('two',))],
        'yts': fail}, progress=lambda records, sources: updates.append((list(records), sources)))
    assert len(updates) == 3
    assert len(records) == 1 and records[0].seeders == 10
    assert set(records[0].tracker_urls) == {'one','two'}
    assert outcomes['yts']['state'] == 'failed'


def test_yts_and_torrent_table_panel():
    records = parse_yts({'status':'ok','data':{'movies':[{'title_long':'Example (2020)','torrents':[
        {'hash':HASH,'quality':'1080p','size_bytes':123,'seeds':55,'peers':4}]}]}})
    from media_cli.providers.torrent import TorrentProvider
    shows = [TorrentProvider(search_fn=lambda query: records)._title_from_result(records[0])]
    shows[0]['_torrent_sources'] = {'yts': {'state':'ready','count':1}}
    table, rows = make_table(shows, 80)
    assert '1080p' in table.render(rows[0], theme=MINT_SCREEN_THEME)
    assert '1 yts' in source_summary(shows).text
    assert len(information(shows[0], 80, MINT_SCREEN_THEME)) == 5


def test_all_indexes_failing_is_not_an_empty_search():
    def fail(*args):
        raise ValueError('blocked')
    with pytest.raises(RuntimeError, match='search unavailable'):
        search_public_torrents('example', fetchers={'knaben':fail,'nyaa':fail,'yts':fail})


def test_progress_uses_same_seeder_filter_as_final_results():
    updates = []
    records, _ = search_public_torrents('example', config=TorrentSearchConfig(min_seeders=2),
        fetchers={'nyaa':lambda *args:[TorrentResult('nyaa','Example',HASH,seeders=1)]},
        progress=lambda records, outcomes: updates.append(records))
    assert records == [] and updates == [[]]


def test_colored_fragments_keep_spaces_and_summary_omits_empty_sources():
    from media_cli.core.terminal import strip_ansi
    from media_cli.ui.torrent_results import styled
    from media_cli.ui.styled_text import render_styled_text
    value = styled(('S ', 'success'), ('22', 'library.value'), ('   L ', 'torrent.rose'), ('3', 'library.value'))
    assert strip_ansi(render_styled_text(value,width=80,theme=MINT_SCREEN_THEME)) == 'S 22   L 3'
    show = {'name':'Example','_torrent':{'source':'knaben','seeders':50}, '_torrent_sources':{
        'knaben':{'state':'ready','count':50}, 'nyaa':{'state':'ready','count':0}, 'yts':{'state':'failed','count':0}}}
    assert source_summary([show]).text == '50 knaben'
    table, rows = make_table([show],80)
    assert strip_ansi(table.render(rows[0],theme=MINT_SCREEN_THEME)).startswith('● Example')


def test_provider_preserves_swarm_ranking_instead_of_anime_name_sort():
    from media_cli.providers.torrent import TorrentProvider
    records = [TorrentResult('nyaa','Z release','a'*40,seeders=100),TorrentResult('nyaa','A release','b'*40,seeders=5)]
    shows = TorrentProvider(search_fn=lambda query:records).search('release')
    assert [show['name'] for show in shows] == ['Z release','A release']


def test_source_column_caps_long_names_without_truncating_details():
    from media_cli.core.terminal import strip_ansi
    shows = [dict(name='Example 1080p', _torrent=dict(source=source, seeders=10))
             for source in ('knaben+nyaa+yts', 'knaben', 'nyaa', 'yts', 'longprovider')]
    table, rows = make_table(shows, 100)
    source_index = next(i for i, column in enumerate(table.columns) if column.key == 'source')
    assert table.widths[source_index] == 6
    assert [row['source'].text for row in rows] == ['knabe…', 'knaben', 'nyaa', 'yts', 'longp…']
    details = '\n'.join(strip_ansi(line) for line in information(shows[0], 120, MINT_SCREEN_THEME))
    assert 'knaben+nyaa+yts' in details
