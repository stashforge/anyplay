"""Shared History asks before invoking application-owned deletion."""

import pytest
from test_ui_view_terminal import Terminal

SOURCE = """
from media_cli.app.history_menu_view import build_history_view
from media_cli.app.history_menu_controller import run_history_menu
from media_cli.app.terminal_controller import run_screen_controller
from media_cli.context import CliFlags, UiState, MachineState
from media_cli.ui.view_position_memory import SavedViewPosition
entries = [dict(show=dict(_id='same', _provider=provider, name='Same title'), translation_type='sub')
           for provider in ('first', 'second')]
deleted = []
flags, ui = CliFlags(show_image=False), UiState()
ui._view_positions = {('history',): SavedViewPosition('history:second:same:sub', 'Same', 0, 2, 'All')}
def read():
    return build_history_view(entries, mode='All', total_count=len(entries), show_cover=False,
                              incognito=flags.incognito_mode)
def delete(reference):
    deleted.append(reference.provider_id)
    entries[:] = [entry for entry in entries if entry['show']['_provider'] != reference.provider_id]
def screen(flags, ui, ms, cfg, args):
    return run_history_menu(flags, ui, read_view=read, switch_mode=lambda mode: None,
        select=lambda ref: True, refresh_title=lambda ref: None, refresh_view=lambda: None,
        delete=None if flags.incognito_mode else delete, information=lambda ref: None)
try:
    outcome = run_screen_controller(screen, flags, ui, MachineState(), {}, SimpleNamespace(), fallback='HISTORY')
except KeyboardInterrupt:
    finish(dict(deleted=deleted, interrupted=True))
else:
    saved = ui._view_positions[('history',)]
    finish(dict(deleted=deleted, kind=outcome.kind, query=saved.query, cursor=saved.query_cursor,
                selected=saved.selected_id))
"""


@pytest.mark.parametrize("key", [b"\x04", b"\x1b[3~"])
@pytest.mark.parametrize("decision", [b"y", b"n", b"\x1b", b"\r"])
def test_shared_history_delete_requires_confirmation_and_keeps_filter(tmp_path, key, decision):
    with Terminal(tmp_path, SOURCE, columns=140) as terminal:
        terminal.send_after(b"Watch History", key)
        terminal.send_after(b"Delete this history entry? y/N", decision)
        terminal.send_after(b"Watch History", b"\x1b")
        result = terminal.result()
    assert result == dict(deleted=["second"] if decision == b"y" else [], kind="quit",
                          query="Same", cursor=2,
                          selected="history:first:same:sub" if decision == b"y" else "history:second:same:sub")


def test_shared_history_confirmation_preserves_interrupt(tmp_path):
    with Terminal(tmp_path, SOURCE, columns=140) as terminal:
        terminal.send_after(b"Watch History", b"\x04")
        terminal.send_after(b"Delete this history entry? y/N", b"\x03")
        assert terminal.result() == dict(deleted=[], interrupted=True)


def test_readonly_shared_history_does_not_delete(tmp_path):
    source = SOURCE.replace("CliFlags(show_image=False)", "CliFlags(show_image=False, incognito_mode=True)")
    with Terminal(tmp_path, source, columns=140) as terminal:
        terminal.send_after(b"Watch History", b"\x04\x1b[3~\x1b")
        result = terminal.result()
    assert result["deleted"] == [] and result["selected"] == "history:second:same:sub"


def test_shared_history_delete_keeps_identity_if_source_reorders_during_prompt(tmp_path):
    source = SOURCE.replace("try:\n    outcome", """
import media_cli.app.history_menu_controller as history_controller
original_confirm = history_controller.confirm_view_action
def confirm(*args, **kwargs):
    entries.reverse()
    return original_confirm(*args, **kwargs)
history_controller.confirm_view_action = confirm
try:
    outcome""")
    with Terminal(tmp_path, source, columns=140) as terminal:
        terminal.send_after(b"Watch History", b"\x04")
        terminal.send_after(b"Delete this history entry? y/N", b"y")
        terminal.send_after(b"Watch History", b"\x1b")
        result = terminal.result()
    assert result["deleted"] == ["second"] and result["query"] == "Same"
