"""Per-row action hints change presentation, never command bindings."""

import os
from dataclasses import replace

import pytest
from test_ui_view_terminal import Terminal

from media_cli.ui.view_model import Blank, DetailLines, Inherit, ViewAction, ViewItem, ViewSpec
from media_cli.ui.view_presentation import ViewPresentation
from media_cli.ui.view_session import ViewSession


def test_selected_row_supplies_footer_labels_without_rebinding_keys():
    from media_cli.ui.tui_actions import tui_bindings
    from media_cli.ui.view_model import ActionHint

    view = ViewSpec(
        "example", "Choose", (
            ViewItem("one", "First", details=Inherit,
                     action_hints=(ActionHint("select", "Open"), ActionHint("delete", "Delete"))),
            ViewItem("two", "Second", details=Inherit,
                     action_hints=(ActionHint("select", "Fetch"), ActionHint("delete", "Delete", visible=False))),
        ), details=DetailLines(Blank, Blank, Blank), actions=(
            ViewAction("select", "select", ("ENTER", "RIGHT")),
            ViewAction("delete", "Remove", ("DELETE", "CTRL_D"), footer=False),
        ),
    )
    presentation = ViewPresentation(ViewSession(view), object())
    assert presentation._footer(item_id="one").actions == ("Enter=Open", "Delete/Ctrl+D=Delete", "Esc=back")
    assert presentation._footer(item_id="two").actions == ("Enter=Fetch", "Esc=back")
    assert tui_bindings(view).resolve("CTRL_D").action.label == "Remove"
    assert view.actions[0].label == "select"


def test_action_hint_data_is_snapshotted_and_rejects_unknown_or_duplicate_ids():
    from media_cli.ui.view_model import ActionHint

    hints = [ActionHint("select", "Open")]
    row = ViewItem("one", "First", details=Inherit, action_hints=hints)
    hints.clear()
    assert row.action_hints == (ActionHint("select", "Open"),)
    with pytest.raises(ValueError, match="Duplicate"):
        replace(row, action_hints=(ActionHint("select", "Open"), ActionHint("select", "Fetch")))
    with pytest.raises(ValueError, match="Unknown.*action"):
        ViewSpec("example", "Choose", (row,), details=DetailLines(Blank, Blank, Blank))


@pytest.mark.parametrize("kwargs", [{"label": "\033[31mOpen"}, {"visible": "false"}, {"action_id": ""}])
def test_action_hints_reject_invalid_data(kwargs):
    from media_cli.ui.view_model import ActionHint

    with pytest.raises((TypeError, ValueError)):
        ActionHint(**({"action_id": "select", "label": "Open"} | kwargs))


def test_live_episode_footer_changes_with_highlight(tmp_path):
    source = """
from media_cli.app.episode_menu_view import EpisodeMenuOption, build_episode_menu_view
from media_cli.state.theme import APPLICATION_THEME
view = build_episode_menu_view({'_id': 'one', 'name': 'Example'}, (
    EpisodeMenuOption('one', 'Episode 1', downloaded=True),
    EpisodeMenuOption('two', 'Episode 2'),
), view_id='episodes:one', image_key='one', ttype='sub', local_only=True, show_cover=False)
session = ViewSession(view, lambda event: ExitSession(event.item_id) if event.kind == 'select' else None)
result = run_view_session(flags, ui, session, poster_manager=manager, theme=APPLICATION_THEME)
finish(dict(selected=result))
"""
    with Terminal(tmp_path, source) as terminal:
        terminal.send_after(b"Enter=Play", b"\x1b[A")
        output = terminal.until(b"Enter=Download")
        assert b"=Delete" not in output
        terminal.cursor = len(terminal.output)
        os.write(terminal.master, b"\x1b[B")
        terminal.until(b"Enter=Play")
        terminal.until(b"Delete/Ctrl+D=Delete")
        os.write(terminal.master, b"\r")
        assert terminal.result() == {"selected": "episode:sub:one"}
