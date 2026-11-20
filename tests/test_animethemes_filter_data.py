import pytest
from media_cli.app.animethemes_filter_data import search_filter_data
from media_cli.ui.smart_filter import compile_filter, filter_value_options
from types import SimpleNamespace


@pytest.mark.parametrize(('tab','raw','query'), [
    ('anime', dict(name='Example', media_format='TV', year=2023, season='SPRING', animethemes=[{},{}]), 'year:>=2020 season:spring type:tv themes:2'),
    ('animethemes', dict(type='OP', song=dict(title='Idol', artists=[dict(name='YOASOBI')]), anime=dict(name='Oshi no Ko', year=2023, season='SPRING')), 'title:idol artist:yoasobi anime:"oshi no ko" type:op year:2023'),
    ('artists', dict(name='Artist', groups=[dict(name='Group Name')]), 'group:"Group Name"'),
    ('series', dict(name='Oshi no Ko'), 'title:oshi'),
    ('studios', dict(name='Doga Kobo'), 'title:doga'),
    ('playlists', dict(name='Favourites', user=dict(name='Someone'), tracksCount=12), 'owner:someone tracks:>10'),
])
def test_loaded_tab_fields_match(tab, raw, query):
    data = search_filter_data(tab, [dict(label='Example', raw=raw)])
    plan = compile_filter(query, data['filter_schema'])
    assert not plan.error
    assert plan.matches('Example', dict(data['filter_values'][0]))


def test_missing_metadata_is_unknown_and_tabs_have_only_relevant_fields():
    data = search_filter_data('anime', [dict(label='Example', raw=dict(name='Example'))])
    assert not compile_filter('year:2023', data['filter_schema']).matches('Example', dict(data['filter_values'][0]))
    assert {f.name for f in search_filter_data('studios', [])['filter_schema']} == {'title'}


def test_artist_suggestions_use_returned_names():
    data = search_filter_data('animethemes', [dict(raw=dict(song=dict(title='Song', artists=[dict(name='Artist Name')])) )])
    options = filter_value_options([SimpleNamespace(filter_values=data['filter_values'][0])], data['filter_schema'])
    assert options['artist'] == ('Artist Name',)


def test_all_collection_song_menus_and_download_selection_share_filters():
    from test_animethemes_songs import anime_fixture
    from media_cli.services.animethemes import anime_song_rows
    from media_cli.app.animethemes_song_menu import SongMenuController
    from media_cli.ui.view_model import visible_items
    songs = anime_song_rows(anime_fixture())
    for collection in ('anime', 'series', 'studio', 'playlist', 'theme'):
        menu = SongMenuController(songs, lambda *args: None, collection_id=collection)
        for view in (menu.songs_view(), menu.download_selection_view()):
            assert {field.name for field in view.filter_schema} == {'title','artist','anime','type','year','season'}
            matched = visible_items(view, 'type:ed')
            assert len(matched) == 1
            assert matched[0].reference == 1
            assert dict(matched[0].filter_values)['type'] == 'ED'


def test_artist_song_view_filters_owner_and_song_title():
    from test_animethemes_artist import artist_fixture
    from media_cli.services.animethemes import artist_theme_rows
    from media_cli.app.animethemes_artist_controller import ArtistController
    from media_cli.ui.view_model import visible_items
    artist = artist_fixture()
    controller = ArtistController(artist, lambda *args: None)
    section = dict(id='direct', label='Songs', rows=artist_theme_rows(artist))
    view = controller.songs_view(section)
    assert len(visible_items(view, 'artist:"Miku Itou" title:song type:ed')) == 1
    assert not visible_items(view, 'type:op')


def test_terminal_nested_song_smart_filter_selects_matching_song(tmp_path):
    import json
    from test_ui_view_terminal import Terminal
    from test_animethemes_songs import anime_fixture
    from media_cli.services.animethemes import anime_song_rows
    payload = json.dumps(anime_song_rows(anime_fixture()))
    script = f'''
from media_cli.app.animethemes_song_menu import SongMenuController
from media_cli.ui.view_session import ViewSession, ExitSession
menu = SongMenuController(json.loads({payload!r}), lambda *args: None, collection_id='anime:10')
view = menu.songs_view()
def select(event):
    if event.kind == 'select':
        return ExitSession(event.item_id)
session = ViewSession(view, select)
result = run_view_session(flags, ui, session, poster_manager=manager)
finish(result)
'''
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b'2 results', b'/type:ed\r')
        assert terminal.result() == 'song:1'
