"""The shared live picker uses the composed bar without changing its controls."""

import os

import pytest
from test_ui_view_picker import ScriptedPicker, run
from test_ui_view_picker import environment as environment
from test_ui_view_terminal import Terminal, row_writes

from media_cli.ui.view_model import Blank, DetailLines, Inherit, ViewItem, ViewSpec, ViewTab
from media_cli.ui.view_session import ExitSession, ViewSession


@pytest.mark.parametrize(
    "query,cursor,selected,above,below",
    [
        ("", 0, 0, 0, 0),
        ("idol", 2, 2, 0, 0),
        ("idol", 4, 0, 3, 5),
    ],
)
def test_normal_width_text_matches_retained_legacy_renderer(query, cursor, selected, above, below):
    from media_cli.core.terminal import strip_ansi
    from media_cli.ui.navigation_bar import NavigationBar, render_navigation_bar
    from media_cli.ui.navigation_counter import CounterMode, NavigationCounter
    from media_cli.ui.picker import _legacy_navigation_line
    from media_cli.ui.query_viewport import QueryField
    from media_cli.ui.scroll_indicators import ScrollIndicators
    from media_cli.ui.styled_text import StyledText, TextRun

    counts = (NavigationCounter(CounterMode.SELECTED, selected),) if selected else ()
    counts += (NavigationCounter(CounterMode.FILTERED, 2, 10),)
    bar = NavigationBar(
        prompt=StyledText((TextRun("Search Anime", "prompt"),)),
        marker=StyledText((TextRun("\u276f", "prompt"),)),
        query=QueryField(query, cursor, True),
        counters=counts,
        scroll=ScrollIndicators(above, below),
    )
    old = _legacy_navigation_line(
        "Search Anime", query, cursor, True, 100, counts=(2, 10, selected), hidden_above=above, hidden_below=below
    )
    assert strip_ansi(render_navigation_bar(bar, width=100)) == strip_ansi(old)


@pytest.mark.parametrize("columns", [32, 40, 80])
def test_live_long_query_keeps_tail_and_count_visible_and_returns_full_query(tmp_path, columns):
    query = "prefix" * 20 + "TAIL"
    script = f"""
finish(picker_module.tui_pick(
    flags, ui, 'Search Anime', [{query!r} + ' one', {query!r} + ' two'],
    initial_query={query!r}, return_query_on_enter=True, return_on_quit=True,
    header='Primary\\nSecondary\\nDetails\\nFooter',
))
"""
    with Terminal(tmp_path, script, rows=12, columns=columns) as terminal:
        frame = terminal.until(f"\033[12;{columns}H".encode())
        nav = [line for row, line in row_writes(frame) if row == 8][-1]
        assert "TAIL" in nav
        assert "2/2" in nav
        os.write(terminal.master, b"\r")
        assert terminal.result() == query


def test_structured_views_supply_tab_and_prompt_as_separate_content(environment):
    spec = ViewSpec(
        "root",
        "Search AnimeThemes",
        (ViewItem("a", "Song", details=Inherit),),
        details=DetailLines(Blank, Blank, Blank),
        tabs=(ViewTab("anime", "Anime"), ViewTab("artist", "Artist")),
        active_tab_id="artist",
    )

    def inspect(options, hooks):
        assert hooks["navigation_prompt"].runs[0].text == "Search AnimeThemes"
        assert hooks["navigation_tabs"].active.id == "artist"
        return 0

    session = ViewSession(spec, lambda event: ExitSession(event.item_id))
    assert run(environment, session, ScriptedPicker(inspect)) == "a"


def test_query_clear_after_resize_does_not_reuse_an_invalid_scroll_offset(tmp_path):
    query = "prefix" * 20 + "TAIL"
    script = f"""
finish(picker_module.tui_pick(flags, ui, 'Search', [{query!r}],
    initial_query={query!r}, return_query_on_enter=True, return_on_quit=True,
    header='Primary\\nSecondary\\nDetails\\nFooter'))
"""
    with Terminal(tmp_path, script, rows=12, columns=80) as terminal:
        terminal.until(b"\x1b[12;80H")
        terminal.resize(12, 32)
        os.write(terminal.master, b"\x15Z\r")
        assert terminal.result() == "Z"


@pytest.mark.parametrize("reverse", [False, True])
def test_scroll_arrows_describe_the_visual_direction(tmp_path, reverse):
    script = f"""
finish(picker_module.tui_pick(flags, ui, 'Scroll', [str(i) for i in range(20)],
    reverse_items={reverse!r}, header='Primary\\nSecondary\\nDetails\\nFooter', return_on_quit=True))
"""
    with Terminal(tmp_path, script, rows=12, columns=80) as terminal:
        frame = terminal.until(b"\x1b[12;80H")
        nav = [line for row, line in row_writes(frame) if row == 8][-1]
        assert ("\u25b213" if reverse else "\u25bc13") in nav
        os.write(terminal.master, b"\x1b")
        assert terminal.result() == -2
