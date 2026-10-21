"""Semantic row fields remain plain for matching and non-TUI backends."""

import os

import pytest
from test_ui_view_terminal import Terminal

from media_cli.ui.styled_text import StyledText, TextRun
from media_cli.ui.view_model import Blank, DetailLines, Inherit, ViewItem, ViewSpec, visible_items


def test_styled_row_fields_have_plain_text_accessors_and_filtering():
    item = ViewItem("one", StyledText((TextRun("Episode ", "muted"), TextRun("12", "success"))),
                    hint=StyledText((TextRun("ready", "success"),)),
                    prefix=StyledText((TextRun("+ ", "success"),)), details=Inherit)
    view = ViewSpec("rows", "Pick", (item,), details=DetailLines(Blank, Blank, Blank))
    assert item.label_text == "Episode 12"
    assert item.hint_text == "ready" and item.prefix_text == "+ "
    assert visible_items(view, "ep12") == (item,)


@pytest.mark.parametrize("field", ["label", "hint", "prefix"])
def test_row_fields_reject_raw_color_codes(field):
    kwargs = {"label": "Row", field: "\033[31munsafe"}
    with pytest.raises(ValueError):
        ViewItem("one", details=Inherit, **kwargs)


def script(plain):
    return f"""
from media_cli.ui.styled_text import StyledText, TextRun
from media_cli.ui.theme import DEFAULT_THEME, TextStyle
flags.plain_mode = {plain!r}
item = ViewItem('one', StyledText((TextRun('Episode ', 'muted'), TextRun('12', 'success'))),
    hint=StyledText((TextRun('ready to play', 'success'),)),
    prefix=StyledText((TextRun('+ ', 'success'),)), details=Inherit)
view = ViewSpec('rows', 'ROW-TEXT', (item,), details=DetailLines(Blank, Blank, Blank))
session = ViewSession(view, lambda event: ExitSession(event.item_id) if event.kind == 'select' else None)
theme = replace(DEFAULT_THEME, success=TextStyle(93), row_selected=TextStyle(94), row_match=TextStyle(95))
result = run_view_session(flags, ui, session, poster_manager=manager, theme=theme)
finish(dict(selected=result, query=session.position.query))
"""


def test_tui_renders_roles_and_filters_styled_labels(tmp_path):
    with Terminal(tmp_path, script(False)) as terminal:
        output = terminal.until(b"ready to play")
        assert b"\x1b[38;5;93m" in output
        assert b"\x1b[38;5;94m" in output
        os.write(terminal.master, b"ep12\r")
        result = terminal.result()
        assert result == {"selected": "one", "query": "ep12"}


def test_plain_backend_never_receives_styled_objects_or_colors(tmp_path):
    with Terminal(tmp_path, script(True)) as terminal:
        output = terminal.until(b"Enter choice:")
        assert b"Episode 12" in output
        assert b"StyledText" not in output and b"TextRun" not in output
        assert b"\x1b[38;5;93m" not in output
        os.write(terminal.master, b"1\n")
        assert terminal.result()["selected"] == "one"


def test_styled_heading_stays_disabled_and_uses_theme(tmp_path):
    source = script(False).replace(
        "(item,), details=", "(ViewItem('heading', StyledText((TextRun('SECTION', 'success'),)), "
        "kind='heading', details=Inherit), item), details=",
    )
    with Terminal(tmp_path, source) as terminal:
        terminal.until(b"SECTION")
        os.write(terminal.master, b"\r")
        assert terminal.result()["selected"] == "one"
