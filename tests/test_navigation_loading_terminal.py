import os
import time

import pytest
from test_ui_view_terminal import Terminal


@pytest.mark.parametrize("cancel", (False, True))
def test_destination_quarantines_prepaint_input_and_can_restore_replaced_source(tmp_path, cancel):
    gate = tmp_path / "finish"
    script = f"""
from pathlib import Path
from media_cli.ui.view_session import BeginNavigation, CompleteNavigation
source = ViewSpec('source', 'SOURCE', (ViewItem('one', 'Artist', details=Inherit),),
    details=DetailLines(Content('Source artist'), Content('Source secondary'), Content('Source metadata')),
    context=('SOURCE-CONTEXT',))
destination = replace(source, id='destination', prompt='DESTINATION', context=('DESTINATION-CONTEXT',))
def handle(event):
    if event.kind == 'select':
        return ExitSession(event.item_id)
session = ViewSession(source, handle)
session.apply(BeginNavigation('source', session.generation, 'request', 'Fetching groups'))
generation = session.generation
def complete():
    while not Path({str(gate)!r}).exists():
        time.sleep(0.01)
    session.post(CompleteNavigation('source', generation, 'request', destination, replace_current=True))
threading.Thread(target=complete, daemon=True).start()
def pick(*args, **kwargs):
    if args[2] == 'DESTINATION':
        terminal_marker('QUEUE-DESTINATION-INPUT')
        time.sleep(0.2)
    return picker_module.terminal_pick(*args, **kwargs)
result = run_view_session(flags, ui, session, picker=pick, poster_manager=manager)
finish(dict(result=result, query=session.position.query, token=session.navigation_token is not None,
            view=session.current.id))
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.until(b"Fetching groups")
        gate.touch()
        terminal.send_after(b"QUEUE-DESTINATION-INPUT", b"\x1b" if cancel else b"\r\x1b[Atyped")
        terminal.send_after(b"SOURCE-CONTEXT" if cancel else b"DESTINATION-CONTEXT", b"\r")
        assert terminal.result() == {
            "result": "one", "query": "", "token": False,
            "view": "source" if cancel else "destination",
        }
        assert b"^[[A" not in terminal.output


def test_source_partial_sequence_keeps_loading_live_until_safe_completion(tmp_path):
    gate, ticks = tmp_path / "finish", tmp_path / "ticks"
    script = f"""
from pathlib import Path
from media_cli.ui.view_session import BeginNavigation, CompleteNavigation
source = ViewSpec('source', 'SOURCE', (ViewItem('one', 'Artist', details=Inherit),),
    details=DetailLines(Content('Source artist'), Blank, Blank))
destination = replace(source, id='destination', prompt='DESTINATION', context=('DESTINATION-READY',))
session = ViewSession(source, lambda event: ExitSession(event.item_id) if event.kind == 'select' else None)
session.apply(BeginNavigation('source', session.generation, 'request', 'Fetching groups'))
generation = session.generation
def complete():
    while not Path({str(gate)!r}).exists():
        time.sleep(0.01)
    session.post(CompleteNavigation('source', generation, 'request', destination))
threading.Thread(target=complete, daemon=True).start()
count = 0
def pick(*args, **kwargs):
    original = kwargs['tick_fn']
    def tick():
        global count
        count += 1
        Path({str(ticks)!r}).write_text(str(count))
        return original()
    kwargs['tick_fn'] = tick
    return picker_module.terminal_pick(*args, **kwargs)
result = run_view_session(flags, ui, session, picker=pick, poster_manager=manager)
finish(dict(result=result, query=session.position.query))
"""

    def count():
        try:
            return int(ticks.read_text())
        except (FileNotFoundError, ValueError):
            return 0

    def wait_ticks(target):
        deadline = time.monotonic() + 2
        while count() < target and time.monotonic() < deadline:
            time.sleep(0.01)
        assert count() >= target, "Loading stopped pumping while a CSI sequence was incomplete"

    with Terminal(tmp_path, script) as terminal:
        terminal.until(b"Fetching groups")
        os.write(terminal.master, b"\x1b[")
        wait_ticks(count() + 4)
        gate.touch()
        wait_ticks(count() + 4)
        assert b"DESTINATION-READY" not in terminal.output
        os.write(terminal.master, b"A\r")
        terminal.send_after(b"DESTINATION-READY", b"\r")
        assert terminal.result() == {"result": "one", "query": ""}
