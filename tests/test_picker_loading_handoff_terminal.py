"""Native lifecycle hooks exercised directly, without a view session."""

import os

import pytest
from test_ui_view_terminal import Terminal


def test_normal_frame_acknowledged_once_after_write_before_ordinary_input(tmp_path):
    script = """
from media_cli.ui.terminal_runtime import terminal_application
acks = 0
def acknowledge():
    global acks
    acks += 1
    terminal_marker('ACKNOWLEDGED')
@terminal_application
def application():
    # Queue ordinary input in raw mode before entering the picker.
    from media_cli.ui.terminal_runtime import borrow_terminal
    with borrow_terminal() as runtime:
        runtime.ensure_raw()
    terminal_marker('QUEUE INPUT')
    time.sleep(0.15)
    return picker_module.terminal_pick(flags, ui, 'Content', ['Visible item'],
        frame_ready_fn=acknowledge, return_on_quit=True)
value = application()
finish([value, acks])
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b"QUEUE INPUT", b"\r")
        assert terminal.result() == [0, 1]
        output = bytes(terminal.output)
        assert output.index(b"Visible item") < output.index(b"ACKNOWLEDGED")
        assert output.index(b"\x1b[35;100H") < output.index(b"ACKNOWLEDGED")


def test_width_notice_and_help_do_not_acknowledge_a_frame(tmp_path):
    script = """
acks = 0
def acknowledge():
    global acks
    acks += 1
    terminal_marker('ACKNOWLEDGED')
value = picker_module.tui_pick(flags, ui, 'Content', ['Visible item'],
    frame_ready_fn=acknowledge, help_dict={'?': 'Help'}, return_on_quit=True)
finish([value, acks])
"""
    with Terminal(tmp_path, script, columns=20) as terminal:
        terminal.until(b"\x1b[35;20H")
        assert b"ACKNOWLEDGED" not in terminal.output
        terminal.resize(35, 100)
        terminal.send_after(b"ACKNOWLEDGED", b"?")
        terminal.send_after(b"Keyboard Shortcuts", b"\x1b")
        terminal.send_after(b"Visible item", b"\x03")
        assert terminal.result() == [-5, 1]


def test_completed_blocked_controls_are_consumed_before_live_transition(tmp_path):
    script = """
from media_cli.ui.input_result import KeyResult
events = []
pending = False
def key(key, index):
    global pending
    events.append(key)
    if key == 'TAB':
        pending = True
        terminal_marker('BLOCKED')
    return KeyResult.CONSUMED
def live(query):
    if pending and 'UP' in events and 'ENTER' in events:
        events.append('LIVE')
    return ['Source item'], 'SOURCE FRAME', True
def exiting():
    return 'LIVE' in events
value = picker_module.tui_pick(flags, ui, 'Source', ['Source item'],
    live_fn=live, key_fn=key, exit_fn=exiting,
    input_pending_fn=lambda: pending, frame_ready_fn=lambda: None,
    structured=True, return_on_quit=True)
finish([value, events])
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b"SOURCE FRAME", b"\t")
        terminal.send_after(b"BLOCKED", b"\x1b[A\r")
        assert terminal.result() == [-6, ["TAB", "UP", "ENTER", "LIVE"]]


@pytest.mark.parametrize("pending_from_live", [False, True])
def test_pending_destination_drains_queued_keys_before_acknowledgement(tmp_path, pending_from_live):
    script = f"""
from media_cli.ui.input_result import KeyResult
from media_cli.ui.terminal_runtime import terminal_application, borrow_terminal
pending = {not pending_from_live!r}
events = []
def live(query):
    global pending
    if 'ACK' not in events:
        pending = True
    return ['Destination item'], '', True
def key(key, index):
    events.append(key)
    return KeyResult.CONSUMED if pending else False
def acknowledge():
    global pending
    events.append('ACK')
    pending = False
    terminal_marker('ACKNOWLEDGED')
@terminal_application
def application():
    with borrow_terminal() as runtime:
        runtime.ensure_raw()
    terminal_marker('QUEUE INPUT')
    time.sleep(0.15)
    return picker_module.terminal_pick(flags, ui, 'Destination', ['Destination item'],
        input_pending_fn=lambda: pending, frame_ready_fn=acknowledge,
        live_fn=live if {pending_from_live!r} else None,
        key_fn=key, structured=True, return_on_quit=True)
value = application()
finish([value, events, ui.active_picker_query])
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b"QUEUE INPUT", b"\x1b[Atyped\r")
        terminal.send_after(b"ACKNOWLEDGED", b"\r")
        assert terminal.result() == [0, ["UP", "t", "y", "p", "e", "d", "ENTER", "ACK", "ENTER"], ""]


def test_partial_csi_keeps_ticking_and_cannot_restart_on_destination(tmp_path):
    gate = tmp_path / "complete"
    script = f"""
from pathlib import Path
from media_cli.ui.input_result import KeyResult
pending = False
events = []
ticks = 0
def key(key, index):
    global pending
    events.append(key)
    if key == 'TAB':
        pending = True
        terminal_marker('BLOCKED')
    return KeyResult.CONSUMED
def live(query):
    if Path({str(gate)!r}).exists():
        events.append('LIVE')
    return ['Source item'], 'SOURCE FRAME', True
def tick():
    global ticks
    ticks += 1
    if ticks % 4 == 0:
        terminal_marker('TICK')
    return True
value = picker_module.tui_pick(flags, ui, 'Source', ['Source item'],
    live_fn=live, key_fn=key, exit_fn=lambda: 'LIVE' in events,
    input_pending_fn=lambda: pending, frame_ready_fn=lambda: None,
    tick_fn=tick, structured=True, return_on_quit=True)
source = list(events)
value2 = picker_module.tui_pick(flags, ui, 'Destination', ['Destination item'],
    return_on_quit=True, frame_ready_fn=lambda: terminal_marker('DESTINATION ACK'))
finish([value, source, value2, ui.active_picker_query])
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b"SOURCE FRAME", b"\t")
        terminal.send_after(b"BLOCKED", b"\x1b[")
        # The source observes the prefix before completion becomes available.
        terminal.until(b"TICK")
        gate.touch()
        terminal.until(b"TICK")
        terminal.until(b"TICK")
        assert b"DESTINATION ACK" not in terminal.output
        os.write(terminal.master, b"A")
        terminal.send_after(b"DESTINATION ACK", b"\r")
        assert terminal.result() == [-6, ["TAB", "UP", "LIVE"], 0, ""]


@pytest.mark.parametrize("restart", [False, True])
def test_partial_destination_input_defers_acknowledgement_and_key_restart(tmp_path, restart):
    script = f"""
from media_cli.ui.input_result import KeyResult
from media_cli.ui.terminal_runtime import terminal_application, borrow_terminal
pending = True
events = []
ticks = 0
def key(key, index):
    events.append(key)
    return True if {restart!r} and key == 'ENTER' else KeyResult.CONSUMED
def acknowledge():
    global pending
    events.append('ACK')
    pending = False
    terminal_marker('ACKNOWLEDGED')
def tick():
    global ticks
    ticks += 1
    if ticks % 4 == 0:
        terminal_marker('TICK')
    return True
@terminal_application
def application():
    with borrow_terminal() as runtime:
        runtime.ensure_raw()
    terminal_marker('QUEUE INPUT')
    time.sleep(0.15)
    return picker_module.terminal_pick(flags, ui, 'Destination', ['Destination item'],
        input_pending_fn=lambda: pending, frame_ready_fn=acknowledge,
        key_fn=key, tick_fn=tick, structured=True, return_on_quit=True)
value = application()
finish([value, events])
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b"QUEUE INPUT", b"\r\x1b[")
        terminal.until(b"TICK")
        terminal.until(b"TICK")
        assert b"ACKNOWLEDGED" not in terminal.output
        os.write(terminal.master, b"A")
        if restart:
            assert terminal.result() == [-6, ["ENTER", "UP"]]
        else:
            terminal.send_after(b"ACKNOWLEDGED", b"\x03")
            assert terminal.result() == [-5, ["ENTER", "UP", "ACK"]]


@pytest.mark.parametrize("key,expected", [(b"\x1b", -2), (b"\x1b[D", -2), (b"\x03", -5)])
def test_pending_cancel_and_interrupt_preserve_picker_results(tmp_path, key, expected):
    script = """
value = picker_module.tui_pick(flags, ui, 'Source', ['Source item'],
    header='PENDING FRAME', input_pending_fn=lambda: True,
    structured=True, return_on_quit=True)
finish(value)
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b"PENDING FRAME", key)
        assert terminal.result() == expected
