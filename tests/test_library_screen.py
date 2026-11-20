from types import SimpleNamespace

from media_cli.core.terminal import display_width, strip_ansi
from media_cli.ui import library_picker
from media_cli.ui.list_screen import list_screen_frame
from media_cli.ui.list_selection import ListSelection


def test_shared_layout_cover_and_small_screen_bounds(monkeypatch):
    state = ListSelection([("one", {"name": "One"})], "")
    for width, height in ((80, 40), (40, 24), (28, 12), (20, 8)):
        frame = list_screen_frame(state, "Downloads", width=width, height=height, info=["One", "Alt", "Metadata", "Directory"], status="Checking…", poster="COVER\nCOVER")
        assert len(frame.lines) == height
        assert all(display_width(line) < width for line in frame.lines)
        assert strip_ansi(frame.lines[0]).startswith("Anyplay · Download")
        if width >= 60 and height >= 26:
            assert any("COVER" in line for line in frame.lines)
        else:
            assert not any("COVER" in line for line in frame.lines)
    monkeypatch.setattr('media_cli.ui.list_screen.compact_cover', lambda raw: raw)
    native = list_screen_frame(state, "Downloads", width=80, height=40, poster="\033_Gfake\033\\")
    assert native.poster.startswith("\033_G")
    assert native.poster_row == 33


def test_selected_cover_is_beside_info_below_results():
    state = ListSelection([("one", {"name": "Result title"})], "")
    frame = list_screen_frame(state, "History", width=80, height=40,
                              info=("Selected title", "Alternative", "Metadata", "Provider"),
                              poster="COVER\nCOVER")
    plain = list(map(strip_ansi, frame.lines))
    title = next(line for line in plain if "Selected title" in line)
    assert title.startswith("COVER" + " " * 4 + "Selected title")
    result_row = next(i for i, line in enumerate(plain) if "Result title" in line)
    assert result_row < frame.poster_row - 1


def test_search_and_download_info_keep_genres_fourth_and_context_fifth():
    from media_cli.ui.library_info import library_info_lines
    show = {'name': 'Title', '_provider': 'anipm', 'genres': ['Comedy']}
    for context in ('AniPM', 'AniPM · 2 downloaded'):
        lines = list(map(strip_ansi, library_info_lines(show, 'sub', 80, context=context)))
        assert len(lines) == 5
        assert lines[3] == 'Comedy'
        assert lines[4] == context


def test_missing_cover_keeps_info_position_stable(monkeypatch):
    monkeypatch.setattr('media_cli.ui.list_screen.placeholder_cover', lambda: 'PLACEHOLDER')
    state = ListSelection([('one', {'name': 'Title'})], '')
    def frame(poster, reserve=True, width=80):
        return list_screen_frame(state, 'History', width=width, height=40,
            info=('Info title', 'Alt', 'Metadata', 'Genres', 'Provider'),
            poster=poster, reserve_cover=reserve)
    existing, missing = frame('COVER'), frame(None)
    def position(result):
        lines = list(map(strip_ansi, result.lines))
        row = next(i for i, line in enumerate(lines) if 'Info title' in line)
        return row, lines[row].index('Info title')
    assert position(existing) == position(missing)
    assert not any('PLACEHOLDER' in line for line in frame(None, False).lines)
    assert not any('PLACEHOLDER' in line for line in frame(None, width=40).lines)


def test_top_column_heading_is_muted_lowercase_and_does_not_take_list_space():
    from media_cli.state.theme import MINT_SCREEN_THEME
    from media_cli.ui.screen_chrome import role_prefix
    state = ListSelection([('one', {'name': 'One', 'prefix': '◆ '})], '', bottom=False)
    frame = list_screen_frame(state, 'Watch History', width=80, height=30)
    assert strip_ansi(frame.lines[3]) == '  title'
    state.entries[0][1]['prefix'] = ''
    assert list_screen_frame(state, 'Watch History', width=80, height=30).lines[3] == frame.lines[3]
    assert role_prefix(MINT_SCREEN_THEME, 'screen.column') in frame.lines[3]
    assert 'One' in frame.lines[4]
    state.bottom = True
    assert list_screen_frame(state, 'Watch History', width=80, height=30).lines[3] == ''


def test_column_heading_names_follow_the_list_content():
    from media_cli.ui.list_screen import list_column_label
    assert list_column_label('History · Episodes') == 'episode'
    assert list_column_label('Providers') == 'provider'
    assert list_column_label('Songs') == 'song'


def test_adapter_gates_selection_and_confirms_delete(monkeypatch):
    rows = ["One (1)", "Two (2)"]
    deleted = []
    ready = False
    def delete(index):
        deleted.append(rows.pop(index))
    def run(render, key):
        nonlocal ready
        render(80, 30, 0)
        assert key("ENTER") is None
        assert any("Still checking" in line for line in render(80, 30, 0).lines)
        ready = True
        render(80, 30, 0)
        key("DELETE")
        assert not deleted
        assert strip_ansi(render(80, 30, 0).lines[-1]) == "Delete title? y/N"
        assert strip_ansi(render(80, 30, 0).lines[-3]) == ""
        key("n")
        assert not deleted
        key("DELETE")
        key("y")
        assert deleted == ["One (1)"]
        frame = render(80, 30, 0)
        assert any("Two (2)" in line for line in frame.lines)
        return key("ENTER")
    monkeypatch.setattr(library_picker, "run_framed_screen", run)
    result = library_picker.run_library_picker(
        SimpleNamespace(show_image=False), SimpleNamespace(), rows.copy(),
        presentation={"status_fn": lambda i, w: "Checking…", "context": "/tmp/media"},
        live_fn=lambda q: (rows.copy(), "", ready), header_fn=lambda i: "Title\nAlt\nMetadata\nold footer",
        top_header_fn=lambda i: "", select_fn=lambda i: ready,
        delete_fn=delete, can_delete_fn=lambda i: ready, info_fn=None,
        item_key_fn=lambda i: rows[i].split()[0], delete_prompt="Delete title? y/N",
    )
    assert result == 0


def test_library_escape_filter_then_back_and_no_stale_info(monkeypatch):
    def run(render, key):
        render(80, 30, 0)
        key("/")
        assert "Filter downloaded titles…" in strip_ansi(render(80, 30, 0).lines[-1])
        for char in "zzzz":
            key(char)
        frame = render(80, 30, 0)
        assert any("No matching downloaded" in line for line in frame.lines)
        assert not any("PRIVATE-DETAILS" in line for line in frame.lines)
        assert key("ENTER") is None
        assert key("ESC") is None
        return key("ESC")
    monkeypatch.setattr(library_picker, "run_framed_screen", run)
    assert library_picker.run_library_picker(
        SimpleNamespace(show_image=False), SimpleNamespace(), ["One"],
        presentation={"status_fn": lambda i, w: ""}, live_fn=lambda q: (["One"], "", True),
        header_fn=lambda i: "PRIVATE-DETAILS\nAlt\nMeta\nFooter", top_header_fn=lambda i: "",
        select_fn=lambda i: True, delete_fn=lambda i: None, can_delete_fn=lambda i: True,
        info_fn=None, item_key_fn=lambda i: "one", delete_prompt="Delete title?",
    ) == -1


def test_library_info_has_fixed_slots_and_theme_colors():
    from media_cli.ui.library_info import library_info_lines
    show = {"_id": "test", "name": "Example", "genres": ["Action", "Fantasy"], "status": "AIRING", "type": "ONA", "_local_progress": 1}
    lines = library_info_lines(show, "sub", 90)
    plain = list(map(strip_ansi, lines))
    assert len(lines) == 4
    assert plain[0] == "Example"
    assert plain[1] == "No alternative title"
    assert "AIRING" in plain[2]
    assert plain[3] == "Action • Fantasy"
    assert lines[0].startswith("\033[38;5;253m")
    assert lines[1].startswith("\033[38;5;240m")
    assert "\033[38;5;111mAIRING" in lines[2]
    show["englishName"] = "Another title"
    assert strip_ansi(library_info_lines(show, "sub", 90)[1]) == "Another title"


def test_cursor_rehidden_and_parked_after_native_cover(monkeypatch):
    import os
    from contextlib import nullcontext
    from media_cli.ui import screen_chrome, display, terminal_images
    from media_cli.ui.list_screen import ScreenFrame
    writes = []
    monkeypatch.setattr(screen_chrome, "borrow_terminal", lambda: nullcontext(SimpleNamespace(fileno=lambda: 123)))
    monkeypatch.setattr(screen_chrome.os, "get_terminal_size", lambda fd: os.terminal_size((80, 24)))
    monkeypatch.setattr(screen_chrome.select, "select", lambda *args: ([123], [], []))
    monkeypatch.setattr(screen_chrome, "get_key", lambda fd: "CTRL_C")
    monkeypatch.setattr(screen_chrome, "write_terminal", writes.append)
    monkeypatch.setattr(display, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(terminal_images, "clear_if_active", lambda: "")
    monkeypatch.setattr(terminal_images, "mark_active", lambda: None)
    frame = ScreenFrame([""] * 24, "\033_Gfake\033\\\033[?25h", 5)
    assert screen_chrome.run_framed_screen(lambda *args: frame, lambda key: -5) == -5
    draw = next(text for text in writes if "_Gfake" in text)
    assert draw.endswith("\033[24;80H\033[?25l")


def test_fuzzy_filter_and_download_hint_and_summary_context():
    state = ListSelection([("one", {"name": "Beyond Times Gaze", "hint": "2 downloads"}),
                           ("two", {"name": "Martial Master", "hint": "1 download"})], "")
    state.key("/")
    for char in "btg":
        state.key(char)
    assert state.visible() == [0]
    frame = list_screen_frame(state, "Downloads", width=90, height=30, summary_context="/tmp/media")
    assert strip_ansi(frame.lines[2]) == "1/2 results     · /tmp/media"
    assert frame.lines[2].startswith("\033[38;5;114m")  # Tork styleOK/colGreen
    row = next(line for line in frame.lines if "❯" in line)
    assert "Beyond Times Gaze" in strip_ansi(row) and "2 downloads" in strip_ansi(row)
    assert "\033[38;5;240m" in row  # Hint is separate from title styling.


def test_missing_genres_is_explicit():
    from media_cli.ui.library_info import library_info_lines
    show = {"_id": "empty-genres", "name": "Example", "genres": [], "_local_progress": 0}
    assert strip_ansi(library_info_lines(show, "sub", 80)[3]) == "No genres available"


def test_information_values_inherit_terminal_foreground_and_roles():
    from media_cli.ui.library_info import library_info_lines
    from media_cli.state.theme import MINT_SCREEN_THEME
    show = {"_id": "roles", "name": "Example", "status": "FINISHED", "type": "TV", "genres": ["Action", "Fantasy"], "_local_progress": 2}
    lines = library_info_lines(show, "sub", 100)
    assert "\033[38;5;114mFINISHED" in lines[2]
    assert "\033[38;5;240m • " in lines[2]
    assert MINT_SCREEN_THEME.resolve_style("library.value").foreground is None
    assert MINT_SCREEN_THEME.resolve_style("library.genre").foreground is None
    assert "\033[38;5;240m • " in lines[3]
    assert "\033[38;5;245mAction" not in lines[3]


def test_footer_loading_filter_and_confirmation_priority():
    state = ListSelection([("one", {"name": "One"})], "")
    options = dict(width=80, height=30, info=["One", "Alt", "Meta", "Genres"], status="Checking files…", shortcuts=(("enter", "open"),))
    frame = list_screen_frame(state, "Downloads", **options)
    assert strip_ansi(frame.lines[-3]) == ""
    assert strip_ansi(frame.lines[-1]) == "Checking files…"
    assert not any("Checking files" in line for line in frame.lines[:-1])
    state.key("/")
    frame = list_screen_frame(state, "Downloads", **options)
    assert strip_ansi(frame.lines[-1]).startswith("/")
    frame = list_screen_frame(state, "Downloads", status_priority=True, **dict(options, status="Delete title? y/N"))
    assert strip_ansi(frame.lines[-1]) == "Delete title? y/N"


def test_ansi_only_idle_status_restores_shortcuts():
    from media_cli.ui.screen_chrome import shortcut_footer
    footer = shortcut_footer(80, (("enter", "open"), ("esc", "back")), message="\033[0m\033[38;5;244m   \033[0m")
    assert "enter" in strip_ansi(footer[-1])
    assert "esc" in strip_ansi(footer[-1])


def test_mint_selection_only_changes_arrow_not_semantic_label_colors():
    from media_cli.state.theme import MINT_SCREEN_THEME
    calls = []
    def render_name(selected, width):
        calls.append(selected)
        return "\033[38;5;114mEpisode 1 ✔\033[0m"
    state = ListSelection([("one", {"name": "Episode 1 ✔", "render_name": render_name})], "")
    frame = list_screen_frame(state, "Episodes", width=80, height=30)
    assert MINT_SCREEN_THEME.row_selected == MINT_SCREEN_THEME.row_normal
    assert calls == [False]
    row = next(line for line in frame.lines if "❯" in line)
    assert "\033[1;" not in row
    assert "\033[38;5;114mEpisode 1" in row


def test_shared_list_scrolls_only_when_selection_crosses_visible_edge():
    state = ListSelection([(str(i), {"name": f"Item {i:02}"}) for i in range(40)], "", selected=39)
    def rendered():
        return [strip_ansi(line) for line in list_screen_frame(state, "Episodes", width=80, height=24).lines]
    initial = rendered()
    first = next(line.strip() for line in initial if "Item" in line)
    state.key("UP")
    moved = rendered()
    assert next(line.strip() for line in moved if "Item" in line) == first
    assert any("❯ Item 38" in line for line in moved)
    while state.selected > state.scroll_start:
        state.key("UP")
        rendered()
    at_edge = state.scroll_start
    state.key("UP")
    rendered()
    assert state.scroll_start == at_edge - 1
    state.key("END")
    rendered()
    state.key("DOWN")
    rendered()
    assert state.selected == 0 and state.scroll_start == 0


def test_every_list_row_resets_inherited_terminal_color_before_its_text():
    state = ListSelection([("one", {"name": "One"}), ("two", {"name": "Two"})], "")
    for selected in (0, 1, 0):
        state.selected = selected
        frame = list_screen_frame(state, "History", width=80, height=24)
        rows = [line for line in frame.lines if strip_ansi(line).strip() in {"One", "Two", "❯ One", "❯ Two"}]
        assert len(rows) == 2
        assert all(line.startswith("\033[0m") for line in rows)


def test_shared_tabs_use_active_color_and_collapse_on_narrow_screens():
    from media_cli.state.theme import MINT_SCREEN_THEME
    from media_cli.ui.navigation_tabs import NavigationTabs
    from media_cli.ui.screen_chrome import role_prefix
    from media_cli.ui.view_model import ViewTab
    tabs = NavigationTabs(tuple(ViewTab(label, label) for label in ('Active', 'Up to date', 'Completed', 'All')), 'Up to date')
    state = ListSelection([('one', {'name': 'One'})], '')
    wide = list_screen_frame(state, 'Watch History', width=100, height=24, tabs=tabs)
    assert strip_ansi(wide.lines[2]) == '1 results   · Active    Up to date    Completed    All'
    assert '[' not in strip_ansi(wide.lines[2])
    assert role_prefix(MINT_SCREEN_THEME, 'tab_active') + 'Up to date' in wide.lines[2]
    assert strip_ansi(wide.lines[0]).strip() == 'Anyplay · Watch History'
    narrow = list_screen_frame(state, 'Watch History', width=32, height=24, tabs=tabs)
    assert strip_ansi(narrow.lines[2]) == '1 results   · … Up to date …'
    tiny = list_screen_frame(state, 'Watch History', width=18, height=24, tabs=tabs)
    assert 'Up to date' in strip_ansi(tiny.lines[2])


def test_result_count_slot_keeps_tabs_fixed_without_leading_padding():
    from media_cli.ui.navigation_tabs import NavigationTabs
    from media_cli.ui.view_model import ViewTab
    tabs = NavigationTabs((ViewTab('active', 'Active'),), 'active')
    positions = []
    for count in (1, 9, 10, 99, 999):
        state = ListSelection([(str(i), {'name': 'Item'}) for i in range(count)], '')
        line = strip_ansi(list_screen_frame(state, 'History', width=100, height=24, tabs=tabs).lines[2])
        assert line.startswith(f'{count} results')
        positions.append(line.index('Active'))
    assert len(set(positions)) == 1
    state = ListSelection([(str(i), {'name': 'Item'}) for i in range(999)], '', filtering=True)
    positions = []
    for count in (1, 9, 10, 99, 999):
        state.visible = lambda count=count: list(range(count))
        line = strip_ansi(list_screen_frame(state, 'History', width=100, height=24, tabs=tabs).lines[2])
        assert line.startswith(f'{count}/999 results')
        positions.append(line.index('Active'))
    assert len(set(positions)) == 1


def test_library_smart_filter_metadata_completion_and_selection(monkeypatch):
    from media_cli.app.title_filter_data import LIBRARY_FIELDS, library_filter_values
    show = dict(_provider='anipm', name='English title', genres=['Martial Arts'],
                type='ONA', status='AIRING', airedStart={'year': 2022})
    metadata = [dict(name='Folder title', filter_values=library_filter_values(show, 'Folder title', 2)),
                dict(name='Other title', filter_values=library_filter_values({'_provider': 'anikoto'}, 'Other title', 1))]
    selected = []
    def run(render, key):
        render(120, 30, 0)
        key('/')
        for char in 'genre:mart':
            key(char)
        key('RIGHT')
        for char in ' provider:anipm downloaded:>1 year:>=2020':
            key(char)
        frame = render(120, 30, 0)
        text = '\n'.join(strip_ansi(line) for line in frame.lines)
        assert 'genre:"Martial Arts"' in text
        assert 'Other title' not in text
        assert key('ENTER') == 0
        key('ESC')
        return -1
    monkeypatch.setattr(library_picker, 'run_framed_screen', run)
    library_picker.run_library_picker(SimpleNamespace(show_image=False), SimpleNamespace(),
        ['Folder title', 'Other title'],
        presentation={'filter_schema': LIBRARY_FIELDS, 'rows_fn': lambda: metadata,
                      'status_fn': lambda *a: '', 'info_fn': lambda *a: ()},
        live_fn=lambda q: (['Folder title', 'Other title'], '', True), header_fn=lambda i: '',
        top_header_fn=lambda i: '', select_fn=lambda i: selected.append(i) or True,
        delete_fn=lambda i: None, can_delete_fn=lambda i: True, info_fn=None,
        item_key_fn=lambda i: str(i), delete_prompt='Delete?')
    assert selected == [0]
