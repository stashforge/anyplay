"""Terminal ownership contracts against real PTYs, without the renderer."""

from __future__ import annotations

import io
import os
import pty
import select
import sys
import termios
import threading
from collections.abc import Callable, Iterator
from types import SimpleNamespace

import pytest

from media_cli.ui import terminal_runtime as runtime_module
from media_cli.ui.terminal_runtime import (
    TerminalRuntime,
    borrow_terminal,
    current_runtime,
    terminal_application,
)


@pytest.fixture
def terminal() -> Iterator[tuple[int, int]]:
    master, slave = pty.openpty()
    try:
        yield master, slave
    finally:
        os.close(slave)
        os.close(master)


@pytest.fixture
def interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    class InteractiveStream(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(
        runtime_module,
        "sys",
        SimpleNamespace(stdin=InteractiveStream(), stdout=InteractiveStream(), exc_info=sys.exc_info),
    )


def patch_open(monkeypatch: pytest.MonkeyPatch, opener: Callable[[str, int], int]) -> None:
    monkeypatch.setattr(
        runtime_module,
        "os",
        SimpleNamespace(open=opener, dup=os.dup, close=os.close, O_RDWR=os.O_RDWR, O_NOCTTY=os.O_NOCTTY),
    )


def assert_raw(fd: int) -> None:
    attrs = termios.tcgetattr(fd)
    assert not attrs[3] & (termios.ECHO | termios.ICANON | termios.ISIG)
    assert not attrs[0] & (termios.ICRNL | termios.IXON)
    assert not attrs[1] & termios.OPOST
    assert attrs[6][termios.VMIN] == 1
    assert attrs[6][termios.VTIME] == 0


def test_nested_borrow_preserves_no_echo(terminal: tuple[int, int]) -> None:
    master, slave = terminal
    original = termios.tcgetattr(slave)
    with TerminalRuntime(fd=slave) as runtime:
        assert current_runtime() is runtime
        with runtime.borrow() as outer, runtime.borrow() as inner:
            assert outer is inner is runtime
            assert_raw(slave)
            os.write(master, b"\x1b[Aquery\r")
            assert os.read(runtime.fileno(), 64) == b"\x1b[Aquery\r"
            assert not select.select([master], [], [], 0.05)[0]
        assert_raw(slave)
        with borrow_terminal() as nested:
            assert nested is runtime
        assert_raw(slave)
    assert current_runtime() is None
    assert termios.tcgetattr(slave) == original


@pytest.mark.parametrize("exception", [None, ValueError, KeyboardInterrupt, SystemExit])
def test_exit_and_exception_restore_original_attrs(
    terminal: tuple[int, int], exception: type[BaseException] | None
) -> None:
    _, slave = terminal
    original = termios.tcgetattr(slave)
    runtime_fd = -1

    def run() -> None:
        nonlocal runtime_fd
        with TerminalRuntime(fd=slave) as runtime:
            runtime_fd = runtime.fileno()
            assert runtime_fd != slave
            assert_raw(slave)
            if exception is not None:
                raise exception("exit")

    if exception is None:
        run()
    else:
        with pytest.raises(exception, match="exit"):
            run()
    assert termios.tcgetattr(slave) == original
    assert current_runtime() is None
    with pytest.raises(OSError):
        os.fstat(runtime_fd)


@pytest.mark.parametrize("fail", [False, True])
def test_suspend_restores_then_reacquires(terminal: tuple[int, int], fail: bool) -> None:
    _, slave = terminal
    original = termios.tcgetattr(slave)
    with TerminalRuntime(fd=slave) as runtime:
        with pytest.raises(ValueError, match="external") if fail else runtime.borrow():
            with runtime.suspend():
                assert current_runtime() is None
                assert termios.tcgetattr(slave) == original
                with runtime.suspend():
                    assert termios.tcgetattr(slave) == original
                with pytest.raises(RuntimeError, match="suspend"):
                    with borrow_terminal():
                        pytest.fail("Suspension allowed a competing borrower")
                if fail:
                    raise ValueError("external")
        assert current_runtime() is runtime
        assert_raw(slave)
    assert termios.tcgetattr(slave) == original


def test_suspension_does_not_replace_original_snapshot(terminal: tuple[int, int]) -> None:
    _, slave = terminal
    original = termios.tcgetattr(slave)
    with TerminalRuntime(fd=slave) as runtime:
        with runtime.suspend():
            changed = termios.tcgetattr(slave)
            changed[3] ^= termios.ECHO
            termios.tcsetattr(slave, termios.TCSANOW, changed)
        assert_raw(slave)
    assert termios.tcgetattr(slave) == original


def test_ensure_raw_after_curses_preserves_original_snapshot(terminal: tuple[int, int]) -> None:
    _, slave = terminal
    original = termios.tcgetattr(slave)
    with TerminalRuntime(fd=slave) as runtime:
        termios.tcsetattr(slave, termios.TCSANOW, original)
        runtime.ensure_raw()
        assert_raw(slave)
        with runtime.suspend(), pytest.raises(RuntimeError, match="suspend"):
            runtime.ensure_raw()
    assert termios.tcgetattr(slave) == original


def test_runtime_does_not_flush_pending_input(terminal: tuple[int, int]) -> None:
    master, slave = terminal
    os.write(master, b"ready\n")
    assert select.select([slave], [], [], 1)[0]
    with TerminalRuntime(fd=slave) as runtime:
        assert os.read(runtime.fileno(), 64) == b"ready\n"


def test_consumer_can_close_its_own_stream(terminal: tuple[int, int]) -> None:
    master, slave = terminal
    with TerminalRuntime(fd=slave) as runtime:
        with os.fdopen(os.dup(runtime.fileno()), "rb+", buffering=0) as stream:
            os.write(master, b"x")
            assert stream.read(1) == b"x"
        os.fstat(runtime.fileno())
        assert_raw(slave)


def test_plain_mode_does_not_acquire_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden_open(path: str, flags: int) -> int:
        pytest.fail("Non-TUI code acquired a terminal")

    patch_open(monkeypatch, forbidden_open)

    @terminal_application
    def plain(value: int, *, suffix: str) -> str:
        assert current_runtime() is None
        return f"{value}{suffix}"

    assert plain(3, suffix=" plain") == "3 plain"
    assert plain.__name__ == "plain"
    assert current_runtime() is None


@pytest.mark.parametrize("stream", ["stdin", "stdout"])
def test_redirected_stream_does_not_force_raw(
    monkeypatch: pytest.MonkeyPatch, interactive: None, terminal: tuple[int, int], stream: str
) -> None:
    _, slave = terminal
    original = termios.tcgetattr(slave)
    monkeypatch.setattr(f"media_cli.ui.terminal_runtime.sys.{stream}", io.StringIO())

    def forbidden_open(path: str, flags: int) -> int:
        pytest.fail("Redirected streams acquired /dev/tty")

    patch_open(monkeypatch, forbidden_open)
    with TerminalRuntime():
        assert current_runtime() is None
        with borrow_terminal() as runtime:
            assert runtime is None
    assert termios.tcgetattr(slave) == original


def test_standalone_borrow_uses_dev_tty_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, interactive: None, terminal: tuple[int, int]
) -> None:
    _, slave = terminal
    original = termios.tcgetattr(slave)

    def open_terminal(path: str, flags: int) -> int:
        assert path == "/dev/tty"
        assert flags & os.O_RDWR
        return os.dup(slave)

    patch_open(monkeypatch, open_terminal)
    with borrow_terminal() as runtime:
        assert runtime is not None
        fd = runtime.fileno()
        assert current_runtime() is runtime
        assert_raw(slave)
    assert termios.tcgetattr(slave) == original
    with pytest.raises(OSError):
        os.fstat(fd)


@pytest.mark.parametrize("fail", [False, True])
def test_application_acquires_lazily_and_retains_ownership_between_borrows(
    monkeypatch: pytest.MonkeyPatch, interactive: None, terminal: tuple[int, int], fail: bool
) -> None:
    _, slave = terminal
    original = termios.tcgetattr(slave)
    opened: list[int] = []

    def open_terminal(path: str, flags: int) -> int:
        fd = os.dup(slave)
        opened.append(fd)
        return fd

    patch_open(monkeypatch, open_terminal)

    @terminal_application
    def nested() -> TerminalRuntime:
        with borrow_terminal() as runtime:
            assert runtime is not None
            return runtime

    @terminal_application
    def application() -> None:
        assert not opened
        assert current_runtime() is None
        with borrow_terminal() as first:
            assert first is not None
        assert current_runtime() is first
        assert_raw(slave)
        assert nested() is first
        assert_raw(slave)
        with borrow_terminal() as second:
            assert second is first
        if fail:
            raise ValueError("application")

    if fail:
        with pytest.raises(ValueError, match="application"):
            application()
    else:
        application()
    assert len(opened) == 1
    assert current_runtime() is None
    assert termios.tcgetattr(slave) == original
    with pytest.raises(OSError):
        os.fstat(opened[0])


def test_missing_controlling_terminal_falls_back_cleanly(monkeypatch: pytest.MonkeyPatch, interactive: None) -> None:
    def unavailable(path: str, flags: int) -> int:
        raise OSError("no controlling terminal")

    patch_open(monkeypatch, unavailable)
    with borrow_terminal() as runtime:
        assert runtime is None
        assert current_runtime() is None


def test_failed_raw_setup_restores_and_closes(
    monkeypatch: pytest.MonkeyPatch, interactive: None, terminal: tuple[int, int]
) -> None:
    import tty

    _, slave = terminal
    original = termios.tcgetattr(slave)
    opened: list[int] = []

    def open_terminal(path: str, flags: int) -> int:
        fd = os.dup(slave)
        opened.append(fd)
        return fd

    def fail_raw(fd: int, when: int) -> None:
        assert when == termios.TCSANOW
        changed = termios.tcgetattr(fd)
        changed[3] &= ~termios.ECHO
        termios.tcsetattr(fd, when, changed)
        raise termios.error("raw failed")

    patch_open(monkeypatch, open_terminal)
    monkeypatch.setattr(tty, "setraw", fail_raw)
    with borrow_terminal() as runtime:
        assert runtime is None
    assert current_runtime() is None
    assert termios.tcgetattr(slave) == original
    with pytest.raises(OSError):
        os.fstat(opened[0])


def in_worker(action: Callable[[], object]) -> BaseException | None:
    errors: list[BaseException] = []

    def run() -> None:
        try:
            action()
        except BaseException as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive()
    return errors[0] if errors else None


def test_owner_thread_checks_reject_worker_access(terminal: tuple[int, int]) -> None:
    _, slave = terminal
    with TerminalRuntime(fd=slave) as runtime:
        runtime.assert_owner_thread()
        for action in (
            runtime.assert_owner_thread,
            runtime.fileno,
            runtime.ensure_raw,
            runtime.borrow().__enter__,
            runtime.suspend().__enter__,
        ):
            error = in_worker(action)
            assert isinstance(error, RuntimeError)
            assert "thread" in str(error).lower()
        assert_raw(slave)


def test_workers_cannot_create_runtime(terminal: tuple[int, int]) -> None:
    _, slave = terminal
    original = termios.tcgetattr(slave)

    def acquire() -> None:
        with TerminalRuntime(fd=slave):
            pytest.fail("Worker acquired terminal ownership")

    assert isinstance(in_worker(acquire), RuntimeError)
    assert termios.tcgetattr(slave) == original
    assert current_runtime() is None


def test_conflicting_owners_are_rejected(terminal: tuple[int, int]) -> None:
    _, slave = terminal
    with TerminalRuntime(fd=slave) as outer:
        with pytest.raises(RuntimeError, match="owner|active"):
            with TerminalRuntime(fd=slave):
                pytest.fail("Competing runtime acquired ownership")
        assert current_runtime() is outer
        assert_raw(slave)


def test_descriptor_unavailable_outside_runtime_scope(terminal: tuple[int, int]) -> None:
    _, slave = terminal
    runtime = TerminalRuntime(fd=slave)
    with pytest.raises(RuntimeError):
        runtime.fileno()
    with runtime:
        assert_raw(slave)
    with pytest.raises(RuntimeError):
        runtime.fileno()


@pytest.mark.parametrize("fd", [-1, True, "0"])
def test_invalid_descriptor_rejected(fd: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        TerminalRuntime(fd=fd)  # type: ignore[arg-type]
