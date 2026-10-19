import os
import pty
import threading
import time
import tty

import pytest


def test_preparation_pumps_through_each_phase():
    from media_cli.ui.transition import run_preparation

    phases = []

    def prepare():
        time.sleep(0.08)
        phases.append("fetched")
        time.sleep(0.08)
        phases.append("prepared")
        return ("ready",)

    frames = []
    result = run_preparation(prepare, tick=lambda: frames.append(tuple(phases)))
    assert result == ("ready",)
    assert () in frames
    assert ("fetched",) in frames


def test_cancellation_does_not_wait_for_uninterruptible_worker():
    from media_cli.ui.transition import PreparationCancelled, run_preparation

    release = threading.Event()
    started = threading.Event()
    cancelled = threading.Event()

    def prepare():
        started.set()
        release.wait(3)
        return "obsolete"

    try:
        before = time.monotonic()

        def tick():
            if started.is_set():
                cancelled.set()

        with pytest.raises(PreparationCancelled):
            run_preparation(prepare, tick=tick, cancelled=cancelled.is_set)
        assert time.monotonic() - before < 1
        assert run_preparation(lambda: "new", tick=lambda: None) == "new"
    finally:
        release.set()


def test_failure_keeps_original_traceback():
    from media_cli.ui.transition import run_preparation

    def explode():
        raise ValueError("preparation failed")

    with pytest.raises(ValueError, match="preparation failed") as error:
        run_preparation(explode, tick=lambda: None)
    import traceback

    assert "explode" in "".join(traceback.format_exception(error.value))


def test_pending_keys_are_decoded_without_blocking_or_replay():
    from media_cli.ui.transition import LoadingInput

    master, slave = pty.openpty()
    tty.setraw(slave)
    keys = LoadingInput(slave)
    try:
        os.write(master, b"\x1b[")
        assert keys.poll() is None
        os.write(master, b"Atyped\r\x1b[B")
        assert keys.poll() is None
        os.write(master, b"\x1b[D")
        assert keys.poll() == "cancel"
        assert keys.poll() is None
        os.write(master, b"\x1b")
        assert keys.poll() is None
        time.sleep(0.12)
        assert keys.poll() == "cancel"
        os.write(master, b"\x03")
        assert keys.poll() == "quit"
    finally:
        os.close(master)
        os.close(slave)


def test_loading_input_reports_complete_keys_and_preserves_cancel_priority():
    from media_cli.ui.transition import LoadingInput

    master, slave = pty.openpty()
    tty.setraw(slave)
    decoded = []
    keys = LoadingInput(slave, on_key=decoded.append)
    try:
        os.write(master, b"\x1b[")
        assert keys.poll() is None
        assert decoded == []
        os.write(master, b"A\r\x12\x1br\x1b[6~\x01")
        assert keys.poll() is None
        assert decoded == ["UP", "ENTER", "CTRL_R", "ALT_R", "PAGE_DOWN", "UNKNOWN"]
        os.write(master, b"\x1b[D\x03\r")
        assert keys.poll() == "quit"
        assert not keys.has_pending_sequence
        assert keys.poll() is None
    finally:
        os.close(master)
        os.close(slave)


def test_controller_ui_calls_run_on_owner_while_preparation_stays_off_thread():
    from media_cli.ui.transition import call_on_ui_thread, run_preparation

    owner = threading.get_ident()

    def controller():
        assert threading.get_ident() != owner
        first = call_on_ui_thread(threading.get_ident)
        time.sleep(0.08)
        second = call_on_ui_thread(threading.get_ident)
        return first, second

    assert run_preparation(controller, tick=lambda: None) == (owner, owner)


@pytest.mark.parametrize("fails", (False, True))
def test_preparation_brackets_child_ui_with_owner_hooks(fails):
    from media_cli.ui.transition import call_on_ui_thread, run_preparation

    events = []

    def child():
        events.append("child")
        if fails:
            raise ValueError("child failure")
        return 42

    def run():
        return run_preparation(
            lambda: call_on_ui_thread(child), tick=lambda: None,
            on_ui_enter=lambda: events.append("suspend"),
            on_ui_return=lambda: events.append("resume"),
        )

    if fails:
        with pytest.raises(ValueError, match="child failure"):
            run()
    else:
        assert run() == 42
    assert events == ["suspend", "child", "resume"]


def test_nested_preparation_reports_status_to_owning_pump():
    from media_cli.ui.transition import run_preparation, set_preparation_status

    statuses = []

    def prepare():
        set_preparation_status("Preparing destination")
        return 1

    assert run_preparation(prepare, tick=lambda: None, status_cb=statuses.append) == 1
    assert statuses == ["Preparing destination"]


def test_abandoned_controller_cannot_request_a_late_screen():
    from media_cli.ui.transition import PreparationCancelled, call_on_ui_thread, run_preparation

    release, started, finished = threading.Event(), threading.Event(), threading.Event()
    screens = []

    def controller():
        started.set()
        release.wait(3)
        try:
            call_on_ui_thread(lambda: screens.append("late"))
        finally:
            finished.set()

    try:
        with pytest.raises(PreparationCancelled):
            run_preparation(controller, tick=lambda: None, cancelled=started.is_set)
        release.set()
        assert finished.wait(1)
        assert screens == []
    finally:
        release.set()


def test_worker_capacity_wait_still_pumps_and_can_be_cancelled(monkeypatch):
    from media_cli.ui import transition

    slots = threading.BoundedSemaphore(1)
    assert slots.acquire(blocking=False)
    monkeypatch.setattr(transition, "_slots", slots)
    ticks = []
    try:
        with pytest.raises(transition.PreparationCancelled):
            transition.run_preparation(
                lambda: pytest.fail("worker started without capacity"),
                tick=lambda: ticks.append(True),
                cancelled=lambda: len(ticks) >= 3,
            )
        assert len(ticks) >= 3
    finally:
        slots.release()


def test_redirected_loading_does_not_write_terminal_controls(monkeypatch):
    from io import StringIO
    from types import SimpleNamespace

    from media_cli.ui import display

    output = StringIO()
    monkeypatch.setattr(display.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(display.sys, "stdout", output)
    assert display.with_loading("Loading", lambda: 42) == 42
    assert output.getvalue() == ""


def test_screen_handoff_waits_for_pending_escape_sequence_boundary():
    from media_cli.ui.transition import LoadingInput, call_on_ui_thread, run_preparation

    master, slave = pty.openpty()
    tty.setraw(slave)
    keys = LoadingInput(slave)
    prefix_seen, completed = threading.Event(), threading.Event()
    os.write(master, b"\x1b[")

    def tick():
        assert keys.poll() is None
        if keys.has_pending_sequence:
            prefix_seen.set()

    def prepare():
        assert prefix_seen.wait(1)
        return call_on_ui_thread(lambda: completed.is_set())

    def finish_key():
        os.write(master, b"A")
        completed.set()

    timer = threading.Timer(0.06, finish_key)
    timer.start()
    try:
        assert run_preparation(prepare, tick=tick, ready_for_ui=lambda: not keys.has_pending_sequence)
    finally:
        timer.join()
        os.close(master)
        os.close(slave)


def test_started_csi_does_not_expire_like_standalone_escape():
    from media_cli.ui.transition import LoadingInput

    master, slave = pty.openpty()
    tty.setraw(slave)
    keys = LoadingInput(slave)
    try:
        os.write(master, b"\x1b[")
        assert keys.poll() is None
        time.sleep(0.15)
        assert keys.poll() is None
        assert keys.has_pending_sequence
        os.write(master, b"A")
        assert keys.poll() is None
        assert not keys.has_pending_sequence
    finally:
        os.close(master)
        os.close(slave)


def test_redirected_search_does_not_enter_alternate_screen(monkeypatch):
    from io import StringIO
    from types import SimpleNamespace

    from media_cli.ui import display

    output = StringIO()
    monkeypatch.setattr(display.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(display.sys, "stdout", output)
    monkeypatch.setattr(display, "_alt_screen_active", False)
    display.enter_alt_screen()
    assert output.getvalue() == ""


@pytest.mark.parametrize('already_active', [True, False])
def test_alt_screen_entry_clears_images_only_when_changing_buffers(monkeypatch, already_active):
    from io import StringIO
    from types import SimpleNamespace

    from media_cli.context import FLAGS
    from media_cli.ui import display, terminal_images

    class TtyOutput(StringIO):
        def isatty(self):
            return True

    output = TtyOutput()
    monkeypatch.setattr(FLAGS, 'plain_mode', False)
    monkeypatch.setattr(display.sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(display.sys, 'stdout', output)
    monkeypatch.setattr(display, '_alt_screen_active', already_active)
    monkeypatch.setattr(terminal_images, '_active', True)
    monkeypatch.setattr(terminal_images, 'clear_terminal_images', lambda: 'IMAGE_DELETED')
    display.enter_alt_screen()
    display.enter_alt_screen()
    assert output.getvalue().count('IMAGE_DELETED') == (0 if already_active else 1)
    assert terminal_images._active is already_active


def test_loading_delay_counts_only_time_since_last_ui_handoff():
    from media_cli.ui.transition import LoadingDelay

    clock = [0.0]
    loading = LoadingDelay(clock=lambda: clock[0])
    clock[0] = 0.149
    assert not loading.visible
    clock[0] = 0.151
    assert loading.visible
    clock[0] = 20.0
    loading.reset()
    assert not loading.visible
    clock[0] += 0.151
    assert loading.visible


def test_ready_result_polls_input_without_painting_a_final_loading_frame():
    from media_cli.ui.transition import run_preparation

    ready = threading.Event()
    inputs, frames = [], []

    def prepare():
        ready.set()
        return 42

    def tick():
        assert ready.wait(1)
        inputs.append(True)

    assert run_preparation(prepare, tick=tick, on_wait=lambda: frames.append(True)) == 42
    assert len(inputs) >= 2
    assert frames == []


def test_preparation_restarts_loading_delay_after_ui_returns():
    from media_cli.ui.transition import LoadingDelay, call_on_ui_thread, run_preparation

    clock = [0.0]
    loading = LoadingDelay(clock=lambda: clock[0])
    frames = []

    def picker():
        clock[0] += 20

    def wait():
        if loading.visible:
            frames.append(True)

    assert run_preparation(
        lambda: call_on_ui_thread(picker), tick=lambda: None,
        on_wait=wait, on_ui_return=loading.reset,
    ) is None
    assert frames == []


def test_hidden_loading_presentation_does_not_delay_cancellation():
    from media_cli.ui.transition import LoadingDelay, PreparationCancelled, run_preparation

    started, release, cancel = threading.Event(), threading.Event(), threading.Event()
    loading = LoadingDelay(clock=lambda: 0)
    frames = []

    def prepare():
        started.set()
        release.wait(2)

    def tick():
        if started.is_set():
            cancel.set()

    def render():
        if loading.visible:
            frames.append(True)

    try:
        with pytest.raises(PreparationCancelled):
            run_preparation(prepare, tick=tick, on_wait=render, cancelled=cancel.is_set)
        assert cancel.is_set()
        assert frames == []
    finally:
        release.set()
