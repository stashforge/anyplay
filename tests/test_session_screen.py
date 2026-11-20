"""Default structured menus share chrome without replacing their event contracts."""
from test_ui_view_terminal import Terminal
import pytest


def test_native_cover_stays_drawn_across_internal_translation_rebuild(tmp_path):
    script = '''
from media_cli.ui.screen_chrome import run_framed_screen
from media_cli.ui.list_screen import ScreenFrame
memory = {}
packet = '\\033_GFAKE_COVER_DATA\\033\\\\'
def render(label):
    def frame(width, height, elapsed):
        lines = [''] * height
        lines[0] = label
        return ScreenFrame(lines, packet, 10, 0)
    return frame
first = run_framed_screen(render('Sub choices'), lambda key: -6, frame_memory=memory)
second = run_framed_screen(render('Dub choices'), lambda key: -1, frame_memory=memory)
finish([first, second])
'''
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b'Sub choices', b'\t')
        terminal.send_after(b'Dub choices', b'\x1b')
        assert terminal.result() == [-6, -1]
        assert terminal.output.count(b'FAKE_COVER_DATA') == 1


@pytest.mark.parametrize('layout,movement', [('top', b'\x1b[B'), ('bottom', b'\x1b[A')])
def test_layout_places_source_first_at_leading_edge_and_moves_to_second(tmp_path, layout, movement):
    script = f'''
ui.tui_layout = {layout!r}
items = tuple(ViewItem(str(i), label, details=Inherit) for i, label in enumerate(('First', 'Second', 'Third')))
view = ViewSpec('layout', 'Layout order', items, details=DetailLines(Blank, Blank, Blank))
session = ViewSession(view, lambda event: ExitSession(event.item_id) if event.kind == 'select' else None)
finish(run_view_session(flags, ui, session, poster_manager=manager))
'''
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b'Layout order', movement + b'\r')
        assert terminal.result() == '1'


@pytest.mark.parametrize('layout', ['top', 'bottom'])
def test_fresh_layout_selects_source_first_item(tmp_path, layout):
    script = f'''
ui.tui_layout = {layout!r}
items = tuple(ViewItem(str(i), label, details=Inherit) for i, label in enumerate(('First', 'Second', 'Third')))
view = ViewSpec('layout', 'Fresh layout', items, details=DetailLines(Blank, Blank, Blank))
session = ViewSession(view, lambda event: ExitSession(event.item_id) if event.kind == 'select' else None)
finish(run_view_session(flags, ui, session, poster_manager=manager))
'''
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b'Fresh layout', b'\r')
        assert terminal.result() == '0'


@pytest.mark.parametrize("key,action,original", [(b"r", "refresh_title", "CTRL_R"),
    (b"R", "refresh_view", "F5"), (b"i", "information", "CTRL_O"), (b"o", "flip", "CTRL_R")])
def test_useful_shortcuts_dispatch_existing_actions(tmp_path, key, action, original):
    script = f'''
from media_cli.ui.view_model import ViewAction
view = ViewSpec('keys', 'Keys test', (ViewItem('one', 'First', details=Inherit),),
    details=DetailLines(Blank, Blank, Blank), actions=(ViewAction('{action}', 'Do action', ('{original}',)),))
session = ViewSession(view, lambda event: ExitSession(event.value) if event.kind == 'action' else None)
finish(run_view_session(flags, ui, session, poster_manager=manager))
'''
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b'Keys test', key)
        assert terminal.result() == action


@pytest.mark.parametrize("key,identity", [(b"e", "EPISODES"), (b"m", "MIRRORS"), (b"d", "DOWNLOAD_MENU")])
def test_details_shortcuts_select_existing_menu_item(tmp_path, key, identity):
    script = '''
items = tuple(ViewItem('action:' + identity, identity, reference=identity, details=Inherit)
              for identity in ('EPISODES', 'MIRRORS', 'DOWNLOAD_MENU'))
view = ViewSpec('details', 'Details keys', items, details=DetailLines(Blank, Blank, Blank))
session = ViewSession(view, lambda event: ExitSession(event.item_id) if event.kind == 'select' else None)
finish(run_view_session(flags, ui, session, poster_manager=manager))
'''
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b'Details keys', key)
        assert terminal.result() == 'action:' + identity


def test_shortcut_letter_stays_text_while_filtering(tmp_path):
    script = '''
from media_cli.ui.view_model import ViewAction
view = ViewSpec('keys', 'Filter keys', (ViewItem('one', 'First', details=Inherit),),
    details=DetailLines(Blank, Blank, Blank), actions=(ViewAction('refresh_title', 'Refresh', ('CTRL_R',)),))
def handle(event):
    if event.kind == 'action':
        raise AssertionError('Typing in a filter must not refresh')
    if event.kind == 'select':
        return ExitSession(session.position.query)
session = ViewSession(view, handle)
finish(run_view_session(flags, ui, session, poster_manager=manager))
'''
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b'Filter keys', b'/r\r')
        assert terminal.result() == 'r'


def test_default_session_keeps_multiselect_prefixes_and_filter(tmp_path):
    script = '''
from media_cli.ui.styled_text import StyledText, TextRun
items = (ViewItem('one', 'First', prefix=StyledText((TextRun('◆ ', 'success'),)), details=Inherit),
         ViewItem('two', 'Second', details=Inherit))
view = ViewSpec('shared', 'Choose songs', items, details=DetailLines(Content('Title'), Blank, Blank),
                reverse_items=False, selection_policy=SelectionPolicy(multiple=True))
session = ViewSession(view, lambda event: ExitSession(list(event.value)) if event.kind == 'select' else None)
finish(run_view_session(flags, ui, session, poster_manager=manager))
'''
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after('◆ '.encode(), b' \x1b[B \r')
        assert terminal.result() == ['one', 'two']
        assert b'Anyplay' in terminal.output


def test_default_session_tab_switch_keeps_query_and_new_chrome(tmp_path):
    script = '''
from media_cli.ui.view_model import ViewTab
view = ViewSpec('tabs', 'AnimeThemes', (ViewItem('one', 'Song', details=Inherit),),
    details=DetailLines(Content('Song title'), Content('Alternative'), Content('Metadata')),
    tabs=(ViewTab('themes', 'Themes'), ViewTab('artists', 'Artists')), active_tab_id='themes')
def handle(event):
    if event.kind == 'tab':
        return ExitSession(dict(tab=event.value, query=session.position.query))
session = ViewSession(view, handle)
finish(run_view_session(flags, ui, session, poster_manager=manager))
'''
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b'Themes', b'/Song\t')
        assert terminal.result() == {'tab': 'artists', 'query': 'Song'}


def test_status_only_render_skips_detail_text_work(monkeypatch):
    from types import SimpleNamespace
    from media_cli.ui import view_presentation
    from media_cli.ui.view_model import Blank, DetailLines, ViewSpec
    from media_cli.ui.view_session import ViewSession
    session = ViewSession(ViewSpec("status", "Menu", details=DetailLines(Blank, Blank, Blank)))
    manager = SimpleNamespace(enabled=lambda: False, loading_frame=lambda: "x")
    presentation = view_presentation.ViewPresentation(session, manager)
    def forbidden(**kwargs):
        raise AssertionError("Footer update must not render discarded detail lines")
    monkeypatch.setattr(view_presentation, "render_detail_lines", forbidden)
    assert isinstance(presentation.details(status_only=True), str)


def test_title_details_keep_mode_genres_inline_hint_and_match_highlight(tmp_path):
    script = '''
from media_cli.app.details_menu_view import build_details_menu_view, DetailsMenuOption
show = dict(name="Example", genres=["Action", "Fantasy"])
view = build_details_menu_view(show, (
    DetailsMenuOption("other", "Much longer action label"),
    DetailsMenuOption("next", "Play Next", hint="play EP 2")),
    view_id="details:example", image_key="example", ttype="sub",
    context=("History", "Example provider"), show_cover=False)
session = ViewSession(view, lambda event: ExitSession(event.item_id) if event.kind == 'select' else None)
finish(run_view_session(flags, ui, session, poster_manager=manager))
'''
    from media_cli.core.terminal import strip_ansi
    with Terminal(tmp_path, script) as terminal:
        terminal.until('History · Details'.encode())
        terminal.send_after('Action • Fantasy'.encode(), b'/Play')
        terminal.until(b'Play\x1b[7m \x1b[27m')
        # Match colors decorate text without replacing the actual action label.
        assert b'\x1b[38;5;114mP' in terminal.output
        assert 'Play Next play EP 2' in strip_ansi(bytes(terminal.output).decode())
        assert 'No alternative title' in strip_ansi(bytes(terminal.output).decode())
        import os
        os.write(terminal.master, b'\r')
        assert terminal.result() == 'action:next'


def test_information_cache_reuses_footer_updates_and_invalidates_content_width(monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    from media_cli.ui import session_screen, view_presentation
    from media_cli.ui.view_model import Content, DetailLines, ViewItem, ViewSpec
    from media_cli.ui.view_session import ViewSession, ReplaceView
    from media_cli.context import CliFlags, UiState
    fields = DetailLines(Content('Title'), Content('Alternative'), Content('Metadata'))
    item = ViewItem('one', 'Title', details=fields, extra_details='Genres')
    view = ViewSpec('test', 'Test', (item,), details=fields)
    session = ViewSession(view)
    from media_cli.state.theme import MINT_SCREEN_THEME
    presentation = view_presentation.ViewPresentation(session, SimpleNamespace(enabled=lambda: False), theme=MINT_SCREEN_THEME)
    original = session_screen.render_detail_lines
    calls = []
    def render(**kwargs):
        calls.append(kwargs['width'])
        return original(**kwargs)
    monkeypatch.setattr(session_screen, 'render_detail_lines', render)
    def picker(*args, presentation, **kwargs):
        info = presentation['info_fn']
        assert info(0, 80) == info(0, 80)
        assert calls == [80]
        info(0, 60)
        assert calls == [80, 60]
        changed = replace(view, items=(replace(item, extra_details='New genre'),))
        session.apply(ReplaceView(session.current.id, session.generation, changed))
        assert any('New genre' in line for line in info(0, 60))
        assert calls == [80, 60, 60]
        return 0
    monkeypatch.setattr(session_screen, 'action_picker', picker)
    session_screen.session_screen_picker(CliFlags(), UiState(), 'Test', ['Title'],
                                       session=session, view_presentation=presentation)
