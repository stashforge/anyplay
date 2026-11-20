import pytest
from media_cli.ui.smart_filter import FilterField, TORRENT_FIELDS, compile_filter, number
from media_cli.app.search_results_view import build_search_results_view
from media_cli.ui.view_model import visible_items


@pytest.mark.parametrize('query,expected', [
    ('oshi no ko', True), ('bleach', False),
    ('title:"oshi no ko" seeds:>20 size:<8GB res:1080p', True),
    ('title:"oshi no ko" AND seeds:>=25 and leechers:<=3', True),
    ('seeds:>25', False), ('seeds:25', True), ('leechers:3', True),
    ('size:<=4GB', True), ('size:<4GB', False), ('res:>=720p', True),
    ('trusted:true', False), ('trusted:false', True), ('source:nyaa', True),
    ('audio:ja subs:en', True), ('title:nyaa', False),
    ('codec:hevc', True), ('source:unknown', False),
])
def test_combined_torrent_filters(query, expected):
    values = dict(title=('Oshi no Ko',), seeds=25, leechers=3, size=4_000_000_000,
                  res='1080p', source=('knaben', 'nyaa'), trusted=False,
                  audio=('ja',), subs=('en',), codec=('hevc', 'x265'))
    plan = compile_filter(query, TORRENT_FIELDS)
    assert not plan.error
    assert plan.matches('Oshi no Ko', values) is expected


@pytest.mark.parametrize('query', ['seeds:>oops', 'unknown:foo', 'size:-4GB',
                                  'trusted:perhaps', 'title:"unclosed',
                                  'source:>nyaa', 'res:NaN', 'size:12wat'])
def test_invalid_filters_are_explicit_and_never_match(query):
    plan = compile_filter(query, TORRENT_FIELDS)
    assert plan.error
    assert not plan.matches('anything', {})


def test_missing_values_do_not_match_even_zero_or_false():
    for query in ('seeds:0', 'trusted:false', 'size:>0B', 'audio:en'):
        assert not compile_filter(query, TORRENT_FIELDS).matches('Name', {})
    assert compile_filter('seeds:0', TORRENT_FIELDS).matches('Name', {'seeds': 0})


def test_size_units_are_decimal_unless_binary_unit_is_explicit():
    assert number('1GB', 'size') == 1_000_000_000
    assert number('1GiB', 'size') == 1024 ** 3
    assert number('4k', 'resolution') == 2160


def test_shared_engine_supports_future_provider_metadata():
    schema = (FilterField('year', 'number'), FilterField('genre', aliases=('genres',)))
    plan = compile_filter('year:>=2008 and genre:magic', schema)
    assert plan.matches('', {'year': 2010, 'genre': ('Magic', 'Fantasy')})
    assert not plan.matches('', {'year': 2007, 'genre': ('Magic',)})
    assert not plan.matches('', {'year': 2010, 'genre': ('Romance',)})


def test_only_search_terms_are_used_for_highlighting():
    assert compile_filter('oshi title:"no ko" seeds:>5', TORRENT_FIELDS).highlight == 'oshi no ko'
    assert compile_filter('rock and roll', TORRENT_FIELDS).text == 'rock and roll'


def test_torrent_search_projection_wires_shared_filter_without_io():
    show = dict(_id='hash', name='[Group] Oshi no Ko 1080p HEVC',
                _torrent=dict(size_bytes=2_000_000_000, seeders=30, leechers=1,
                              trusted=None, source='nyaa', audio_langs=['ja']))
    view = build_search_results_view([show], provider_id='torrent', provider_name='Torrents',
                                    query='oshi', ttype='sub', prompt='Select torrent')
    assert len(visible_items(view, 'title:oshi seeds:>20 size:<3GB codec:x265')) == 1
    assert not visible_items(view, 'trusted:false')
    assert not visible_items(view, 'provider:anipm')
    assert any(key == 'Fields' for key, _ in view.help)
    plain = build_search_results_view([dict(_id='one', name='Example')], provider_id='anipm',
                                     provider_name='AniPM', query='', ttype='sub', prompt='Select')
    assert plain.filter_schema
    assert len(visible_items(plain, 'Exm')) == 1


def test_invalid_filter_uses_one_line_footer_in_custom_picker(monkeypatch):
    from types import SimpleNamespace
    from media_cli.ui import action_picker as module
    from media_cli.ui.picker_state import PickerState
    from media_cli.core.terminal import strip_ansi
    plan = compile_filter('seeds:bad', TORRENT_FIELDS)
    def run(render, on_key, **kwargs):
        frame = render(80, 24, 0)
        text = '\n'.join(strip_ansi(line) for line in frame.lines)
        assert plan.error in text
        assert text.count(plan.error) == 1
        assert on_key('ENTER') is None
        return -1
    monkeypatch.setattr(module, 'run_framed_screen', run)
    result = module.action_picker(SimpleNamespace(), SimpleNamespace(), 'Select', ['Example'],
        presentation=dict(screen_title='Results', layout='top', info_fn=lambda *a: (),
                          status_fn=lambda *a: '', shortcuts_fn=lambda _: (('esc', 'back'),)),
        state=PickerState(query='seeds:bad'), header_fn=lambda _: '',
        filter_fn=lambda *a: [], filter_error_fn=lambda _: plan.error)
    assert result == -1


@pytest.mark.parametrize('unfinished', ['size:', 'size:<', 'size:<=', 'size:>', 'size:8', 'size:<8', 'size:<8G', 'size:<8Gi', 'size:8.', 'seeds:', 'title:'])
def test_unfinished_field_keeps_other_completed_conditions(unfinished):
    plan = compile_filter('seeds:>20 ' + unfinished, TORRENT_FIELDS)
    assert not plan.error
    assert plan.matches('Example', {'seeds': 30})
    assert not plan.matches('Example', {'seeds': 10})
    assert plan.highlight == ''


def test_bare_reserved_word_can_still_be_searched_as_title_value():
    assert compile_filter('title:size', TORRENT_FIELDS).matches('Size', {'title': ('Size',)})
    assert compile_filter('size:', TORRENT_FIELDS).matches('anything', {})
    assert not compile_filter('size:<8GB', TORRENT_FIELDS).matches('anything', {'size': 9_000_000_000})
    assert compile_filter('size: seeds:>1', TORRENT_FIELDS).error


def test_empty_filter_renders_schema_fields_as_placeholder(monkeypatch):
    from types import SimpleNamespace
    from media_cli.ui import action_picker as module
    from media_cli.ui.picker_state import PickerState
    from media_cli.core.terminal import strip_ansi
    from media_cli.ui.smart_filter import filter_placeholder
    placeholder = filter_placeholder(TORRENT_FIELDS)
    def run(render, on_key, **kwargs):
        assert on_key('/') is None
        frame = render(120, 30, 0)
        text = '\n'.join(strip_ansi(line) for line in frame.lines)
        assert placeholder in text
        return -1
    monkeypatch.setattr(module, 'run_framed_screen', run)
    module.action_picker(SimpleNamespace(), SimpleNamespace(), 'Select', ['Example'],
        presentation=dict(screen_title='Results', layout='top', info_fn=lambda *a: (),
                          status_fn=lambda *a: '', shortcuts_fn=lambda _: (('esc', 'back'),)),
        state=PickerState(), header_fn=lambda _: '', filter_placeholder=placeholder)


def test_size_requires_unit_before_finishing_clause():
    assert compile_filter('size:8 seeds:>1', TORRENT_FIELDS).error
    assert compile_filter('size:8 ', TORRENT_FIELDS).error
    assert compile_filter('size:8B', TORRENT_FIELDS).matches('', {'size': 8})


def test_filter_error_never_hides_editor(monkeypatch):
    from types import SimpleNamespace
    from media_cli.ui import action_picker as module
    from media_cli.ui.picker_state import PickerState
    from media_cli.core.terminal import strip_ansi
    def run(render, on_key, **kwargs):
        footer = strip_ansi(render(100, 24, 0).lines[-1])
        assert '/size:bad' in footer
        raw = render(100, 24, 0).lines[-1]
        from media_cli.ui.screen_chrome import role_prefix
        from media_cli.state.theme import MINT_SCREEN_THEME
        assert role_prefix(MINT_SCREEN_THEME, 'muted') in raw
        assert '\033[7m \033[27m' in raw
        assert 'size:800MB' in footer
        assert 'Use a size' not in footer
        return -1
    monkeypatch.setattr(module, 'run_framed_screen', run)
    module.action_picker(SimpleNamespace(), SimpleNamespace(), 'Select', ['Example'],
        presentation=dict(screen_title='Results', layout='top', info_fn=lambda *a: (),
                          status_fn=lambda *a: '', status_priority=True, shortcuts_fn=lambda _: ()),
        state=PickerState(query='size:bad', query_cursor=8), header_fn=lambda _: '',
        filter_error_fn=lambda _: compile_filter('size:bad', TORRENT_FIELDS).error,
        filter_hint_fn=lambda _: 'size:800MB  size:<800MB  size:>1GB')


def test_bare_field_name_is_normal_text_until_colon():
    plan = compile_filter('size', TORRENT_FIELDS)
    assert plan.text == 'size'
    assert plan.matches('Size matters', {})
    assert not plan.matches('Example', {})


def test_field_examples_follow_active_field():
    from media_cli.ui.smart_filter import filter_hint
    assert filter_hint('size', TORRENT_FIELDS) == ''
    assert filter_hint('size:<8G', TORRENT_FIELDS) == 'size:800MB  size:<800MB  size:>1GB'
    assert filter_hint('size:<8GB seeds:', TORRENT_FIELDS) == 'seeds:20  seeds:>20  seeds:<100'
    assert filter_hint('size:<8GB ', TORRENT_FIELDS) == ''


def test_unknown_field_help_hint_exits_editor_before_help():
    assert compile_filter('s:', TORRENT_FIELDS).error == "Unknown filter 's'; Esc, then ? for fields"


def test_field_completion_requires_unique_prefix():
    from media_cli.ui.smart_filter import field_completion, filter_hint
    assert field_completion('s', TORRENT_FIELDS) == ''
    assert 'size:' in filter_hint('s', TORRENT_FIELDS)
    assert 'seeders:' in filter_hint('s', TORRENT_FIELDS)
    assert field_completion('si', TORRENT_FIELDS) == 'ze:'
    assert field_completion('size:', TORRENT_FIELDS) == ''


def test_right_completion_spaces_and_blink_in_shared_editor(monkeypatch):
    from types import SimpleNamespace
    from media_cli.ui import action_picker as module
    from media_cli.ui.picker_state import PickerState
    from media_cli.ui.smart_filter import field_completion
    saved = PickerState(query='si')
    def run(render, on_key, **kwargs):
        assert 'ze:' in render(100, 24, 0).lines[-1].replace('\033[7m', '').replace('\033[27m', '')
        assert on_key('RIGHT') is None
        on_key('8'); on_key('G'); on_key('B'); on_key(' '); on_key(' ')
        raw = render(100, 24, 0).lines[-1]
        assert 'size:8GB  \033[7m \033[27m' in raw
        assert '\033[7m' not in render(100, 24, 1.3).lines[-1]
        return -1
    monkeypatch.setattr(module, 'run_framed_screen', run)
    module.action_picker(SimpleNamespace(), SimpleNamespace(), 'Select', ['Example'],
        presentation=dict(screen_title='Results', layout='top', info_fn=lambda *a: (),
                          status_fn=lambda *a: '', shortcuts_fn=lambda _: ()),
        state=saved, header_fn=lambda _: '',
        filter_completion_fn=lambda query: field_completion(query, TORRENT_FIELDS))
    assert saved.query == 'size:8GB  '


def test_unique_keyword_hint_is_not_duplicated():
    from media_cli.ui.smart_filter import filter_hint
    assert filter_hint('si', TORRENT_FIELDS) == ''
    assert filter_hint('title:s03', TORRENT_FIELDS) == ''
    assert filter_hint('title:', TORRENT_FIELDS) == 'title:"search text"'


def test_categorical_hints_use_loaded_values_and_current_prefix():
    from media_cli.ui.smart_filter import filter_hint
    options = {'source': ('knaben', 'nyaa', 'yts')}
    assert filter_hint('source:', TORRENT_FIELDS, options) == 'source:knaben,nyaa,yts'
    assert filter_hint('source:n', TORRENT_FIELDS, options) == ''
    assert filter_hint('source:nyaa,k', TORRENT_FIELDS, options) == ''
    assert filter_hint('source:', TORRENT_FIELDS) == ''


def test_comma_choices_are_or_while_conditions_are_and():
    plan = compile_filter('source:knaben,nyaa res:1080p', TORRENT_FIELDS)
    assert plan.matches('', {'source': ('nyaa',), 'res': '1080p'})
    assert plan.matches('', {'source': ('knaben', 'yts'), 'res': '1080p'})
    assert not plan.matches('', {'source': ('yts',), 'res': '1080p'})
    assert not plan.matches('', {'source': ('nyaa',), 'res': '720p'})
    assert compile_filter('source:nyaa,', TORRENT_FIELDS).matches('', {'source': ('nyaa',)})


def test_value_options_come_from_returned_metadata():
    from types import SimpleNamespace
    from media_cli.ui.smart_filter import filter_value_options
    items = [SimpleNamespace(filter_values=(('source', ('nyaa', 'knaben')), ('title', ('Ignore',)))),
             SimpleNamespace(filter_values=(('source', ('nyaa',)), ('audio', None)))]
    values = filter_value_options(items, TORRENT_FIELDS)
    assert values['source'] == ('knaben', 'nyaa')
    assert values['audio'] == ()
    assert 'title' not in values


def test_unique_value_completion_uses_shared_field_aliases_and_comma_segment():
    from media_cli.app.title_filter_data import HISTORY_FIELDS
    from media_cli.ui.smart_filter import field_completion, filter_hint
    options = {'genre': ('Magic', 'Martial Arts', 'Mystery'), 'source': ('knaben', 'nyaa')}
    assert field_completion('genre:magi', HISTORY_FIELDS, options) == 'c'
    assert field_completion('genres:magi', HISTORY_FIELDS, options) == 'c'
    assert filter_hint('genre:magi', HISTORY_FIELDS, options) == ''
    assert field_completion('genre:Magic,mys', HISTORY_FIELDS, options) == 'tery'
    assert field_completion('genre:ma', HISTORY_FIELDS, options) == ''
    assert filter_hint('genre:ma', HISTORY_FIELDS, options) == 'genre:Magic,Martial Arts'
    assert field_completion('genre:', HISTORY_FIELDS, options) == ''
    assert field_completion('source:ny', TORRENT_FIELDS, options) == 'aa'
    assert field_completion('genre:mart', HISTORY_FIELDS, options) == 'ial Arts'


def test_multiword_values_complete_with_quotes_and_parse():
    from media_cli.app.title_filter_data import HISTORY_FIELDS
    from media_cli.ui.smart_filter import field_completion, accept_field_completion, filter_hint
    options = {'genre': ('Magic', 'Martial Arts')}
    for query in ('genre:mart', 'genre:"Martial A', 'genres:Magic,mar'):
        completed = accept_field_completion(query, HISTORY_FIELDS, options)
        assert completed.endswith('"Martial Arts"')
        assert field_completion(query, HISTORY_FIELDS, options)
        assert filter_hint(query, HISTORY_FIELDS, options) == ''
        plan = compile_filter(completed, HISTORY_FIELDS)
        assert not plan.error
        assert plan.matches('', {'genre': ('Martial Arts',)})


def test_provider_search_filters_use_loaded_metadata():
    show = dict(_id='one', name='Example', englishName='Other Title', genres=['Martial Arts'],
                status='RELEASING', type='ONA', airedStart={'year': 2022}, season='SUMMER')
    view = build_search_results_view([show], provider_id='anipm', provider_name='AniPM',
                                    query='example', ttype='sub', prompt='Select')
    assert len(visible_items(view, 'title:"Other Title" genre:"Martial Arts" year:>=2020 type:ona status:airing season:summer')) == 1
    assert not visible_items(view, 'genre:magic')
    assert not visible_items(view, 'provider:anipm')


def test_categorical_options_deduplicate_case_only_aliases():
    from types import SimpleNamespace
    from media_cli.ui.smart_filter import filter_value_options
    from media_cli.app.title_filter_data import HISTORY_FIELDS
    items = [SimpleNamespace(filter_values=(('provider', ('animexin', 'AnimeXin')),)),
             SimpleNamespace(filter_values=(('provider', ('ANIMEXIN',)),))]
    assert filter_value_options(items, HISTORY_FIELDS)['provider'] == ('animexin',)
