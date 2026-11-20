from media_cli.core.terminal import display_width, strip_ansi
from media_cli.ui.provider_selection import ProviderSelection
from media_cli.ui.screen_chrome import shortcut_footer

ENTRIES = [("anipm", {"name": "AniPM", "type": "anime", "languages": ["sub", "dub"]}),
           ("senshi", {"name": "Senshi", "type": "anime", "languages": ["sub", "dub"]}),
           ("animexin", {"name": "AnimeXin", "type": "donghua", "languages": ["sub"]})]


def test_active_provider_is_separate_from_highlight():
    state = ProviderSelection(ENTRIES, "senshi")
    text = "\n".join(map(strip_ansi, state.lines(90, 35)))
    assert "Senshi ✔" in text
    assert "❯ AniPM" in text
    assert state.key("ENTER") == 0


def test_filter_keeps_navigation_and_clear_then_back():
    state = ProviderSelection(ENTRIES, "senshi")
    state.key("/")
    for key in "anime":
        state.key(key)
    assert len(state.visible()) == 3
    state.key("DOWN")
    assert state.selected == 1
    state.key("LEFT")
    assert state.cursor == 4
    state.key("ESC")
    assert not state.filtering and state.query == ""
    assert state.key("ESC") == -1
    assert state.key("CTRL_C") == -2


def test_no_matches_clears_selection_info_and_blocks_enter():
    state = ProviderSelection(ENTRIES, "senshi")
    state.key("/")
    for key in "zzzz":
        state.key(key)
    text = "\n".join(map(strip_ansi, state.lines(80, 30)))
    assert "No matching providers" in text
    assert "Sub / Dub" not in text
    assert state.key("ENTER") is None


def test_top_bottom_anchor_and_bounded_resize():
    for width, height in ((90, 40), (40, 24), (28, 16), (20, 8)):
        for bottom in (True, False):
            state = ProviderSelection(ENTRIES, "senshi", bottom=bottom)
            lines = state.lines(width, height)
            assert len(lines) == height
            assert all(display_width(line) < width for line in lines)
            assert "Anyplay" in strip_ansi(lines[0])
    top = list(map(strip_ansi, ProviderSelection(ENTRIES, "senshi", bottom=False).lines(90, 40)))
    bottom = list(map(strip_ansi, ProviderSelection(ENTRIES, "senshi").lines(90, 40)))
    assert top.index(next(line for line in top if "❯ AniPM" in line)) < bottom.index(next(line for line in bottom if "❯ AniPM" in line))
    assert top[-8:] == bottom[-8:]


def test_real_terminal_select_after_filter(tmp_path):
    from test_ui_view_terminal import Terminal
    script = '''
from media_cli.ui.provider_selection import run_provider_selection
entries = [("anipm", dict(name="AniPM")), ("senshi", dict(name="Senshi"))]
result = run_provider_selection(entries, "anipm")
finish(dict(selected=result))
'''
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b"Providers", b"/senshi\r")
        assert terminal.result() == {"selected": 1}


def test_shared_footer_exactly_two_rows():
    lines = shortcut_footer(80, (("enter", "select"), ("esc", "back")))
    assert len(lines) == 2
    assert "enter" in strip_ansi(lines[1])


def test_flat_list_header_alignment_and_semantic_theme():
    from dataclasses import replace
    from media_cli.state.theme import MINT_SCREEN_THEME
    from media_cli.ui.theme import TextStyle
    state = ProviderSelection(ENTRIES, "senshi")
    theme = replace(MINT_SCREEN_THEME, primary=TextStyle(120), row_normal=TextStyle(120), row_selected=TextStyle(121, bold=True), muted=TextStyle(122))
    lines = state.lines(90, 35, theme=theme)
    plain = list(map(strip_ansi, lines))
    assert plain[0].startswith("Anyplay · Providers")
    assert plain[1].startswith("─") and plain[-2].startswith("─")
    assert plain[2] == "3 results"
    assert not any("Category" in line or "Provider (" in line for line in plain)
    selected = next(line for line in lines if "❯" in line)
    assert "\033[38;5;120m" in selected
    assert strip_ansi(selected).startswith("❯ AniPM")
    assert not strip_ansi(selected).endswith("Anime")
    assert not any("-- " in line for line in plain)
    assert any("\033[38;5;120m" in line and "Senshi" in line for line in lines)


def test_filter_placeholder_aligned_hints_and_heavy_mark():
    state = ProviderSelection(ENTRIES, "senshi")
    lines = list(map(strip_ansi, state.lines(80, 30)))
    rows = [line for line in lines if line.startswith(('❯ AniPM', '  Senshi ✔', '  AnimeXin'))]
    assert len(rows) == 3
    assert len({line.index('Sub') for line in rows}) == 1
    assert not any('-- ' in line for line in lines)
    assert any("Senshi ✔" in line for line in lines)
    state.key("/")
    assert "Filter providers…" in strip_ansi(state.lines(80, 30)[-1])
    state.key("s")
    assert "Filter providers…" not in strip_ansi(state.lines(80, 30)[-1])


def test_normal_result_titles_use_terminal_foreground():
    from media_cli.state.theme import MINT_SCREEN_THEME
    from media_cli.ui.screen_chrome import role_prefix
    assert MINT_SCREEN_THEME.row_normal.foreground is None
    state = ProviderSelection(ENTRIES, "senshi")
    lines = state.lines(90, 35)
    row = next(line for line in lines if "Senshi ✔" in strip_ansi(line) and strip_ansi(line).startswith("  "))
    assert row.startswith("\033[0m  " + role_prefix(MINT_SCREEN_THEME, "row_normal") + "Senshi")
    assert "\033[38;5;253mSenshi" not in row


def test_table_has_only_provider_rows_and_navigation_keeps_original_identity():
    entries = ENTRIES + [('animethemes',dict(name='AnimeThemes',type='music')),
                         ('anilist',dict(name='AniList',type='tracker'))]
    for bottom in (False, True):
        state = ProviderSelection(entries, 'anipm', bottom=bottom)
        state.selected = 4
        lines = list(map(strip_ansi,state.lines(90,40)))
        assert lines[2] == '5 results'
        assert not any('-- ' in line for line in lines)
        heading=next(line for line in lines if 'name' in line and 'translation' in line)
        assert 'type' in heading
        state.key('DOWN')
        assert state.key('ENTER') == 3
        state.key('DOWN')
        assert state.key('ENTER') == 0
        state.key('/')
        for char in 'songs': state.key(char)
        assert '1/5 results' in strip_ansi(state.lines(90,40)[2])


def test_registry_info_is_five_useful_lines_without_health_claims():
    from media_cli.ui.provider_selection import provider_info
    meta = dict(name='AniPM', type='anime', languages=['sub', 'dub'], engine='api',
                domains=['https://ani.pm'], description='Browse, stream and download anime.')
    assert provider_info('anipm', meta, 'anipm') == (
        'AniPM', 'Anime · Sub / Dub', 'Browse, stream and download anime.',
        'API · ani.pm', 'Active for this session')
    assert all(provider_info('test', {}, 'other'))


def test_tracker_info_uses_account_state_without_exposing_credentials():
    from media_cli.ui.provider_selection import provider_info
    for linked in (False, True):
        info = provider_info('anilist', dict(name='AniList', type='tracker', account_linked=linked), 'anilist')
        assert len(info) == 5 and all(info)
        assert info[3] == 'Playback through a matched streaming provider.'
        assert ('Account linked' in info[4]) == linked


def test_provider_panel_uses_semantic_accents_not_grey_for_every_line():
    from media_cli.ui.provider_selection import render_provider_info
    from media_cli.ui.screen_chrome import role_prefix
    from media_cli.state.theme import MINT_SCREEN_THEME as theme
    meta = dict(name='AniPM', type='anime', languages=['sub', 'dub'], engine='api', domains=['https://ani.pm'])
    lines = render_provider_info('anipm', meta, 'anipm')
    assert role_prefix(theme, 'info.category') + 'Anime' in lines[1]
    assert role_prefix(theme, 'engine.api') + 'API' in lines[3]
    assert role_prefix(theme, 'library.value') + 'ani.pm' in lines[3]
    assert role_prefix(theme, 'success') + 'Active for this session' in lines[4]
    inactive = render_provider_info('anipm', meta, 'other')
    assert role_prefix(theme, 'success') not in inactive[4]
    tracker = render_provider_info('anilist', dict(type='tracker', account_linked=True), 'anilist')
    assert role_prefix(theme, 'success') + 'Account linked' in tracker[4]


def test_engine_styles_are_distinct_and_languages_remain_grey():
    from media_cli.ui.provider_selection import render_provider_info
    from media_cli.ui.screen_chrome import role_prefix
    from media_cli.state.theme import MINT_SCREEN_THEME as theme
    assert len({role_prefix(theme, 'engine.' + key) for key in ('api', 'hybrid', 'scraper')}) == 3
    for key, label in (('api', 'API'), ('hybrid', 'Hybrid'), ('scraper', 'Site scraper')):
        lines = render_provider_info('test', dict(type='anime', languages=['sub'], engine=key), 'other')
        assert role_prefix(theme, 'engine.' + key) + label in lines[3]
        assert role_prefix(theme, 'library.value') + 'Sub' in lines[1]


def test_animethemes_registry_does_not_claim_video_is_a_language():
    from media_cli.provider_manifest import PROVIDER_REGISTRY
    meta = dict(PROVIDER_REGISTRY['animethemes'], type='music')
    assert meta['languages'] == []
    state = ProviderSelection([('animethemes', meta)], 'animethemes')
    text = '\n'.join(map(strip_ansi, state.lines(90, 35)))
    assert 'Video' not in text
    assert '—' in text
