from media_cli.torrents.ranking import assess, sort_key, decimal_size
from media_cli.torrents.models import TorrentResult
from media_cli.torrents.search import merge_results, parse_nyaa_rss
from media_cli.ui.torrent_results import make_table, information
from media_cli.state.theme import MINT_SCREEN_THEME
from media_cli.core.terminal import strip_ansi


def test_decimal_sizes_have_real_decimal_units_and_space():
    assert decimal_size(728_000_000) == '728 MB'
    assert decimal_size(1_400_000_000) == '1.4 GB'
    assert decimal_size(100) == '100 B'
    assert decimal_size(0) == '0 B'


def test_quality_trust_and_dead_swarm_influence_explained_score():
    base = assess('Show 1080p WEB-DL AVC', seeders=100, leechers=10)
    trusted = assess('Show 1080p WEB-DL AVC', seeders=100, leechers=10, trusted=True)
    assert trusted['score'] == base['score'] + 12
    assert assess('Show CAM',seeders=0)['warnings'] == ('zero reported seeds','camera recording')
    assert assess('Show 1080p',seeders=None)['warnings'] == ()
    assert assess('Show OST',seeders=1)['warnings'] == ('soundtrack',)


def test_title_relevance_then_score_order_instead_of_seeds_alone():
    records = [TorrentResult('nyaa','Unrelated 1080p','a'*40,seeders=300),
               TorrentResult('nyaa','Show 720p','b'*40,seeders=110),
               TorrentResult('nyaa','Show 1080p BluRay x265','c'*40,seeders=100)]
    assert [row.infohash[0] for row in sorted(records,key=lambda row:sort_key(row,'Show'))] == ['c','b','a']


def test_nyaa_trust_retained_through_hash_merge():
    xml = '<rss xmlns:nyaa="https://nyaa.si/xmlns/nyaa"><channel><item><title>Show</title><nyaa:infoHash>'+'a'*40+'</nyaa:infoHash><nyaa:trusted>Yes</nyaa:trusted></item></channel></rss>'
    row = parse_nyaa_rss(xml)[0]
    merged = merge_results([TorrentResult('knaben','Show','a'*40),row])[0]
    assert merged.trusted is True
    assert TorrentResult('yts','Show','b'*40).trusted is None


def test_table_right_aligns_supporting_columns_and_panel_has_five_useful_lines():
    shows = [{'name':'A','_torrent':{'seeders':5,'leechers':2,'size_bytes':1_400_000_000,'source':'nyaa','completed':3578}},
             {'name':'B','_torrent':{'seeders':100,'leechers':20,'size_bytes':728_000_000,'source':'knaben','trusted':True}}]
    table, rows = make_table(shows,90)
    rendered=[strip_ansi(table.render(row,theme=MINT_SCREEN_THEME)) for row in rows]
    assert rendered[0].index('5') == rendered[1].index('100')+2
    assert '1.4 GB' in rendered[0]
    lines=[strip_ansi(line) for line in information(shows[0],120,MINT_SCREEN_THEME)]
    assert len(lines)==5 and all(lines)
    assert 'downloads 3578' in lines[2]
    assert 'trust unknown' in lines[1] and 'score ' in lines[3]
    assert lines[4] == 'No title warnings'


def test_catalog_loading_text_is_about_files_not_translation():
    from media_cli.app.loading_messages import catalog_loading_message
    assert catalog_loading_message({'_provider':'torrent'},'dub') == 'Fetching torrent file list…'
    assert catalog_loading_message({'_provider':'anipm'},'dub') == 'Loading DUB episodes…'
