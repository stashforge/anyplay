"""Exercise normal search through the real shared session, without network."""

import os

import pytest
from test_ui_view_terminal import Terminal


def session_script(gate=None, *, single=False, reject=False):
    return f"""
from pathlib import Path
from media_cli.app.search_results_controller import SearchResultsController
from media_cli.context import UiState
from media_cli.state.theme import APPLICATION_THEME
ui = UiState()
rows = [dict(_id='one', name='Alpha', description='Synopsis', status='FINISHED', type='TV', _local_progress=0)]
if not {single!r}:
    rows.append(dict(_id='two', name='Beta', status='FINISHED', type='TV', _local_progress=0))
def ready():
    return {gate is None!r} or Path({str(gate)!r}).exists()
def select(show):
    if {reject!r}:
        ui.search_error = 'CANCELLED-RETAINED'
        return False
    return True
information = []
def info(show):
    information.append(show['_id'])
    ui.search_error = 'INFO-SEEN'
control = SearchResultsController(
    lambda q: ([], '', ready()), lambda: rows if ready() else [],
    lambda: '' if ready() else 'SEARCH-WAIT', lambda: '', ui=ui,
    provider_id='anikoto', provider_name='Anikoto', query='test', ttype='sub',
    prompt='Search Anime', auto_single=True, select=select, information=info)
outcome = run_view_session(flags, ui, control.session, theme=APPLICATION_THEME, updates_fn=control.updates)
finish(dict(kind=outcome.kind, selected=(outcome.show or {{}}).get('_id'),
    query=control.session.position.query, information=information))
"""


def test_live_result_data_reaches_shared_session_and_auto_single(tmp_path):
    gate = tmp_path / "ready"
    with Terminal(tmp_path, session_script(gate, single=True)) as terminal:
        terminal.until(b"SEARCH-WAIT")
        gate.touch()
        assert terminal.result()["selected"] == "one"
        assert b"\033[38;5;250mFINISHED" in terminal.output


@pytest.mark.parametrize("key,word", [(b"\r", b"pick"), (b"\x1b[A", b"browse")])
def test_empty_search_keeps_loading_feedback_and_does_not_replay_keys(tmp_path, key, word):
    gate = tmp_path / "ready"
    script = session_script(gate)
    # Keep wording deterministic without replacing input or rendering logic.
    script = (
        "from media_cli.ui import view_presentation\nview_presentation.result_wait_messages = lambda action: 'Nothing to pick yet' if action == 'select' else 'Nothing to browse yet'\n"
        + script
    )
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b"SEARCH-WAIT", key)
        terminal.until(word)
        terminal.until(b"SEARCH-WAIT")
        gate.touch()
        terminal.send_after(b"FINISHED", b"\x1b")
        assert terminal.result()["kind"] == "back"


def test_filtering_two_results_to_one_does_not_auto_enter(tmp_path):
    with Terminal(tmp_path, session_script()) as terminal:
        terminal.send_after(b"FINISHED", b"Alpha")
        terminal.until(b"Alpha")
        os.write(terminal.master, b"\r")
        assert terminal.result()["selected"] == "one"


def test_help_and_information_remain_available_without_changing_footer(tmp_path):
    with Terminal(tmp_path, session_script()) as terminal:
        terminal.send_after(b"Esc=search", b"?")
        terminal.until(b"Title information")
        terminal.send_after(b"Press Esc or ? to close", b"\x1b")
        terminal.send_after(b"Esc=search", b"\x0f")
        terminal.send_after(b"INFO-SEEN", b"\x1b")
        result = terminal.result()
        assert result["kind"] == "back"
        assert result["information"] == ["one"]


def test_cancelled_selection_retains_filter_until_user_goes_back(tmp_path):
    with Terminal(tmp_path, session_script(reject=True)) as terminal:
        terminal.send_after(b"FINISHED", b"Alpha\r")
        terminal.send_after(b"CANCELLED-RETAINED", b"\x1b")
        result = terminal.result()
        assert result["kind"] == "back"
        assert result["query"] == "Alpha"


def test_plain_renderer_selects_same_domain_identity_without_tui_styling(tmp_path):
    script = "flags.plain_mode = True\n" + session_script()
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b"Enter choice:", b"1\n")
        result = terminal.result()
        assert result["selected"] == "one"
        assert b"\033[38;5;250mFINISHED" not in terminal.output


def test_live_reordering_preserves_selected_identity_through_actual_picker(tmp_path):
    gate = tmp_path / "reorder"
    script = (
        session_script()
        .replace(
            "lambda: rows if ready() else []",
            f"lambda: list(reversed(rows)) if Path({str(gate)!r}).exists() else rows",
        )
        .replace(
            "lambda: '' if ready() else 'SEARCH-WAIT'", f"lambda: 'REORDERED' if Path({str(gate)!r}).exists() else ''"
        )
    )
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b"Esc=search", b"\x1b[A")
        terminal.until(b"Beta")
        gate.touch()
        terminal.send_after(b"REORDERED", b"\r")
        assert terminal.result()["selected"] == "two"
