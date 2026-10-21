"""Diagnose blank loading handoffs without logging media or changing rendering."""

from test_ui_view_terminal import Terminal


def test_loading_diagnostics_distinguish_initial_and_retained_frames(tmp_path):
    logfile = tmp_path / "handoff.log"
    script = f'''
import logging
logging.basicConfig(filename={str(logfile)!r}, level=logging.DEBUG)
from media_cli.ui import display
from media_cli.ui.terminal_runtime import terminal_application

@terminal_application
def application():
    display.with_loading('INITIAL WAIT', lambda: time.sleep(0.3))
    picker_module.tui_pick(flags, ui, 'Source', ['Open'],
        header='SOURCE CONTENT', return_on_quit=True)
    display.with_loading('SLOW HANDOFF', lambda: time.sleep(0.3))
    picker_module.tui_pick(flags, ui, 'Destination', ['Back'],
        header='DESTINATION CONTENT', return_on_quit=True)
    display.exit_alt_screen()

application()
finish('done')
'''
    with Terminal(tmp_path, script, rows=12) as terminal:
        terminal.send_after(b"SOURCE CONTENT", b"\r")
        terminal.until(b"SLOW HANDOFF")
        between = bytes(terminal.output).split(b"SOURCE CONTENT", 1)[1]
        assert b"\033[2J" not in between
        assert b"\033[?1049l" not in between
        terminal.send_after(b"DESTINATION CONTENT", b"\x1b")
        assert terminal.result() == "done"
    logs = logfile.read_text()
    paints = [line for line in logs.splitlines() if "Loading first paint:" in line]
    assert len(paints) == 2
    assert "cached_rows=0" in paints[0]
    assert "cached_rows=12" in paints[1]
    assert "alt_screen=True" in paints[1]
    assert "Alternate screen entered: cleared=True" in logs
    assert "Alternate screen exited" in logs
    assert "Picker frame cache invalidated: rows=12" in logs
    assert "SOURCE CONTENT" not in logs
    assert "SLOW HANDOFF" not in logs
