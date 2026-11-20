"""Landing-page focus and bounded rendering regressions."""
from media_cli.ui.search_home import SearchHomeState, search_home_lines
from media_cli.core.terminal import display_width


def test_query_and_focus_survive_provider_round_trip():
    state = SearchHomeState()
    for key in "oshi no ko":
        state.key(key)
    state.key("TAB")
    assert state.key("ENTER") == "history"
    state.key("TAB")
    assert state.key("ENTER") == "downloads"
    state.key("TAB")
    assert state.key("ENTER") == "providers"
    state.focus = 0
    assert state.key("ENTER") == "search"
    assert state.query == "oshi no ko"


def test_editing_and_history_do_not_activate_actions():
    state = SearchHomeState(history=("previous",))
    state.key("UP")
    assert state.query == "previous"
    state.key("LEFT")
    state.key("BACKSPACE")
    assert state.query == "previos"
    assert state.focus == 0
    assert state.key("ESC") is None
    assert state.query == "" and state.cursor == 0 and state.focus == 0
    assert state.key("CTRL_C") == "quit"


def test_empty_search_is_not_submitted():
    assert SearchHomeState(query="  ").key("ENTER") is None


def test_history_action_and_focus_wrap_preserve_query():
    state = SearchHomeState(query="slime", cursor=5)
    state.key("BTAB")
    assert state.key("ENTER") == "providers"
    state.key("TAB")
    state.key("TAB")
    assert state.key("ENTER") == "history"
    assert state.query == "slime"
    state.key("BTAB")
    assert state.focus == 0


def test_search_home_returns_history_route(monkeypatch):
    from types import SimpleNamespace
    from media_cli.app import search
    monkeypatch.setattr(search, "call_on_ui_thread", lambda fn: fn())
    monkeypatch.setattr("media_cli.ui.search_home.run_search_home", lambda *a, **kw: "history")
    assert search.prompt_search_home(SimpleNamespace(incognito_mode=True),
        SimpleNamespace(search_error=""), SimpleNamespace(provider="anipm"), {}) == -8


def test_frames_fit_desktop_and_phone():
    for width, height in ((100, 40), (40, 24), (28, 16), (20, 8)):
        state = SearchHomeState(query="界" * 100, cursor=100)
        lines = search_home_lines(state, "AniPM", width, height, message="Loading…")
        assert len(lines) == height
        assert all(display_width(line) < width for line in lines)
        assert "esc" in lines[-1] or width < 40


def test_provider_selection_preserves_query_and_marks_current(monkeypatch):
    from types import SimpleNamespace
    from media_cli.app import search
    from media_cli import providers
    from media_cli.ui import search_home, provider_selection

    args = SimpleNamespace(provider="senshi")
    flags = SimpleNamespace(incognito_mode=True)
    ui = SimpleNamespace(search_error="")
    calls = []
    def home(state, provider, **kwargs):
        calls.append((state.query, provider))
        if len(calls) == 1:
            state.query, state.cursor = "oshi no ko", 10
            return "providers"
        return "search"
    def picker(entries, current, **kwargs):
        assert [key for key, _ in entries] == ["anipm", "senshi", "animethemes", "anilist"]
        assert current == "senshi"
        return 0
    monkeypatch.setattr(providers, "available_providers", lambda: {"anipm": None, "senshi": None})
    monkeypatch.setattr(providers, "provider_display_name", lambda key: {"anipm": "AniPM", "senshi": "Senshi"}[key])
    monkeypatch.setattr(search, "call_on_ui_thread", lambda fn: fn())
    monkeypatch.setattr(search_home, "run_search_home", home)
    monkeypatch.setattr(provider_selection, "run_provider_selection", picker)
    assert search.prompt_search_home(flags, ui, args, {}) == "oshi no ko"
    assert args.provider == "anipm"
    assert calls == [("", "Senshi"), ("oshi no ko", "AniPM")]


def test_landing_page_real_terminal_query_and_provider_action(tmp_path):
    from test_ui_view_terminal import Terminal
    script = '''
from media_cli.ui.search_home import SearchHomeState, run_search_home
state = SearchHomeState()
action = run_search_home(state, "AniPM")
finish(dict(action=action, query=state.query))
'''
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b"Providers", b"oshi no ko\t\t\t\r")
        assert terminal.result() == {"action": "providers", "query": "oshi no ko"}


def test_brand_placeholder_and_provider_specific_copy():
    from media_cli.core.terminal import strip_ansi
    state = SearchHomeState(focus=1)
    anime = "\n".join(strip_ansi(line) for line in search_home_lines(state, "AniPM", 90, 42))
    torrent = "\n".join(strip_ansi(line) for line in search_home_lines(state, "Torrents", 90, 42))
    assert "Find a show and watch or download it." in anime
    assert "Search for a title…" in anime
    assert "Search torrents…" in torrent
    assert "magnet" not in anime
    assert "Play downloaded titles" in anime
    assert "█" in anime


def test_compact_wordmark_and_provider_taglines():
    from media_cli.ui.search_home import LOGO, ACCENT, search_tagline
    assert len(LOGO) == 2
    assert ACCENT == "\033[38;5;114m"
    assert search_tagline("AnimeXin", "donghua") == "Find a donghua and watch or download it."
    assert search_tagline("Movies", "movies") == "Find a movie or show to watch or download."
    assert search_tagline("Torrents", "torrent") == "Find a torrent and choose what to play."


def test_history_keys_never_navigate_menu_and_cursor_blinks():
    from media_cli.core.terminal import strip_ansi
    state = SearchHomeState(focus=2, history=("erased",))
    state.key("UP")
    assert state.focus == 0 and state.query == "erased"
    shown = search_home_lines(state, "AniPM", 80, 30, cursor_visible=True)
    hidden = search_home_lines(state, "AniPM", 80, 30, cursor_visible=False)
    assert shown != hidden
    assert [strip_ansi(line) for line in shown] == [strip_ansi(line) for line in hidden]  # blinking never shifts the layout
    assert "tab/↑↓" not in strip_ansi(shown[-1])
    assert any("↑↓ browse history" in strip_ansi(line) and "\033[38;5;240m" in line for line in shown)


def test_escape_clears_from_menu_without_exiting():
    state = SearchHomeState(query="slime", cursor=5, focus=2, history_index=0)
    assert state.key("ESC") is None
    assert (state.query, state.cursor, state.focus, state.history_index) == ("", 0, 0, -1)
    assert state.key("ESC") is None
    assert state.key("CTRL_C") == "quit"


def test_home_actions_keep_focus_emphasis_separate_from_result_rows():
    from media_cli.state.theme import MINT_SCREEN_THEME
    selected = search_home_lines(SearchHomeState(focus=2), "AniPM", 80, 35)
    downloads = next(line for line in selected if "Downloads" in line)
    assert "\033[1;38;5;253mDownloads" in downloads
    assert MINT_SCREEN_THEME.row_selected == MINT_SCREEN_THEME.row_normal


def test_animethemes_selection_keeps_music_identity(monkeypatch):
    from types import SimpleNamespace
    from media_cli.app import search
    monkeypatch.setattr(search, 'call_on_ui_thread', lambda fn: fn())
    args = SimpleNamespace(provider='anipm')
    seen = []
    def home(state, provider, **kwargs):
        seen.append((provider, kwargs['provider_type']))
        if len(seen) == 1:
            return 'providers'
        state.query = 'oshi no ko'
        return 'search'
    def choose(entries, current, **kwargs):
        index = next(i for i, (key, meta) in enumerate(entries) if key == 'animethemes')
        assert entries[index][1]['type'] == 'music'
        return index
    monkeypatch.setattr('media_cli.ui.search_home.run_search_home', home)
    monkeypatch.setattr('media_cli.ui.provider_selection.run_provider_selection', choose)
    assert search.prompt_search_home(SimpleNamespace(incognito_mode=True), SimpleNamespace(search_error=''), args, {}) == 'oshi no ko'
    assert seen[-1][0] == 'AnimeThemes'
    assert args.provider == 'animethemes'


def test_animethemes_empty_search_uses_home_and_allows_library(monkeypatch):
    from types import SimpleNamespace
    from media_cli.app import animethemes, search
    monkeypatch.setattr(search, 'prompt_search_home', lambda *a: -7)
    ui = SimpleNamespace(search_error='')
    args = SimpleNamespace()
    result = animethemes.handle_animethemes_search_state(SimpleNamespace(plain_mode=False), ui,
        SimpleNamespace(query_str=''), {}, args, 'sub')
    assert result == 'DOWNLOADS'
    assert args.provider == 'animethemes'
    assert ui.downloads_parent == 'SEARCH'


def test_anilist_search_choice_exposes_library_and_airing_without_stream_adapter(monkeypatch):
    from types import SimpleNamespace
    from media_cli.app import search
    monkeypatch.setattr(search, 'call_on_ui_thread', lambda fn: fn())
    def home(state, provider, **kwargs):
        assert provider == 'AniList'
        assert [entry[0] for entry in state.menu] == ['lists','airing','providers']
        return 'airing'
    monkeypatch.setattr('media_cli.ui.search_home.run_search_home', home)
    assert search.prompt_search_home(SimpleNamespace(incognito_mode=True),SimpleNamespace(search_error=''),
        SimpleNamespace(provider='anilist'),{}) == -10


def test_editor_preserves_leading_trailing_and_repeated_spaces():
    from media_cli.core.terminal import strip_ansi
    state = SearchHomeState()
    for char in '  oshi  no ko  ':
        state.key(char)
    for shown in (True, False):
        lines = search_home_lines(state, 'AniPM', 80, 30, cursor_visible=shown)
        bar = next(strip_ansi(line) for line in lines if '│ ❯' in strip_ansi(line))
        assert '│ ❯   oshi  no ko  ' in bar
    state.key('LEFT'); state.key('BACKSPACE')
    assert state.query == '  oshi  no ko '
    state.key('HOME'); state.key('DELETE')
    assert state.query == ' oshi  no ko '


def test_long_query_viewport_is_stable_during_blink_and_focus_change():
    from media_cli.core.terminal import strip_ansi
    state = SearchHomeState(query='START' + 'x' * 100 + 'TAIL', cursor=109)
    bars = []
    for focus, shown in ((0, True), (0, False), (1, False)):
        state.focus = focus
        lines = search_home_lines(state, 'AniPM', 60, 30, cursor_visible=shown)
        bars.append(next(strip_ansi(line) for line in lines if '│ ❯' in strip_ansi(line)))
    assert bars[0] == bars[1] == bars[2]
    assert 'TAIL' in bars[0] and 'START' not in bars[0]


def test_editable_sanitizer_preserves_spaces_without_terminal_sequences():
    from media_cli.core.terminal import sanitize_input_text
    assert sanitize_input_text('  a  b  ') == '  a  b  '
    assert sanitize_input_text('a\x1b[31mb\n') == 'ab '
