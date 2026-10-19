import os
import re

import pytest

from test_ui_view_terminal import Terminal, row_writes


@pytest.mark.parametrize('loader', ['with_loading', 'with_anilist_menu_loading'])
def test_fast_loading_does_not_flash_a_status(tmp_path, loader):
    script = f'''
from media_cli.ui import display
if {loader!r} == 'with_loading':
    result = display.with_loading('UNNECESSARY LOADING FLASH', lambda: 42)
else:
    result = display.with_anilist_menu_loading('Watching', 'UNNECESSARY LOADING FLASH', lambda: 42)
display.restore_terminal()
finish(result)
'''
    with Terminal(tmp_path, script) as terminal:
        assert terminal.result() == 42
        assert b'UNNECESSARY LOADING FLASH' not in terminal.output


def test_fast_initial_forward_and_back_screens_do_not_flash_preparing(tmp_path):
    script = '''
from media_cli.app.terminal_controller import run_screen_controller
from media_cli.context import MachineState
from media_cli.ui import display
from media_cli.ui.terminal_runtime import terminal_application

def controller(flags, ui, ms, cfg, args):
    for prompt, header in (('Source', 'SOURCE READY'), ('Destination', 'DESTINATION READY'), ('Back', 'RETURN READY')):
        picker_module.tui_pick(flags, ui, prompt, ['Open'], header=header, return_on_quit=True)
    return 'done'

@terminal_application
def application():
    return run_screen_controller(controller, flags, ui, MachineState(), {}, SimpleNamespace(), fallback='SEARCH')

result = application()
display.restore_terminal()
finish(result)
'''
    with Terminal(tmp_path, script) as terminal:
        for marker in (b'SOURCE READY', b'DESTINATION READY', b'RETURN READY'):
            terminal.until(marker)
            # Time spent using a ready picker is not preparation time.
            import time
            time.sleep(0.22)
            os.write(terminal.master, b'\r')
        assert terminal.result() == 'done'
        assert b'Preparing screen' not in terminal.output


def test_loading_keeps_native_cover_until_the_destination_takes_over(tmp_path):
    script = '''
from media_cli.ui import display, terminal_images

display.enter_alt_screen()
terminal_images.mark_active()
terminal_images.clear_terminal_images = lambda: 'SOURCE_IMAGE_REMOVED'
display.with_loading('Fetching destination', lambda: time.sleep(0.35))
preserved = terminal_images._active
print('PREPARATION COMPLETE', flush=True)
display.restore_terminal()
finish(preserved)
'''
    with Terminal(tmp_path, script) as terminal:
        assert terminal.result() is True
        preparing = bytes(terminal.output).split(b'PREPARATION COMPLETE')[0]
        assert b'Fetching destination' in preparing
        assert b'SOURCE_IMAGE_REMOVED' not in preparing


def test_picker_clears_previous_cover_only_with_its_first_ready_frame(tmp_path):
    script = '''
from media_cli.ui import display, terminal_images

display.enter_alt_screen()
terminal_images.mark_active()
terminal_images.clear_terminal_images = lambda: 'PREVIOUS_COVER_CLEARED'
calls = 0

def header(index):
    global calls
    calls += 1
    if calls == 2:
        time.sleep(0.2)
        print('DESTINATION HEADER PREPARED', flush=True)
    return 'DESTINATION FRAME READY'

finish(picker_module.tui_pick(flags, ui, 'Destination', ['Open'], header_fn=header, return_on_quit=True))
'''
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b'DESTINATION FRAME READY', b'\x03')
        assert terminal.result() == -5
        output = bytes(terminal.output)
        assert output.index(b'DESTINATION HEADER PREPARED') < output.index(b'PREVIOUS_COVER_CLEARED')


def test_loading_cannot_hide_an_identical_destination_footer(tmp_path):
    script = '''
from media_cli.app.terminal_controller import run_screen_controller
from media_cli.context import MachineState
from media_cli.ui.terminal_runtime import terminal_application

def controller(flags, ui, ms, cfg, args):
    header = 'TITLE\\nALTERNATE\\nMETADATA\\nIDENTICAL FOOTER'
    picker_module.tui_pick(flags, ui, 'Source', ['Open'], header=header, return_on_quit=True)
    time.sleep(0.35)
    return picker_module.tui_pick(flags, ui, 'Destination', ['Play'], header=header, return_on_quit=True)

@terminal_application
def application():
    return run_screen_controller(controller, flags, ui, MachineState(), {}, SimpleNamespace(), fallback='SEARCH')
finish(application())
'''
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b'IDENTICAL FOOTER', b'\r')
        terminal.until(b'Preparing screen')
        terminal.until(b'Destination')
        terminal.until(b'\x1b[35;100H')
        footers = [text for row, text in row_writes(bytes(terminal.output)) if row == 35]
        assert 'IDENTICAL FOOTER' in footers[-1]
        os.write(terminal.master, b'\x03')
        assert terminal.result() == -5


@pytest.mark.parametrize('loader', ['with_loading', 'with_anilist_menu_loading'])
@pytest.mark.parametrize('outcome', ['success', 'failure', 'exit'])
def test_loading_never_positions_or_clears_the_normal_shell_buffer(tmp_path, loader, outcome):
    script = f'''
from media_cli.ui import display
from media_cli.ui.transition import call_on_ui_thread

def prepare():
    time.sleep(0.12)
    if {outcome!r} == 'exit':
        call_on_ui_thread(display.exit_alt_screen)
        time.sleep(0.12)
    if {outcome!r} == 'failure':
        raise ValueError('expected failure')
    return 'done'

print('SHELL BEFORE', flush=True)
try:
    if {loader!r} == 'with_loading':
        display.with_loading('Testing loading', prepare)
    else:
        display.with_anilist_menu_loading('Watching', 'Testing loading', prepare)
except ValueError:
    pass
finally:
    display.restore_terminal()
print('SHELL AFTER', flush=True)
finish('done')
'''
    with Terminal(tmp_path, script) as terminal:
        assert terminal.result() == 'done'
        output = bytes(terminal.output)
    active = False
    entries = 0
    for match in re.finditer(rb'\x1b\[\?1049[hl]|\x1b\[[0-9;]*[HJK]|Testing loading', output):
        token = match.group()
        if token == b'\x1b[?1049h':
            active = True
            entries += 1
        elif token == b'\x1b[?1049l':
            active = False
        else:
            assert active, f'UI write leaked into normal buffer: {token!r}'
    assert entries == 1, 'Loading must not reopen the screen after an explicit exit'
    assert b'SHELL BEFORE' in output and b'SHELL AFTER' in output


def test_loading_consumes_keys_and_keeps_echo_disabled_between_helpers(tmp_path):
    script = """
import termios
from media_cli.ui.display import with_loading
from media_cli.ui.terminal_runtime import terminal_application
from media_cli.ui.transition import PreparationCancelled

@terminal_application
def application():
    picker_module.tui_pick(flags, ui, 'Source', ['Result'],
        header='TITLE\\nSECONDARY\\nMETADATA\\nFOOTER', return_on_quit=True)
    with_loading('Fetching episodes', lambda: time.sleep(0.3))
    assert not termios.tcgetattr(0)[3] & termios.ECHO
    with_loading('Preparing details', lambda: time.sleep(0.3))
    return picker_module.tui_pick(flags, ui, 'Destination', ['Play'],
        header='NEXT TITLE\\nNEXT SECONDARY\\nNEXT METADATA\\nNEXT FOOTER', return_on_quit=True)
finish(application())
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b"FOOTER", b"\r")
        terminal.until(b"Fetching episodes")
        os.write(terminal.master, b"\x1b[Atyped\r")
        terminal.until(b"Preparing details")
        os.write(terminal.master, b"\x1b[Bmore\r")
        terminal.until(b"NEXT FOOTER")
        assert b"typed" not in terminal.output
        assert b"more" not in terminal.output
        assert b"^[[A" not in terminal.output
        assert b"^[[B" not in terminal.output
        os.write(terminal.master, b"\x03")
        assert terminal.result() == -5


def test_loading_escape_cancels_before_worker_finishes(tmp_path):
    script = """
from media_cli.ui.display import with_loading
from media_cli.ui.transition import PreparationCancelled
try:
    with_loading('Slow fetch', lambda: time.sleep(30))
except PreparationCancelled:
    finish('cancelled')
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b"Slow fetch", b"\x1b")
        assert terminal.result() == "cancelled"


def test_screen_preparation_keeps_pumping_until_destination_picker(tmp_path):
    script = """
from media_cli.app.terminal_controller import run_screen_controller
from media_cli.ui.terminal_runtime import terminal_application
from media_cli.ui import display
from media_cli.core.terminal import strip_ansi
from media_cli.context import MachineState

display._spinner_style = ('1', '2', '3', '4')
def controller(flags, ui, ms, cfg, args):
    picker_module.tui_pick(flags, ui, 'Source', ['Result'],
        header='TITLE\\nSECONDARY\\nMETADATA\\nFOOTER', return_on_quit=True)
    # This is deliberately outside a with_loading helper: the original gap.
    time.sleep(0.5)
    ui.ui_show_ctx = {'name': 'Prepared'}
    return picker_module.tui_pick(flags, ui, 'Destination', ['Play'],
        header='NEXT TITLE\\nNEXT SECONDARY\\nNEXT METADATA\\nNEXT FOOTER', return_on_quit=True)
@terminal_application
def application():
    return run_screen_controller(controller, flags, ui, MachineState(), {}, SimpleNamespace(), fallback='SEARCH')
finish(application())
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b"FOOTER", b"\r")
        terminal.until(b"Preparing screen")
        start = len(terminal.output)
        os.write(terminal.master, b"\x1b[Atyped\r")
        terminal.until(b"NEXT FOOTER")
        gap = bytes(terminal.output[start:])
        assert gap.count(b"Preparing screen") + gap.count(b"cancel") >= 3
        assert b"typed" not in gap and b"^[[A" not in gap
        os.write(terminal.master, b"\x03")
        assert terminal.result() == -5


def test_cancelled_screen_preparation_restores_source_filter_and_selection(tmp_path):
    script = """
from media_cli.app.terminal_controller import run_screen_controller
from media_cli.ui.terminal_runtime import terminal_application
from media_cli.context import MachineState
def controller(flags, ui, ms, cfg, args):
    result = picker_module.tui_pick(flags, ui, 'Source', ['Alpha', 'Beta'],
        header='SOURCE FOOTER', return_on_quit=True)
    if result == -5:
        return 'QUIT'
    time.sleep(30)
    return 'DESTINATION'
@terminal_application
def application():
    ms, args, cfg = MachineState(), SimpleNamespace(), {}
    first = run_screen_controller(controller, flags, ui, ms, cfg, args, fallback='SEARCH')
    assert first == 'SEARCH'
    second = run_screen_controller(controller, flags, ui, ms, cfg, args, fallback='SEARCH')
    return second, ui.active_picker_query
finish(application())
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b"SOURCE FOOTER", b"be")
        terminal.until(b"1/2")
        os.write(terminal.master, b"\r")
        terminal.until(b"Preparing screen")
        terminal.cursor = len(terminal.output)
        os.write(terminal.master, b"\x1b")
        restored = terminal.until(b"SOURCE FOOTER")
        assert b"2/2" not in restored
        os.write(terminal.master, b"\x03")
        assert terminal.result() == ["QUIT", "be"]


def test_cancelled_tab_task_stays_in_current_picker(tmp_path):
    script = """
from media_cli.ui.display import with_loading
def tab(*args, **kwargs):
    with_loading('Switching translation', lambda: time.sleep(30))
    raise AssertionError('cancelled task accepted')
finish(picker_module.tui_pick(flags, ui, 'Details', ['Play', 'Episodes'],
    header='DETAILS FOOTER', tab_fn=tab, return_on_quit=True))
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b"DETAILS FOOTER", b"\t")
        terminal.send_after(b"Switching translation", b"\x1b")
        terminal.until(b"DETAILS FOOTER")
        os.write(terminal.master, b"\x03")
        assert terminal.result() == -5
