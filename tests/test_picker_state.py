"""Selection policy and position restoration at the existing picker boundary."""

import errno
import fcntl
import json
import os
import pty
import select
import struct
import subprocess
import sys
import termios
import time
from types import SimpleNamespace

import pytest

from media_cli.ui.plain_picker import plain_pick


def plain(choices, options=None, **kwargs):
    answers = iter(choices)
    output = []
    result = plain_pick(
        SimpleNamespace(), SimpleNamespace(), "Choose",
        options if options is not None else ["Heading", "Disabled", "Alpha", "Beta"],
        input_fn=lambda prompt: next(answers), output_fn=output.append, **kwargs,
    )
    return result, "\n".join(output)


def test_plain_numbers_only_selectable_rows():
    result, output = plain(["2"], heading_indices={0}, disabled_indices={1})
    assert result == 3
    assert "[1] Alpha" in output
    assert "[2] Beta" in output
    assert "[1] Heading" not in output
    assert "[2] Disabled" not in output
    assert "Disabled (disabled)" in output


def test_plain_multi_select_maps_numbers_and_checks_selection():
    seen = []
    result, _ = plain(
        ["1-2", "1"], heading_indices={0}, disabled_indices={1}, multi_select=True,
        select_fn=lambda idx: seen.append(idx) or idx != 3,
    )
    assert result == [2]
    assert seen == [2, 3, 2]


@pytest.mark.parametrize("choice", ["0-2", "1-9", "0", "9"])
def test_plain_multi_select_rejects_entire_invalid_range(choice):
    result, _ = plain([choice, "b"], multi_select=True)
    assert result == -3


def test_plain_auto_select_evaluates_callable_and_eligibility():
    result, _ = plain(["b"], ["Only"], auto_select_single_when_done=lambda: False)
    assert result == -3
    result, _ = plain([], ["Heading", "Only"], heading_indices={0}, auto_select_single_when_done=True)
    assert result == 1
    result, _ = plain(["b"], ["Disabled"], disabled_indices={0}, auto_select_single_when_done=True)
    assert result == -3


@pytest.mark.parametrize("signal", ["q", EOFError, KeyboardInterrupt])
@pytest.mark.parametrize("query_mode", [False, True])
def test_plain_structured_quit(signal, query_mode):
    def answer(prompt):
        if isinstance(signal, str):
            return signal
        raise signal

    result = plain_pick(
        None, None, "Choose", ["Only"], input_fn=answer, output_fn=lambda line: None,
        return_on_quit=True, return_query_on_enter=query_mode,
    )
    assert result == -5


def test_plain_legacy_quit_still_exits():
    with pytest.raises(SystemExit) as exc:
        plain(["q"])
    assert exc.value.code == 0


def test_state_defaults_and_plain_position():
    from media_cli.ui.picker_state import PickerState

    first, second = PickerState(), PickerState()
    first.marked_indices.add(2)
    assert second.marked_indices == set()
    state = PickerState(query="beta", selected_index=99, scroll=99, marked_indices={0, 3, 99})
    result, output = plain(["1"], state=state, heading_indices={0})
    assert result == 3
    assert "[1] Beta" in output
    assert "[2]" not in output
    assert state.selected_index == 3
    assert state.scroll == 0
    assert state.query == "beta"
    assert state.marked_indices == {3}


def test_plain_query_state_and_back():
    from media_cli.ui.picker_state import PickerState

    state = PickerState(query="previous")
    result, _ = plain(["updated"], state=state, return_query_on_enter=True)
    assert result == state.query == "updated"
    result, _ = plain(["b"], ["updated"], state=state)
    assert result == -3
    assert state.query == "updated"


def test_plain_structured_header_and_actions_preserve_supplied_lines():
    header = "Title\n\nLoading...\nLeft/Esc=back"
    _, output = plain(["b"], ["Only"], header=header, structured=True,
                      action_labels=["[b] Back", "[x] Custom"])
    assert header in output
    assert "Actions: [b] Back, [x] Custom" in output
    assert "[q] Quit" not in output
    _, output = plain(["b"], ["Only"], header=header)
    assert "Loading..." not in output
    assert "Left/Esc=back" not in output


def test_plain_empty_action_labels_suppresses_action_hint():
    _, output = plain(["b"], ["Only"], structured=True, action_labels=[])
    assert "Actions:" not in output


def test_tui_plain_fallback_propagates_new_parameters(monkeypatch):
    from media_cli.ui.picker import tui_pick
    from media_cli.ui.picker_state import PickerState
    import media_cli.ui.plain_picker as module

    received = {}

    def capture(*args, **kwargs):
        received.update(kwargs)
        return -5

    monkeypatch.setattr(module, "plain_pick", capture)
    state = PickerState()
    assert tui_pick(SimpleNamespace(plain_mode=True), None, "Pick", ["Heading"],
                    state=state, heading_indices={0}, return_on_quit=True,
                    structured=True, action_labels=["Back"]) == -5
    assert received["state"] is state
    assert received["heading_indices"] == {0}
    assert received["return_on_quit"] is True
    assert received["structured"] is True
    assert received["action_labels"] == ["Back"]


def run_terminal(script, steps):
    """Drive the real picker in a child with its own controlling terminal."""
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 14, 80, 0, 0))

    def own_terminal():
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)

    prelude = """
import json
from dataclasses import asdict
from types import SimpleNamespace
from media_cli.ui.picker import tui_pick
from media_cli.ui.picker_state import PickerState
flags = SimpleNamespace(plain_mode=False, show_image=False)
ui = SimpleNamespace(active_picker_query='', hovered_show_id=None)
def pick(prompt, options, **kwargs):
    return tui_pick(flags, ui, prompt, options, reverse_items=False, **kwargs)
"""
    proc = subprocess.Popen(
        [sys.executable, "-c", prelude + script], stdin=slave, stdout=slave, stderr=slave,
        preexec_fn=own_terminal, close_fds=True,
    )
    os.close(slave)
    output = bytearray()
    deadline = time.monotonic() + 8

    def until(marker):
        while marker not in output:
            if time.monotonic() > deadline:
                pytest.fail(f"Timed out waiting for {marker!r}: {bytes(output)!r}")
            if select.select([master], [], [], 0.05)[0]:
                try:
                    data = os.read(master, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        pytest.fail(f"Terminal closed before {marker!r}: {bytes(output)!r}")
                    raise
                if not data:
                    pytest.fail(f"EOF before {marker!r}: {bytes(output)!r}")
                output.extend(data)

    try:
        for marker, keys in steps:
            until(marker)
            os.write(master, keys)
        until(b"RESULT:")
        until(b"END_RESULT")
        proc.wait(timeout=2)
        assert proc.returncode == 0, bytes(output)
        payload = bytes(output).split(b"RESULT:", 1)[1].split(b"END_RESULT", 1)[0]
        return json.loads(payload), bytes(output)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)
        os.close(master)


def test_terminal_restores_underlying_filtered_selection_marks_and_scroll():
    result, _ = run_terminal("""
state = PickerState(query='item', selected_index=14, scroll=10, marked_indices={12, 14, 99})
options = ['Other'] + [f'item {i:02}' for i in range(20)]
first = pick('First', options, state=state, multi_select=True)
saved = asdict(state)
second = pick('Second', options, state=state, multi_select=True)
print('RESULT:' + json.dumps([first, saved, second, asdict(state)], default=sorted) + 'END_RESULT', flush=True)
""", [(b"First", b"\x1b[B\x1b[H\x1b[D\x1b[D"), (b"Second", b"\r")])
    first, saved, second, restored = result
    assert first == -3
    assert saved["query"] == "item"
    assert saved["selected_index"] == 15
    assert saved["scroll"] > 0
    assert saved["marked_indices"] == [12, 14]
    assert second == [12, 14]
    assert restored == saved


def test_terminal_back_and_changed_list_clamp_state():
    result, _ = run_terminal("""
state = PickerState(selected_index=99, scroll=99, marked_indices={0, 1, 99})
first = pick('Before', ['Heading', 'Enabled'], state=state, heading_indices={0})
saved = asdict(state)
second = pick('After', ['Heading', 'Disabled', 'Enabled'], state=state,
              heading_indices={0}, disabled_indices={1})
print('RESULT:' + json.dumps([first, saved, second, asdict(state)], default=sorted) + 'END_RESULT', flush=True)
""", [(b"Before", b"\x1b[D"), (b"After", b"\r")])
    assert result[0] == -3
    assert result[1]["selected_index"] == 1
    assert result[1]["marked_indices"] == [1]
    assert result[2] == result[3]["selected_index"] == 2
    assert result[3]["marked_indices"] == []


def test_terminal_structured_quit():
    result, _ = run_terminal("""
state = PickerState(query='item')
value = pick('QuitTest', ['item'], state=state, return_on_quit=True)
print('RESULT:' + json.dumps([value, state.query]) + 'END_RESULT', flush=True)
""", [(b"QuitTest", b"\x03")])
    assert result == [-5, "item"]


def test_terminal_multi_selection_honors_can_select():
    result, _ = run_terminal("""
state = PickerState(marked_indices={0, 1})
seen = []
value = pick('MultiTest', ['A', 'B'], state=state, multi_select=True,
             select_fn=lambda index: seen.append(index) or index != 1,
             header_fn=lambda index: 'Rejected' if 1 in seen else '')
print('RESULT:' + json.dumps(value) + 'END_RESULT', flush=True)
""", [(b"MultiTest", b"\r"), (b"Rejected", b"\x1b")])
    assert result == -2


def test_plain_filter_and_action_hooks_use_original_indexes():
    from media_cli.ui.picker_state import PickerState

    state = PickerState(query="b", selected_index=3)
    seen = []
    result, output = plain(
        ["custom"], state=state, heading_indices={0},
        filter_fn=lambda query, options: [0, 3] if query == "b" else [],
        key_fn=lambda key, index: seen.append((key, index)) or key == "custom",
    )
    assert result == -6
    assert seen == [("custom", 3)]
    assert "Heading" in output and "[1] Beta" in output
    assert state.selected_index == 3


def test_plain_exit_hook_precedes_live_auto_select():
    done = []
    result, _ = plain(
        [], [], live_fn=lambda query: done.append(True) or (["Only"], "", True),
        exit_fn=lambda: bool(done), auto_select_single_when_done=True,
        select_fn=lambda index: pytest.fail("transition must precede selection"),
    )
    assert result == -6


def test_terminal_filter_and_key_hooks_restore_original_index():
    result, _ = run_terminal("""
state = PickerState(query='match', selected_index=2)
seen = []
def action(key, index):
    seen.append([key, index])
    return key == 'CTRL_O'
value = pick('HookTest', ['Heading', 'Other', 'match'], state=state, heading_indices={0},
             filter_fn=lambda query, options: [0, 2], key_fn=action)
print('RESULT:' + json.dumps([value, state.selected_index, seen]) + 'END_RESULT', flush=True)
""", [(b"HookTest", b"\x0f")])
    assert result == [-6, 2, [["CTRL_O", 2]]]


def test_terminal_live_exit_precedes_auto_select():
    result, _ = run_terminal("""
done = []
value = pick('ExitTest', [], live_fn=lambda query: done.append(True) or (['Only'], '', True),
             exit_fn=lambda: bool(done), auto_select_single_when_done=True,
             select_fn=lambda index: (_ for _ in ()).throw(AssertionError('must not select')))
print('RESULT:' + json.dumps(value) + 'END_RESULT', flush=True)
""", [])
    assert result == -6


def test_terminal_legacy_printable_q_remains_query_input():
    result, _ = run_terminal("""
value = pick('QueryTest', ['Only'], return_query_on_enter=True)
print('RESULT:' + json.dumps(value) + 'END_RESULT', flush=True)
""", [(b"QueryTest", b"q\r")])
    assert result == "q"


def test_terminal_structured_printable_q_filters_options():
    result, _ = run_terminal("""
state = PickerState()
value = pick('PrintableTest', ['Other', 'Quartz'], state=state, return_on_quit=True)
print('RESULT:' + json.dumps([value, state.query, state.selected_index]) + 'END_RESULT', flush=True)
""", [(b"PrintableTest", b"q\r")])
    assert result == [1, "q", 1]


@pytest.mark.parametrize("structured", [False, True])
def test_terminal_buffered_filter_survives_structured_restart(structured):
    result, _ = run_terminal(f"""
import termios
original_attrs = termios.tcgetattr(0)
state = PickerState()
first = pick('RestartBefore', ['Other', 'Quartz'], state=state, structured={structured!r},
             key_fn=lambda key, index: key == 'CTRL_O')
restored_between = termios.tcgetattr(0) == original_attrs
second = pick('RestartAfter', ['Other', 'Quartz'], state=state, structured={structured!r})
restored_after = termios.tcgetattr(0) == original_attrs
print('RESULT:' + json.dumps([first, second, state.query, restored_between, restored_after]) + 'END_RESULT', flush=True)
""", [(b"RestartBefore", b"\x0fquartz"), (b"RestartAfter", b"\r")])
    assert result == [-6, 1, "quartz", True, True]


def test_terminal_structured_callback_exception_restores_terminal():
    result, _ = run_terminal("""
import termios
original_attrs = termios.tcgetattr(0)
state = PickerState()
def fail(key, index):
    raise RuntimeError('callback failed')
try:
    pick('RestoreOnError', ['Only'], state=state, structured=True, key_fn=fail)
except RuntimeError as error:
    value = str(error)
print('RESULT:' + json.dumps([value, termios.tcgetattr(0) == original_attrs]) + 'END_RESULT', flush=True)
""", [(b"RestoreOnError", b"x")])
    assert result == ["callback failed", True]


@pytest.mark.parametrize("decoder", ["lambda fd: ''", "lambda fd: (_ for _ in ()).throw(EOFError())"])
def test_terminal_structured_eof(decoder):
    result, _ = run_terminal(f"""
import media_cli.ui.picker as module
module._get_key = {decoder}
value = pick('EOFTest', ['Only'], return_on_quit=True)
print('RESULT:' + json.dumps(value) + 'END_RESULT', flush=True)
""", [(b"EOFTest", b"x")])
    assert result == -5


def test_terminal_rejected_auto_select_remains_responsive():
    result, _ = run_terminal("""
value = pick('AutoTest', ['Only'], live_fn=lambda query: (['Only'], '', True),
             auto_select_single_when_done=True, select_fn=lambda index: False,
             return_on_quit=True)
print('RESULT:' + json.dumps(value) + 'END_RESULT', flush=True)
""", [(b"AutoTest", b"\x03")])
    assert result == -5


def test_terminal_filter_excluding_single_live_result_does_not_auto_select():
    result, _ = run_terminal("""
state = PickerState(query='missing')
value = pick('EmptyFilterTest', ['Only'], state=state,
             live_fn=lambda query: (['Only'], '', True), auto_select_single_when_done=True)
print('RESULT:' + json.dumps([value, state.query]) + 'END_RESULT', flush=True)
""", [(b"EmptyFilterTest", b"\x1b")])
    assert result == [-2, "missing"]


def test_plain_filtering_multiple_results_to_one_requires_confirmation():
    from media_cli.ui.picker_state import PickerState

    result, output = plain(
        ["b"], ["Alpha", "Beta"], state=PickerState(query="Alpha"),
        auto_select_single_when_done=True,
    )
    assert result == -3
    assert "Automatically selecting" not in output


def test_terminal_filtering_multiple_live_results_to_one_does_not_auto_select():
    result, _ = run_terminal("""
options = ['Alpha', 'Beta']
value = pick('LiveFilter', options, live_fn=lambda query: (options, '', True),
             auto_select_single_when_done=True, return_on_quit=True)
print('RESULT:' + json.dumps(value) + 'END_RESULT', flush=True)
""", [(b"LiveFilter", b"Alpha"), (b"1/2", b"\x03")])
    assert result == -5


def test_terminal_restored_filter_does_not_make_multiple_results_auto_select():
    result, _ = run_terminal("""
state = PickerState(query='Alpha')
options = ['Alpha', 'Beta']
value = pick('RestoredFilter', options, state=state, live_fn=lambda query: (options, '', True),
             auto_select_single_when_done=True, return_on_quit=True)
print('RESULT:' + json.dumps(value) + 'END_RESULT', flush=True)
""", [(b"RestoredFilter", b"\x03")])
    assert result == -5


def test_terminal_actual_single_selectable_result_still_auto_selects():
    result, _ = run_terminal("""
options = ['Heading', 'Only']
value = pick('TrueSingle', options, heading_indices={0},
             live_fn=lambda query: (options, '', True), auto_select_single_when_done=True)
print('RESULT:' + json.dumps(value) + 'END_RESULT', flush=True)
""", [])
    assert result == 1


def test_plain_terminal_async_transition_without_keyboard_input():
    result, output = run_terminal("""
import threading
import time
flags.plain_mode = True
ready = threading.Event()
transition = []
polls = []
def live(query):
    polls.append(query)
    if ready.is_set():
        transition.append(True)
    return ['Loading...'], 'Waiting for results', False
worker = threading.Thread(target=lambda: (time.sleep(0.3), ready.set()))
worker.start()
value = pick('AsyncPlain', ['Loading...'], structured=True, disabled_indices={0},
             heading_indices={0}, live_fn=live, exit_fn=lambda: bool(transition),
             return_on_quit=True, action_labels=['[b] Back', '[q] Quit'])
worker.join()
print('RESULT:' + json.dumps([value, len(polls)]) + 'END_RESULT', flush=True)
""", [])
    assert result[0] == -6
    assert result[1] >= 2
    assert b"[b] Back" in output and b"[q] Quit" in output
    assert b"[1] Loading" not in output


def test_plain_terminal_adapter_reopens_after_async_loading_update():
    result, output = run_terminal("""
import threading
import time
from media_cli.ui.poster import PosterManager
from media_cli.ui.view_model import Blank, DetailLines, Inherit, ViewItem, ViewSpec
from media_cli.ui.view_picker import run_view_session
from media_cli.ui.view_session import ExitSession, UpdateItems, UpdateStatus, ViewSession
flags.plain_mode = True
session = ViewSession(ViewSpec('root', 'AsyncAdapter', state='loading', details=DetailLines(Blank, Blank, Blank)),
                      lambda event: ExitSession(event.item_id))
manager = PosterManager(enabled=lambda: False, cache_dir=lambda: '/tmp',
                        hovered_show_id=lambda: None, request_redraw=lambda: None,
                        loading_frame=lambda: '*')
def update():
    time.sleep(0.3)
    session.post(UpdateItems('root', session.generation, (ViewItem('arrived', 'Arrived', details=Inherit),), replace_all=True))
    session.post(UpdateStatus('root', session.generation, 'ready'))
worker = threading.Thread(target=update)
worker.start()
value = run_view_session(flags, ui, session, poster_manager=manager)
worker.join()
print('RESULT:' + json.dumps(value) + 'END_RESULT', flush=True)
""", [(b"Arrived", b"1\n")])
    assert result == "arrived"
    assert b"Loading..." in output


@pytest.mark.parametrize("options", [[], ["Loading..."]])
@pytest.mark.parametrize("keys, expected", [(b"b\n", -3), (b"q\n", -5), (b"\x03", -5), (b"\x04", -5)])
def test_plain_terminal_loading_remains_navigable(options, keys, expected):
    result, _ = run_terminal(f"""
flags.plain_mode = True
value = pick('LoadingPlain', {options!r}, structured=True, disabled_indices={{0}},
             live_fn=lambda query: ({options!r}, '', False), return_on_quit=True,
             action_labels=['[b] Back', '[q] Quit'])
print('RESULT:' + json.dumps(value) + 'END_RESULT', flush=True)
""", [(b"Enter choice:", keys)])
    assert result == expected


def test_plain_injected_input_bypasses_terminal_polling(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(select, "select", lambda *args: pytest.fail("injected input must not poll"))
    result, _ = plain(["1"], ["Only"], structured=True,
                      live_fn=lambda query: (["Only"], "", False))
    assert result == 0


@pytest.mark.parametrize("modes", [(False, True), (True, False), (False, False), (True, True)])
def test_plain_piped_input_remains_consumable_across_modes(modes):
    script = f"""
import json
import sys
from media_cli.ui.plain_picker import plain_pick
results = [plain_pick(None, None, 'Pipe', ['A', 'B'], structured=mode, return_on_quit=True,
                     live_fn=lambda query: (['A', 'B'], '', False)) for mode in {modes!r}]
remaining = sys.stdin.readline()
print('RESULT:' + json.dumps([results, remaining]) + 'END_RESULT')
"""
    proc = subprocess.run([sys.executable, "-c", script], input="1\n2\nfuture\n", capture_output=True,
                          text=True, timeout=3)
    assert proc.returncode == 0, proc.stderr
    payload = proc.stdout.split("RESULT:", 1)[1].split("END_RESULT", 1)[0]
    assert json.loads(payload) == [[0, 1], "future\n"]


@pytest.mark.parametrize("partial_line", [False, True])
def test_plain_pipe_adapter_displays_async_result_before_completed_input(partial_line):
    script = """
import json
import threading
import time
from types import SimpleNamespace
from media_cli.ui.poster import PosterManager
from media_cli.ui.view_model import Blank, DetailLines, Inherit, ViewItem, ViewSpec
from media_cli.ui.view_picker import run_view_session
from media_cli.ui.view_session import ExitSession, UpdateItems, UpdateStatus, ViewSession
session = ViewSession(ViewSpec('root', 'PipeAdapter', state='loading', details=DetailLines(Blank, Blank, Blank)),
                      lambda event: ExitSession(event.item_id))
manager = PosterManager(enabled=lambda: False, cache_dir=lambda: '/tmp',
                        hovered_show_id=lambda: None, request_redraw=lambda: None,
                        loading_frame=lambda: '*')
def update():
    time.sleep(0.4)
    session.post(UpdateItems('root', session.generation,
                            (ViewItem('heading', 'Results', kind='heading', details=Blank),
                             ViewItem('original-id', 'Delayed result', details=Inherit)), replace_all=True))
    session.post(UpdateStatus('root', session.generation, 'ready'))
worker = threading.Thread(target=update)
worker.start()
value = run_view_session(SimpleNamespace(plain_mode=True, show_image=False),
                         SimpleNamespace(active_picker_query='', hovered_show_id=None),
                         session, poster_manager=manager)
worker.join()
print('RESULT:' + json.dumps(value) + 'END_RESULT', flush=True)
"""
    proc = subprocess.Popen([sys.executable, "-c", script], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output = bytearray()
    deadline = time.monotonic() + 4

    def until(marker):
        while marker not in output:
            if time.monotonic() > deadline:
                pytest.fail(f"Pipe stalled before {marker!r}: {bytes(output)!r}")
            if select.select([proc.stdout], [], [], 0.05)[0]:
                chunk = os.read(proc.stdout.fileno(), 65536)
                assert chunk, bytes(output)
                output.extend(chunk)

    try:
        until(b"Enter choice:")
        if partial_line:
            proc.stdin.write(b"1")
            proc.stdin.flush()
        until(b"[1] Delayed result")
        proc.stdin.write(b"\n" if partial_line else b"1\n")
        proc.stdin.flush()
        until(b"END_RESULT")
        proc.wait(timeout=1)
        assert proc.returncode == 0, proc.stderr.read()
        payload = bytes(output).split(b"RESULT:", 1)[1].split(b"END_RESULT", 1)[0]
        assert json.loads(payload) == "original-id"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=1)
        proc.stdin.close()
        proc.stdout.close()
        proc.stderr.close()


@pytest.mark.parametrize("piped, expected", [("", [-5, -5]), ("1", [0, -5]), ("1\n", [0, -5])])
def test_plain_pipe_eof_and_unterminated_final_line(piped, expected):
    script = """
import json
from media_cli.ui.plain_picker import plain_pick
values = [plain_pick(None, None, 'PipeEOF', ['Only'], structured=True,
                     return_on_quit=True) for _ in range(2)]
print('RESULT:' + json.dumps(values) + 'END_RESULT')
"""
    proc = subprocess.run([sys.executable, "-c", script], input=piped, capture_output=True,
                          text=True, timeout=3)
    assert proc.returncode == 0, proc.stderr
    payload = proc.stdout.split("RESULT:", 1)[1].split("END_RESULT", 1)[0]
    assert json.loads(payload) == expected


@pytest.mark.parametrize("failure", ["open", "attrs"])
def test_tui_terminal_failure_fallback_passes_hooks(monkeypatch, failure):
    import media_cli.ui.picker as module
    import media_cli.ui.plain_picker as plain_module
    from media_cli.ui.picker_state import PickerState

    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: True)
    if failure == "open":
        open_tty = lambda *args: (_ for _ in ()).throw(OSError())
    else:
        open_tty = lambda *args: os.dup(0)
        monkeypatch.setattr(module.termios, "tcgetattr", lambda *args: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(module, "os", SimpleNamespace(open=open_tty, fdopen=os.fdopen, close=os.close, O_RDWR=os.O_RDWR))
    received = {}

    def fallback(*args, **kwargs):
        received.update(kwargs)
        return -6

    monkeypatch.setattr(plain_module, "plain_pick", fallback)
    state = PickerState()
    hooks = {"filter_fn": lambda query, options: [], "key_fn": lambda key, index: True,
             "exit_fn": lambda: True}
    assert module.tui_pick(SimpleNamespace(plain_mode=False), None, "Pick", ["Heading"],
                           state=state, heading_indices={0}, structured=True, action_labels=["Back"],
                           return_on_quit=True, **hooks) == -6
    assert received["state"] is state
    assert received["heading_indices"] == {0}
    assert received["return_on_quit"] is True
    assert received["structured"] is True
    assert received["action_labels"] == ["Back"]
    assert all(received[name] is value for name, value in hooks.items())
