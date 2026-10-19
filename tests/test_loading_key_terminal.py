import os

import pytest
from test_ui_view_terminal import Terminal


@pytest.mark.parametrize("outcome", ("complete", "cancel", "fail"))
def test_loading_feedback_restores_without_restarting_picker_or_leaking(tmp_path, outcome):
    gate = tmp_path / "finish"
    script = f"""
from pathlib import Path
from media_cli.ui.view_model import Blank, Content, Inherit
from media_cli.ui.view_session import BeginNavigation, CompleteNavigation, FailNavigation
source = ViewSpec('source', 'SOURCE', (ViewItem('one', 'One', details=Inherit),),
    details=DetailLines(Content('Source title'), Content('Source secondary'), Content('Source metadata')),
    context=('SOURCE-CONTEXT',), actions=(ViewAction('refresh', 'Reload', ('CTRL_R',)),))
destination = replace(source, id='destination', prompt='DESTINATION', context=('DESTINATION-CONTEXT',))
session = ViewSession(source)
generation = session.generation
session.apply(BeginNavigation('source', generation, 'request', 'FETCHING-ORIGINAL'))
def finish_request():
    while not Path({str(gate)!r}).exists():
        time.sleep(0.01)
    command = FailNavigation('source', generation, 'request', 'FETCH-FAILED') if {outcome!r} == 'fail' else CompleteNavigation('source', generation, 'request', destination)
    session.post(command)
threading.Thread(target=finish_request, daemon=True).start()
calls = 0
def pick(*args, **kwargs):
    global calls
    calls += 1
    return picker_module.terminal_pick(*args, **kwargs)
run_view_session(flags, ui, session, picker=pick, poster_manager=manager)
finish(dict(calls=calls, pending=session.navigation is not None))
"""
    with Terminal(tmp_path, script, columns=140) as terminal:
        terminal.until(b"FETCHING-ORIGINAL")
        terminal.until(b"\033[35;140H")
        os.write(terminal.master, b"\x12")
        terminal.until(b"Hang on, still loading.")
        terminal.until(b"\033[35;140H")
        terminal.cursor = len(terminal.output)
        terminal.until(b"FETCHING-ORIGINAL")
        if outcome == "cancel":
            terminal.until(b"\033[35;140H")
            terminal.cursor = len(terminal.output)
            os.write(terminal.master, b"\x1b")
            terminal.until(b"SOURCE-CONTEXT")
        gate.touch()
        if outcome == "complete":
            terminal.until(b"DESTINATION-CONTEXT")
        elif outcome == "fail":
            terminal.until(b"FETCH-FAILED")
        os.write(terminal.master, b"\x03")
        assert terminal.result() == {"calls": 2, "pending": False}
