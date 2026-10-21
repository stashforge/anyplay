"""Shared selected-row footer context and parent counts are supplied as data."""

from types import SimpleNamespace

import pytest
from test_ui_view_terminal import Terminal

from media_cli.ui.view_model import Blank, DetailLines, Inherit, ViewItem, ViewSpec
from media_cli.ui.view_presentation import ViewPresentation
from media_cli.ui.view_session import ViewSession


def test_row_context_is_snapshotted_and_composed_for_both_renderers():
    context = ["Provider", "Updated just now"]
    item = ViewItem("one", "First", details=Inherit, footer_context=context)
    context.clear()
    view = ViewSpec("view", "Choose", (item,), details=DetailLines(Blank, Blank, Blank),
                    context=("Common",), total_count=10)
    presentation = ViewPresentation(ViewSession(view), SimpleNamespace(enabled=lambda: False))
    assert item.footer_context == ("Provider", "Updated just now")
    assert presentation._footer().context == ("Common", "Provider", "Updated just now")
    assert presentation.plain_details()[-1] == "Common \u2022 Provider \u2022 Updated just now"


@pytest.mark.parametrize("context", ["text", ("\033[31mRed",), (3,)])
def test_invalid_row_context_is_rejected(context):
    with pytest.raises((TypeError, ValueError)):
        ViewItem("one", "First", details=Inherit, footer_context=context)


def test_history_snapshot_reaches_terminal_with_parent_count_and_row_context(tmp_path):
    source = """
from media_cli.app.history_menu_view import build_history_view
from media_cli.state.theme import APPLICATION_THEME
entries = tuple(dict(show=dict(_id=identity, _provider=provider, name=title, _local_progress=1),
                     translation_type='sub', timestamp=140)
                for identity, provider, title in [('one', 'example', 'First'), ('two', 'other', 'Second')])
view = build_history_view(entries, mode='All', total_count=9, show_cover=False, now=200)
session = ViewSession(view, lambda event: ExitSession(event.item_id) if event.kind == 'select' else None)
selected = run_view_session(flags, ui, session, poster_manager=manager, theme=APPLICATION_THEME)
finish(dict(selected=selected))
"""
    with Terminal(tmp_path, source, columns=140) as terminal:
        terminal.until(b"2/9")
        terminal.until(b"History")
        terminal.send_after(b"Example", b"\x1b[A")
        terminal.send_after(b"Other", b"\r")
        assert terminal.result() == {"selected": "history:other:two:sub"}
