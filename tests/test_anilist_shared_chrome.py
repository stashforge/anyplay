import pytest
from media_cli.app.anilist import _anilist_chrome
from media_cli.core.terminal import strip_ansi
from media_cli.ui.legacy_list_picker import legacy_list_frame
from media_cli.ui.search_home import SearchHomeState


def test_anilist_library_projection_has_provider_tabs_and_five_slots():
    show = dict(_id='one', name='Example', englishName='Alternative', status='FINISHED',
                genres=['Drama'], availableEpisodes={'sub':12})
    chrome = _anilist_chrome('AniList · Watching', shows=[show], tabs=(('recent','Recent'),('title','Title')), active='recent')
    frame, _, _ = legacy_list_frame('AniList', ['Example'], [0], 0, width=90,height=35,
        header='\n\n\nEnter=select • Esc=back', chrome=chrome)
    lines = [strip_ansi(line) for line in frame.lines]
    assert lines[0].endswith('AniList')
    assert 'Watching' in lines[0]
    assert 'Recent' in lines[2] and 'Title' in lines[2]
    assert 'Drama' in lines
    assert 'AniList' in lines


def test_anilist_home_menu_keeps_search_editing_and_routes_lists():
    state = SearchHomeState(menu=(('lists','Lists','Browse AniList lists'),))
    state.key('TAB')
    assert state.key('ENTER') == 'lists'
    state.key('TAB')
    state.key('x')
    assert state.key('ENTER') == 'search'


def test_anilist_loading_frame_uses_shared_heading_and_footer():
    from media_cli.ui.anilist_menu import loading_frame
    value = strip_ansi(loading_frame('CURRENT',35,90,'Loading Watching list…'))
    assert 'Anyplay · AniList' in value
    assert 'Watching' in value and 'Planning' in value
    assert 'Loading Watching list…' in value
    assert 'Select action' not in value


def test_root_menu_opens_watching_without_navigation_menu(monkeypatch):
    from types import SimpleNamespace
    from media_cli.app import anilist
    monkeypatch.setattr(anilist, 'tui_pick', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('old menu opened')))
    ui = SimpleNamespace()
    assert anilist.handle_anilist_menu_state(None, ui, None, {'anilist_token':'test'}, SimpleNamespace(anilist='menu'), 'sub', None) == 'ANILIST_BROWSE'
    assert ui.anilist_browse_status == 'CURRENT'


def test_anilist_search_home_can_return_to_lists(monkeypatch):
    from types import SimpleNamespace
    from media_cli.app import anilist
    monkeypatch.setattr('media_cli.app.search.prompt_search_home', lambda *a: -9)
    assert anilist.handle_anilist_search_state(SimpleNamespace(plain_mode=False,incognito_mode=True),
        SimpleNamespace(), SimpleNamespace(query_str='', anilist_search_parent='QUIT'), {}, SimpleNamespace(), 'sub', None) == 'ANILIST_MENU'


def test_library_tab_changes_category_even_when_list_is_empty(monkeypatch):
    from types import SimpleNamespace
    from media_cli import app_core
    from media_cli.app import anilist
    from media_cli.context import UiState, MachineState, CliFlags
    monkeypatch.setattr(app_core,'load_anilist_browse',lambda *a: [])
    monkeypatch.setattr(app_core,'load_history',lambda: [])
    ui = UiState(anilist_browse_status='CURRENT')
    states = []
    def picker(*args, chrome_fn, tab_fn, state, **kwargs):
        chrome = chrome_fn()
        assert chrome['tabs'].active_id == ui.anilist_browse_status
        assert chrome['title'] == 'AniList'
        states.append(state)
        tab_fn(direction=1)
        return -6
    monkeypatch.setattr(anilist,'tui_pick',picker)
    args = SimpleNamespace(provider='senshi')
    for _ in range(2):
        assert anilist.handle_anilist_browse_state(CliFlags(),ui,MachineState(),{'anilist_token':'test'},args,'sub',None) == 'ANILIST_BROWSE'
    assert ui.anilist_browse_status == 'COMPLETED'
    assert states[0] is not states[1]


def test_library_search_shortcut_does_not_change_streaming_choice(monkeypatch):
    from types import SimpleNamespace
    from media_cli import app_core
    from media_cli.app import anilist
    from media_cli.context import UiState, MachineState, CliFlags
    monkeypatch.setattr(app_core,'load_anilist_browse',lambda *a: [])
    monkeypatch.setattr(app_core,'load_history',lambda: [])
    def picker(*args,key_fn,**kwargs):
        assert key_fn('s',-1)
        return -6
    monkeypatch.setattr(anilist,'tui_pick',picker)
    args = SimpleNamespace(provider='senshi')
    ms = MachineState(query_str='old')
    assert anilist.handle_anilist_browse_state(CliFlags(),UiState(anilist_browse_status='CURRENT'),ms,
        {'anilist_token':'test'},args,'sub',None) == 'ANILIST_SEARCH'
    assert args.provider == 'senshi'
    assert ms.query_str == ''


def test_anilist_library_without_login_returns_to_search_with_hint():
    from types import SimpleNamespace
    from media_cli.app import anilist
    ui = SimpleNamespace()
    args = SimpleNamespace(anilist='menu')
    assert anilist.handle_anilist_menu_state(None,ui,None,{},args,'sub',None) == 'SEARCH'
    assert args.provider == 'anilist'
    assert 'Link AniList' in ui.search_error


def test_real_terminal_library_tab_changes_list_without_old_menu(tmp_path):
    from test_ui_view_terminal import Terminal
    script = '''
from media_cli import app_core
from media_cli.app.anilist import handle_anilist_browse_state
from media_cli.context import MachineState
from media_cli.app.terminal_controller import run_screen_controller
app_core.load_anilist_browse=lambda *a: []
app_core.load_history=lambda: []
ui.anilist_browse_status='CURRENT'
result=run_screen_controller(handle_anilist_browse_state,flags,ui,MachineState(),
    {'anilist_token':'test'},SimpleNamespace(provider='senshi'),'sub',None,fallback='ANILIST_BROWSE')
finish(dict(state=result,status=ui.anilist_browse_status))
'''
    with Terminal(tmp_path,script) as terminal:
        terminal.send_after(b'Watching',b'\t')
        assert terminal.result() == {'state':'ANILIST_BROWSE','status':'PLANNING'}


def test_saved_tracker_search_uses_configured_stream_provider(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from media_cli import app_core
    from media_cli.app import anilist
    # No matching provider exists: the lookup argument still must be streaming,
    # never the AniList search choice itself.
    monkeypatch.setattr(app_core,'get_al_match',lambda *a:{})
    monkeypatch.setattr(app_core,'provider_display_name',lambda value:value or 'Default')
    monkeypatch.setattr(app_core,'with_loading',lambda msg,fn,*a,**kw:fn(*a,**kw))
    lookup=Mock(return_value=None)
    monkeypatch.setattr(app_core,'match_anilist_show_to_provider',lookup)
    monkeypatch.setattr(app_core,'prepare_show_display_state',lambda *a,**kw:None)
    monkeypatch.setattr(app_core,'get_show_anilist_id',lambda *a:None)
    # Existing open-flow tests verify the remaining unlinked Details behavior;
    # stop immediately after recording the resolver argument here.
    def stop(*a,**kw):
        assert kw['provider_id']=='senshi'
        raise LookupError('verified argument')
    lookup.side_effect=stop
    import pytest
    with pytest.raises(LookupError,match='verified argument'):
        anilist._open_anilist_show_from_picker(None,None,None,
            SimpleNamespace(provider='anilist',_anilist_stream_provider='senshi'),
            'sub',{'_id':'123'},'ANILIST_BROWSE',{'provider':'anipm'})


def test_cached_library_lists_do_not_enter_loading_wrapper(monkeypatch):
    from media_cli.core import anilist
    from media_cli.core.api import anilist_account_cache_key
    monkeypatch.setattr(anilist, '_anilist_list_cache', {})
    monkeypatch.setattr('media_cli.ui.display.with_anilist_menu_loading',
        lambda *a,**kw: (_ for _ in ()).throw(AssertionError('cached list entered loading')))
    for status, shows in [('COMPLETED', [{'name':'Example'}]), (None, []), ('PAUSED', [])]:
        anilist._anilist_list_cache[(anilist_account_cache_key('test'),str(status or 'ALL').upper())] = shows
        assert anilist.load_anilist_browse('test', status) is shows


def test_list_cache_miss_after_invalidation_and_account_isolation(monkeypatch):
    from media_cli.core import anilist
    from media_cli.core.api import anilist_account_cache_key
    monkeypatch.setattr(anilist, '_anilist_list_cache', {
        (anilist_account_cache_key('first'), 'COMPLETED'): []})
    calls=[]
    def load(status,message,fetch,*args):
        calls.append((status,args))
        return ['fetched']
    monkeypatch.setattr('media_cli.ui.display.with_anilist_menu_loading',load)
    assert anilist.load_anilist_browse('second','COMPLETED') == ['fetched']
    anilist._anilist_list_cache.clear()
    assert anilist.load_anilist_browse('first','COMPLETED') == ['fetched']
    assert len(calls)==2


def test_search_home_airing_remembers_search_parent(monkeypatch):
    from types import SimpleNamespace
    from media_cli.app import anilist
    from media_cli.context import MachineState
    monkeypatch.setattr('media_cli.app.search.prompt_search_home', lambda *a: -10)
    ms = MachineState()
    assert anilist.handle_anilist_search_state(
        SimpleNamespace(plain_mode=False, incognito_mode=True), SimpleNamespace(),
        ms, {}, SimpleNamespace(), 'sub', None) == 'ANILIST_AIRING'
    assert ms.anilist_airing_parent == 'ANILIST_SEARCH'


def test_airing_escape_returns_to_its_opener(monkeypatch):
    from types import SimpleNamespace
    from media_cli.app import anilist
    from media_cli import app_core
    from media_cli.context import MachineState, UiState
    monkeypatch.setattr(anilist, '_load_anilist_airing_shows', lambda *a, **kw: [])
    monkeypatch.setattr(anilist, 'tui_pick', lambda *a, **kw: -2)
    monkeypatch.setattr(app_core, '_clear_poster_downloads', lambda: None)
    monkeypatch.setattr(app_core, 'clear_terminal_images', lambda: None)
    for parent in ('ANILIST_SEARCH', 'ANILIST_BROWSE', 'ANILIST_MENU', 'SEARCH'):
        ms = MachineState(anilist_airing_parent=parent)
        assert anilist.handle_anilist_airing_state(
            SimpleNamespace(), UiState(), ms, {'anilist_token': 'test'},
            SimpleNamespace(), 'sub', None) == parent


def test_anilist_loading_cancel_leaves_request_state():
    from media_cli.app.terminal_controller import preparation_parent
    from media_cli.context import UiState, MachineState
    ui, ms = UiState(), MachineState()
    for state in ('ANILIST_MENU', 'ANILIST_BROWSE', 'ANILIST_SEARCH'):
        assert preparation_parent(state, ui, ms) == 'SEARCH'
    for parent in ('ANILIST_SEARCH', 'ANILIST_BROWSE', 'SEARCH', 'ANILIST_MENU'):
        ms.anilist_airing_parent = parent
        assert preparation_parent('ANILIST_AIRING', ui, ms) == parent


def test_anilist_filter_snapshot_includes_progress_list_and_normalized_season():
    from media_cli.ui.smart_filter import compile_filter
    show = dict(name='Example', genres=['Martial Arts'], airedStart={'year': 2020},
                season={'year': 2020, 'quarter': 'Spring'}, status='RELEASING', type='TV',
                _anilist_list='CURRENT', _anilist_progress=3)
    chrome = _anilist_chrome('AniList', shows=[show], active='ALL')
    values = dict(chrome['filter_values'][0])
    plan = compile_filter('genre:"Martial Arts" year:>=2008 season:spring list:watching progress:>0', chrome['filter_schema'])
    assert plan.matches('Example', values)
    assert not compile_filter('list:completed', chrome['filter_schema']).matches('Example', values)
    assert values['season'] == 'Spring'


def test_real_terminal_anilist_smart_filter_and_completion(tmp_path):
    from test_ui_view_terminal import Terminal
    script = """
from media_cli.app.anilist import _anilist_chrome
from media_cli.ui.picker import tui_pick
shows = [dict(name='Magic title', genres=['Magic'], _anilist_list='CURRENT', _anilist_progress=2),
         dict(name='Martial title', genres=['Martial Arts'], _anilist_list='PLANNING', _anilist_progress=0)]
result = tui_pick(flags, ui, 'AniList', [show['name'] for show in shows],
    chrome_fn=lambda: _anilist_chrome('AniList', shows=shows),
    help_dict={'?': 'Help'}, reverse_items=False)
finish(result)
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b'Martial title', b'/genre:mart')
        terminal.send_after(b'ial Arts', b'\x1b[C')
        terminal.send_after(b'genre:"Martial Arts"', b'\r')
        assert terminal.result() == 1


def test_large_anilist_does_not_project_all_metadata_for_each_visible_row(tmp_path):
    from test_ui_view_terminal import Terminal
    script = """
from media_cli.app.anilist import _anilist_chrome
from media_cli.ui.picker import tui_pick
shows = [dict(name='Title %s' % i, genres=['Magic'], airedStart={'year': 2020}) for i in range(604)]
calls = 0
def chrome():
    global calls
    calls += 1
    return _anilist_chrome('AniList', shows=shows)
result = tui_pick(flags, ui, 'AniList', [show['name'] for show in shows],
                  chrome_fn=chrome, reverse_items=False)
finish({'selected': result, 'projections': calls})
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b'Title 0', b'\r')
        result = terminal.result()
        assert result['selected'] == 0
        assert result['projections'] < 12


def test_large_anilist_held_backspace_clears_filter_and_keeps_selection_valid(tmp_path):
    from test_ui_view_terminal import Terminal
    script = """
from media_cli.app.anilist import _anilist_chrome
from media_cli.ui.picker import tui_pick
shows = [dict(name='Title %s' % i, genres=['Magic']) for i in range(604)]
result = tui_pick(flags, ui, 'AniList', [show['name'] for show in shows],
                  chrome_fn=lambda: _anilist_chrome('AniList', shows=shows), reverse_items=False)
finish(result)
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b'Title 0', b'/genre:magic')
        terminal.send_after(b'genre:magic', b'\x7f' * len('genre:magic'))
        terminal.send_after(b'title:  genre:', b'\r')
        assert terminal.result() == 0


def test_list_field_only_on_all_tab():
    for active in ('CURRENT', 'PLANNING', None):
        assert 'list' not in [field.name for field in _anilist_chrome('AniList', shows=[], active=active)['filter_schema']]
    assert 'list' in [field.name for field in _anilist_chrome('AniList', shows=[], active='ALL')['filter_schema']]


@pytest.mark.parametrize(('key', 'expected'), [(b'\t', 'PLANNING'), (b'\x1b[Z', None)])
def test_tab_can_leave_pending_anilist_fetch_without_waiting(tmp_path, key, expected):
    from test_ui_view_terminal import Terminal
    script = """
from media_cli import app_core
from media_cli.app.anilist import handle_anilist_browse_state
from media_cli.app.terminal_controller import run_screen_controller
from media_cli.context import MachineState
from media_cli.ui.display import with_anilist_menu_loading
ui.anilist_browse_status = 'CURRENT'
finished = threading.Event()
def fetch():
    time.sleep(5)
    finished.set()
    return []
app_core.load_anilist_browse = lambda token, status: with_anilist_menu_loading(status, 'Loading pending list', fetch)
result = run_screen_controller(handle_anilist_browse_state, flags, ui, MachineState(),
    {'anilist_token': 'test'}, SimpleNamespace(provider='anipm'), 'sub', None, fallback='SEARCH')
finish({'state': result, 'tab': ui.anilist_browse_status, 'fetch_finished': finished.is_set()})
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b'Loading pending list', key)
        assert terminal.result() == {'state': 'ANILIST_BROWSE', 'tab': expected, 'fetch_finished': False}


def test_held_backspace_paints_intermediate_queries_with_input_queued(tmp_path):
    from test_ui_view_terminal import Terminal
    script = """
from media_cli.app.anilist import _anilist_chrome
from media_cli.ui.picker import tui_pick
from media_cli.ui import legacy_list_picker
lengths = []
original = legacy_list_picker.legacy_list_frame
def recording(*a, **kw):
    lengths.append(len(kw.get('query', '')))
    return original(*a, **kw)
legacy_list_picker.legacy_list_frame = recording
shows = [dict(name='Title %s' % i, genres=['Magic']) for i in range(604)]
result = tui_pick(flags, ui, 'AniList', [show['name'] for show in shows], initial_query='z'*100,
    chrome_fn=lambda: _anilist_chrome('AniList', shows=shows), reverse_items=False, keep_empty=True)
finish({'selected': result, 'lengths': lengths})
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b'Anyplay', b'\x7f' * 100)
        terminal.send_after(b'title:  genre:', b'\r')
        result = terminal.result()
        assert result['selected'] == 0
        assert any(0 < length < 100 for length in result['lengths'])
        assert 0 in result['lengths']
