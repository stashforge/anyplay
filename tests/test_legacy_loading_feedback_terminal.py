import os

import pytest
from test_ui_view_terminal import Terminal


@pytest.mark.parametrize("key", [b"\r", b"\x1b[C"])
def test_repeated_selection_during_preparation_is_not_replayed(tmp_path, key):
    gate = tmp_path / "finish"
    script = f"""
from pathlib import Path
from media_cli.ui import display
from media_cli.ui.terminal_runtime import terminal_application
def work():
    while not Path({str(gate)!r}).exists():
        time.sleep(0.01)
@terminal_application
def application():
    display.with_loading('GETTING-READY', work)
    return picker_module.tui_pick(flags, ui, 'Destination', ['One', 'Two'],
        header='Title\\nSecondary\\nMetadata\\nDESTINATION', return_on_quit=True)
finish(application())
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b"GETTING-READY", key)
        terminal.until(b"selection")
        gate.touch()
        terminal.until(b"DESTINATION")
        os.write(terminal.master, b"\x1b")
        assert terminal.result() == -2


@pytest.mark.parametrize("loader", ("with_loading", "with_anilist_menu_loading"))
@pytest.mark.parametrize("outcome", ("complete", "cancel", "quit"))
def test_common_loaders_share_feedback_and_keep_input_owned(tmp_path, loader, outcome):
    finish_gate, update_gate = tmp_path / "finish", tmp_path / "update"
    script = f"""
from pathlib import Path
from media_cli.ui import display
from media_cli.ui.terminal_runtime import terminal_application
from media_cli.ui.transition import PreparationCancelled, set_preparation_status
@terminal_application
def application():
    picker_module.tui_pick(flags, ui, 'SOURCE', ['Title'],
        header='Source title\\nSecondary\\nMetadata\\nSOURCE-FOOTER', return_on_quit=True)
    def prepare():
        while not Path({str(finish_gate)!r}).exists():
            if Path({str(update_gate)!r}).exists():
                set_preparation_status('LEGACY-NEWEST')
            time.sleep(0.01)
        return 42
    try:
        if {loader!r} == 'with_loading':
            value = display.with_loading('LEGACY-FETCH', prepare)
        else:
            value = display.with_anilist_menu_loading('Watching', 'LEGACY-FETCH', prepare)
    except PreparationCancelled:
        return 'cancel'
    except KeyboardInterrupt:
        return 'quit'
    assert value == 42
    return picker_module.tui_pick(flags, ui, 'DESTINATION', ['Play'],
        header='Next title\\nSecondary\\nMetadata\\nDESTINATION-FOOTER', return_on_quit=True)
finish(application())
"""
    with Terminal(tmp_path, script, columns=140) as terminal:
        terminal.send_after(b"SOURCE-FOOTER", b"\r")
        terminal.send_after(b"LEGACY-FETCH", b"\x1b[A")
        terminal.until(b"cancel")
        if outcome == "complete":
            terminal.cursor = len(terminal.output)
            update_gate.touch()
            terminal.until(b"LEGACY-NEWEST")
            finish_gate.touch()
            terminal.until(b"DESTINATION-FOOTER")
            os.write(terminal.master, b"\x03")
            assert terminal.result() == -5
        else:
            os.write(terminal.master, b"\x1b" if outcome == "cancel" else b"\x03")
            assert terminal.result() == outcome
        assert b"^[[A" not in terminal.output


@pytest.mark.parametrize("loader", ("with_loading", "with_anilist_menu_loading"))
def test_common_loaders_rotate_supplied_choices_and_restore_real_progress(tmp_path, loader):
    gate = tmp_path / "finish"
    script = f"""
from pathlib import Path
from media_cli.ui import display, message_choices
from media_cli.ui.terminal_runtime import terminal_application
from media_cli.ui.transition import set_preparation_status
message_choices._DEFAULT_PICKER = message_choices.MessagePicker(choose=lambda options: options[0])
message_choices._rotation_interval = lambda: 3.0

def work(status_cb):
    while not Path({str(gate)!r}).exists():
        time.sleep(0.01)
    status_cb('REAL-PROGRESS')
    time.sleep(1.2)
    return 42

@terminal_application
def application():
    messages = ('FETCH-FIRST', 'FETCH-SECOND')
    if {loader!r} == 'with_loading':
        return display.with_loading(messages, work)
    return display.with_anilist_menu_loading('Watching', messages, work)

finish(application())
"""
    with Terminal(tmp_path, script, columns=140) as terminal:
        terminal.until(b"FETCH-FIRST")
        terminal.until(b"FETCH-SECOND")
        terminal.send_after(b"FETCH-SECOND", b"\x1b[A")
        terminal.until(b"cancel")
        gate.touch()
        terminal.until(b"REAL-PROGRESS")
        assert terminal.result() == 42
        assert b"^[[A" not in terminal.output
