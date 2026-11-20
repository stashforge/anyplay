"""Legacy presentation uses shared chrome without owning legacy input."""
from media_cli.core.terminal import strip_ansi
from media_cli.ui.legacy_list_picker import legacy_list_frame, legacy_row


def frame(**kwargs):
    args = dict(width=80, height=24, header="Title\nAlternative\nMetadata\nGenres\nStatus")
    args.update(kwargs)
    return legacy_list_frame("Legacy", ["one", "two", "three"], [0, 1, 2], 1, **args)


def test_header_status_is_separate_from_four_fixed_details():
    rendered, _, _ = frame()
    lines = [strip_ansi(line) for line in rendered.lines]
    assert lines[0].startswith("Anyplay · Legacy")
    assert lines[17:21] == ["Title", "Alternative", "Metadata", "Genres"]
    assert lines[-1] == "Status"


def test_old_mirror_instruction_heading_becomes_screen_name():
    from media_cli.ui.screen_chrome import standard_screen_title
    assert standard_screen_title('Select mirror', ('History', 'AniPM')) == 'History · Mirrors'
    assert standard_screen_title('Select version') == 'Versions'


def test_box_separator_does_not_leak_shortcuts_into_status():
    from media_cli.ui.legacy_list_picker import split_legacy_footer
    context, status = split_legacy_footer('3 mirrors found │ Ctrl+R=refresh · ?=Help · Esc=back')
    assert 'Ctrl+R' not in context + status
    assert '3 mirrors found' in context


def test_adapter_keeps_filtered_order_without_refiltering():
    rendered, _, _ = legacy_list_frame("Menu", ["one", "two", "three"], [2, 0], 2,
                                      width=80, height=24, query="external", cursor=3)
    lines = [strip_ansi(line) for line in rendered.lines]
    assert any("❯ three" in line for line in lines)
    assert not any("two" in line for line in lines)
    assert "external" in lines[-1]
    assert "\033[7me\033[27m" in rendered.lines[-1]


def test_reverse_and_forward_rows():
    for reverse, expected in [(True, ["three", "two", "one"]), (False, ["one", "two", "three"])]:
        rendered, _, _ = frame(reverse=reverse)
        rows = [strip_ansi(line).replace("❯", "").strip() for line in rendered.lines[4:16]]
        assert [row for row in rows if row] == expected


def test_confirmation_overrides_query_and_compact_frame_is_bounded():
    rendered, _, _ = frame(height=12, query="one", cursor=3, priority=True, header="Detail\nDelete? y/N")
    assert len(rendered.lines) == 12
    assert strip_ansi(rendered.lines[-1]) == "Delete? y/N"


def test_mint_rows_keep_marks_prefix_and_disabled_style():
    assert strip_ansi(legacy_row("one", width=40, prefix="* ", marked=True)) == "* [✔] one"
    assert legacy_row("one", width=40, disabled=True) != legacy_row("one", width=40)


def test_terminal_page_uses_shared_frame_capacity(tmp_path):
    from test_ui_view_terminal import Terminal
    script = """
finish(picker_module.tui_pick(flags, ui, 'LEGACY',
    [f'item {i}' for i in range(30)], initial_selected=12,
    reverse_items=False, return_on_quit=True))
"""
    with Terminal(tmp_path, script, rows=24) as terminal:
        terminal.send_after(b'\x1b[24;100H', b'\x1b[6~\r')
        assert terminal.result() == 24  # Twelve result rows in shared 24-row frame.


def test_structured_terminal_does_not_call_legacy_adapter(tmp_path):
    from test_ui_view_terminal import Terminal
    script = """
import media_cli.ui.legacy_list_picker as adapter
def forbidden(*args, **kwargs):
    raise AssertionError('Structured picker must retain old route')
adapter.legacy_list_frame = forbidden
finish(picker_module.tui_pick(flags, ui, 'STRUCTURED', ['one'],
    structured=True, return_on_quit=True))
"""
    with Terminal(tmp_path, script, rows=24) as terminal:
        terminal.send_after(b'\x1b[24;100H', b'\r')
        assert terminal.result() == 0


def test_navigation_footer_moves_context_to_summary_and_keeps_shared_shortcuts():
    rendered, _, _ = frame(header='Title\nProvider • Enter=open • Esc=back',
                           shortcuts=[('enter', 'select'), ('esc', 'back')])
    lines = [strip_ansi(line) for line in rendered.lines]
    assert lines[2] == '3 results   · Provider'
    assert 'enter select' in lines[-1] and 'esc back' in lines[-1]
    assert 'Enter=open' not in '\n'.join(lines)


def test_real_status_survives_navigation_cleanup():
    rendered, _, _ = frame(header='Title\nProvider • Loading results… • Enter=open • Esc=back',
                           shortcuts=[('Enter', 'select')])
    lines = [strip_ansi(line) for line in rendered.lines]
    assert lines[2] == '3 results   · Provider'
    assert lines[-1] == 'Loading results…'


def test_navigation_variants_are_removed_but_errors_remain():
    rendered, _, _ = frame(header='Title\nProvider | Tab=switch | Ctrl+R reverse | Shift+Tab previous | ? Help | Error: offline')
    lines = [strip_ansi(line) for line in rendered.lines]
    assert lines[2] == '3 results   · Provider'
    assert lines[-1] == 'Error: offline'


def test_info_neutralizes_legacy_ansi_and_applies_mint_roles():
    from media_cli.state.theme import MINT_SCREEN_THEME
    from media_cli.ui.screen_chrome import RESET, role_prefix
    rendered, _, _ = frame(header='\x1b[31mTitle\x1b[0m\n\x1b[1mAlt\n\x1b[35mMetadata\nGenres\nStatus')
    from media_cli.ui.metadata_styles import legacy_metadata
    from media_cli.ui.styled_text import render_styled_text
    assert rendered.lines[17] == role_prefix(MINT_SCREEN_THEME, 'primary') + 'Title' + RESET
    assert rendered.lines[18] == role_prefix(MINT_SCREEN_THEME, 'secondary') + 'Alt' + RESET
    assert rendered.lines[19] == render_styled_text(legacy_metadata('Metadata'), width=80, theme=MINT_SCREEN_THEME)
    assert '\x1b[35m' not in rendered.lines[19]
    assert strip_ansi(rendered.lines[20]) == 'Genres'


def test_layout_controls_anchoring_independently_from_order():
    for reverse in (False, True):
        top, _, _ = frame(reverse=reverse, tui_layout='top')
        bottom, _, _ = frame(reverse=reverse, tui_layout='bottom')
        expected = ['three', 'two', 'one'] if reverse else ['one', 'two', 'three']
        for rendered, start in ((top, 4), (bottom, 13)):
            labels = [strip_ansi(line).replace('❯', '').strip() for line in rendered.lines[start:start + 3]]
            assert labels == expected


def test_action_hint_is_inline_and_does_not_hide_shortcuts():
    rendered, _, _ = frame(header='Title\nEnter=open • Esc=back', action_hint='Open folder',
                           shortcuts=[('Enter', 'select')])
    assert strip_ansi(rendered.lines[2]) == '3 results'
    assert any('❯ two Open folder' in strip_ansi(line) for line in rendered.lines)
    assert 'Enter select' in strip_ansi(rendered.lines[-1])


def test_terminal_top_layout_changes_order_and_arrow_direction_together(tmp_path):
    from test_ui_view_terminal import Terminal
    script = """
import media_cli.ui.legacy_list_picker as adapter
original = adapter.legacy_list_frame
seen = []
def capture(*args, **kwargs):
    seen.append((kwargs['tui_layout'], kwargs['reverse']))
    return original(*args, **kwargs)
adapter.legacy_list_frame = capture
ui.tui_layout = 'top'
result = picker_module.tui_pick(flags, ui, 'LAYOUT', ['one', 'two', 'three'],
    reverse_items=True, initial_selected=1, return_on_quit=True,
    header='Title\\nProvider • Enter=open • Esc=back')
finish(dict(result=result, layout=seen[-1]))
"""
    with Terminal(tmp_path, script, rows=24) as terminal:
        terminal.send_after(b'\x1b[24;100H', b'\x1b[A\r')
        assert terminal.result() == {'result': 0, 'layout': ['top', False]}


def test_generic_lowercase_controls_return_after_status_clears():
    loading, _, _ = frame(header='Title\nLoading results…')
    assert strip_ansi(loading.lines[-1]) == 'Loading results…'
    for header in ('Title\n', 'Title\nProvider • Enter=open • Esc=back',
                   'Title\n\x1b[35m\x1b[0m'):
        idle, _, _ = frame(header=header)
        footer = strip_ansi(idle.lines[-1])
        for hint in ('enter select', '↑↓ move', 'type filter', 'esc back'):
            assert hint in footer
        assert 'esc quit' not in footer
        assert '/ filter' not in footer


def test_terminal_generic_footer_and_type_filter_preserve_input(tmp_path):
    from test_ui_view_terminal import Terminal
    script = """
from media_cli.core.terminal import strip_ansi
import media_cli.ui.legacy_list_picker as adapter
original = adapter.legacy_list_frame
footers = []
def capture(*args, **kwargs):
    result = original(*args, **kwargs)
    footers.append(strip_ansi(result[0].lines[-1]))
    return result
adapter.legacy_list_frame = capture
from media_cli.ui.picker_state import PickerState
state = PickerState()
result = picker_module.tui_pick(flags, ui, 'SUBMENU', ['one', 'two'], state=state,
    header='Title\\nProvider • Enter=open • Esc=back', return_on_quit=True)
finish(dict(result=result, footer=footers[0], query=state.query))
"""
    with Terminal(tmp_path, script, rows=24) as terminal:
        terminal.send_after(b'\x1b[24;100H', b'two\r')
        result = terminal.result()
        assert result['result'] == 1
        assert result['query'] == 'two'
        for hint in ('enter select', '↑↓ move', 'type filter', 'esc back'):
            assert hint in result['footer']


def test_watched_progress_color_is_preserved_with_filter_highlights():
    from media_cli.state.theme import MINT_SCREEN_THEME
    from media_cli.ui.screen_chrome import role_prefix
    row = legacy_row('\033[38;5;244mEpisode 13\033[0m', width=80, query='13')
    assert strip_ansi(row) == 'Episode 13'
    assert '\033[38;5;244m' in row
    assert role_prefix(MINT_SCREEN_THEME, 'row_match') + '1' in row


def test_legacy_title_panel_uses_five_shared_semantic_slots():
    rendered, _, _ = frame(height=35, header='Title\nAlt\nFINISHED • TV • Watched 2/12\nComedy\nAniPM\nCtrl+R=refresh • Esc=back')
    lines = [strip_ansi(line) for line in rendered.lines]
    start = lines.index('Title')
    assert lines[start:start + 5] == ['Title', 'Alt', 'FINISHED  TV • Watched 2/12', 'Comedy', 'AniPM']
    assert '\033[38;5;114m' in rendered.lines[start + 2]


def test_search_chrome_reserves_provider_and_displays_tabs():
    from media_cli.ui.navigation_tabs import NavigationTabs
    from media_cli.ui.view_model import ViewTab
    tabs = NavigationTabs((ViewTab('anime', 'Anime'), ViewTab('series', 'Series')), 'series')
    rendered, _, _ = frame(width=65, chrome=dict(title='Results · ' + 'long query ' * 20, badge='AnimeThemes', tabs=tabs))
    lines = [strip_ansi(line) for line in rendered.lines]
    assert lines[0].endswith('AnimeThemes')
    assert '…' in lines[0]
    assert 'Anime' in lines[2] and 'Series' in lines[2]
    assert 'Status' not in lines[2]


def test_searching_footer_uses_shared_loading_color_after_sanitizing():
    from media_cli.state.theme import MINT_SCREEN_THEME
    from media_cli.ui.footer import Footer, FooterStatus, StatusKind, render_footer
    rendered, _, _ = frame(header='\n\n\n\x1b[31mSearching…\x1b[0m')
    expected = render_footer(Footer(statuses=(FooterStatus('Searching…', StatusKind.LOADING),)), 80, theme=MINT_SCREEN_THEME)
    assert rendered.lines[-1] == expected
    assert '\x1b[31m' not in rendered.lines[-1]


def test_empty_cover_slot_uses_shared_placeholder(monkeypatch):
    from media_cli.ui.legacy_list_picker import legacy_list_frame
    from media_cli.ui import list_screen
    calls = []
    monkeypatch.setattr(list_screen, 'placeholder_cover', lambda: calls.append(True) or '')
    legacy_list_frame('Results', ['Title'], [0], 0, width=80, height=40,
                      header='Title\nAlt\nTV\nGenres\nProvider\nEnter=open', poster='')
    assert calls
    calls.clear()
    legacy_list_frame('Results', ['Title'], [0], 0, width=80, height=40,
                      header='Title\nAlt\nTV\nGenres\nProvider\nEnter=open', poster=None)
    assert not calls
