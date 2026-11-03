"""Paint-only footer updates must not retire the active picker or its rows."""

from dataclasses import replace

import pytest
from test_ui_view_picker import ScriptedPicker, environment as environment

from media_cli.ui.footer import FooterStatus, StatusKind
from media_cli.ui.picker_state import PickerState
from media_cli.ui.view_model import Blank, DetailLines, Inherit, ViewItem, ViewSpec
from media_cli.ui.view_picker import run_view_session
from media_cli.ui.view_session import BeginNavigation, ExitSession, ReplaceView, UpdateFooter, ViewSession


def view():
    return ViewSpec('menu', 'Pick', (ViewItem('one', 'One', details=Inherit),
                                  ViewItem('two', 'Two', details=Inherit)),
                    details=DetailLines(Blank, Blank, Blank))


def status(frame):
    return (FooterStatus('Refreshing titles...', StatusKind.LOADING, str(frame)),)


def test_footer_preserves_session_position_rows_and_pending_navigation():
    session = ViewSession(view())
    frame = session.frame(session.current.id, session.generation)
    session.position.selected_id = 'two'
    session.position.query = 'o'
    session.position.scroll = 1
    session.position.marked_ids = {'two'}
    frame.picker_state = PickerState(query='o', selected_index=1, scroll=1)
    session.apply(BeginNavigation('menu', session.generation, 'fetch', 'Fetching', item_id='two'))
    version, generation, revision = session.version, session.generation, session.current.revision
    rows, position, picker_state, navigation = session.current.items, session.position, frame.picker_state, session.navigation
    for i in range(50):
        assert session.apply(UpdateFooter('menu', generation, status(i)))
    assert (session.version, session.generation, session.current.revision) == (version, generation, revision)
    # ViewSpec snapshots tuples on construction; the immutable row objects and
    # the active renderer's index map must remain usable without reconciliation.
    assert all(current is previous for current, previous in zip(session.current.items, rows, strict=True))
    assert session.position is position
    assert frame.picker_state is picker_state and session.navigation is navigation
    assert session.position.selected_id == 'two' and session.position.query == 'o'
    assert session.position.scroll == 1 and session.position.marked_ids == {'two'}


def test_footer_queue_coalesces_and_rejects_stale_or_unchanged_updates():
    session = ViewSession(view())
    for i in range(50):
        session.post(UpdateFooter('menu', session.generation, status(i)))
    assert session.pending_count == 1
    assert session.drain_updates()
    assert session.current.footer_statuses == status(49) and session.version == 0
    assert not session.apply(UpdateFooter('menu', session.generation, status(49)))
    assert not session.apply(UpdateFooter('menu', session.generation + 1, status(50)))
    assert session.apply(UpdateFooter('menu', session.generation, ()))


def test_real_row_change_still_retires_the_picker():
    session = ViewSession(view())
    version = session.version
    session.apply(ReplaceView('menu', session.generation, replace(view(), items=(ViewItem('three', 'Three', details=Inherit),))))
    assert session.version == version + 1


@pytest.mark.parametrize('statuses', [[], ('not a status',)])
def test_footer_command_validates_immutable_statuses(statuses):
    with pytest.raises(TypeError):
        UpdateFooter('menu', 1, statuses)


def test_fifty_footer_ticks_repaint_without_picker_reentry(environment):
    flags, ui, manager = environment
    session = ViewSession(view(), lambda event: ExitSession(event.item_id))
    session.position.selected_id = 'two'
    session.position.query = 'o'
    pending = []
    def updates():
        result, pending[:] = tuple(pending), []
        return result
    def navigate(options, hooks):
        state = hooks['state']
        for i in range(50):
            pending.append(UpdateFooter('menu', session.generation, status(i)))
            _, header, _ = hooks['live_fn']('o')
            assert str(i) in header and 'Refreshing titles...' in header
            assert not hooks['exit_fn']()
            assert hooks['state'] is state and state.selected_index == 1
        return 1
    picker = ScriptedPicker(navigate)
    assert run_view_session(flags, ui, session, picker=picker, poster_manager=manager, updates_fn=updates) == 'two'
    assert picker.calls == 1


def test_terminal_footer_ticks_only_repaint_footer_and_keep_one_picker(tmp_path):
    from test_ui_view_terminal import Terminal, row_writes

    script = '''
from media_cli.ui.footer import FooterStatus, StatusKind
from media_cli.ui.view_session import UpdateFooter
session = ViewSession(ViewSpec('menu', 'Pick',
    (ViewItem('one', 'One', details=Inherit), ViewItem('two', 'Two', details=Inherit)),
    details=DetailLines(Content('SELECTED TITLE'), Blank, Blank)),
    lambda event: ExitSession(event.item_id))
session.position.selected_id = 'two'
ticks, picker_calls = [], []
started = time.monotonic()
def updates():
    number = min(5, int((time.monotonic() - started) / 0.08))
    if ticks and ticks[-1] == number:
        return ()
    ticks.append(number)
    return (UpdateFooter('menu', session.generation,
        (FooterStatus('FOOTER-PHASE-' + str(number), StatusKind.LOADING, '*'),)),)
def tracked_picker(*args, **kwargs):
    picker_calls.append(True)
    return picker_module.tui_pick(*args, **kwargs)
result = run_view_session(flags, ui, session, poster_manager=manager,
    picker=tracked_picker, updates_fn=updates)
finish(dict(selected=result, picker_calls=len(picker_calls)))
'''
    with Terminal(tmp_path, script) as terminal:
        terminal.until(b'FOOTER-PHASE-1')
        start = terminal.cursor
        terminal.send_after(b'FOOTER-PHASE-5', b'\r')
        writes = row_writes(bytes(terminal.output[start:]))
        assert writes and {row for row, _ in writes} == {35}
        assert terminal.result() == {'selected': 'two', 'picker_calls': 1}
