"""Main-thread terminal ownership shared by interactive widgets.

Decorate the application entry point to retain ownership between widgets. A
standalone borrower owns the terminal only for its context. Unavailable terminals
yield ``None`` from ``borrow_terminal`` so consumers can use their plain path.
"""

from __future__ import annotations

import os
import sys
import termios
import threading
import tty
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from types import TracebackType
from typing import TYPE_CHECKING, ParamSpec, TypeVar

if TYPE_CHECKING:
    from .actions import ActionBindings

_P = ParamSpec("_P")
_R = TypeVar("_R")
_scope_runtime: TerminalRuntime | None = None


class TerminalRuntime:
    """Own a duplicate of ``fd``, or open /dev/tty for interactive streams.

    Explicit contexts acquire immediately. ``terminal_application`` establishes a
    lazy scope instead, acquiring on the first borrow. Injected descriptors bypass
    stream checks, allowing PTY use without replacing the process's stdin.
    """

    def __init__(self, fd: int | None = None) -> None:
        if fd is not None:
            if isinstance(fd, bool) or not isinstance(fd, int):
                raise TypeError("Terminal descriptor must be an integer")
            if fd < 0:
                raise ValueError("Terminal descriptor must be nonnegative")
        self._injected_fd = fd
        self._fd: int | None = None
        self._original_attrs: list[int | list[bytes | int]] | None = None
        self._entered = False
        self._attempted = False
        self._suspensions = 0
        self.input_bindings: ActionBindings | None = None

    def assert_owner_thread(self) -> None:
        """Reject terminal access outside the main UI thread."""
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Terminal ownership requires the main UI thread")

    def __enter__(self) -> TerminalRuntime:
        self._enter(acquire=True)
        return self

    def _enter(self, *, acquire: bool) -> None:
        global _scope_runtime
        self.assert_owner_thread()
        if self._entered or _scope_runtime is not None:
            raise RuntimeError("An active terminal owner already exists; borrow it instead")
        self._entered = True
        self._attempted = False
        _scope_runtime = self
        try:
            if acquire:
                self._acquire()
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        global _scope_runtime
        self.assert_owner_thread()
        if not self._entered:
            raise RuntimeError("Terminal runtime has no active scope")
        try:
            self._release()
        finally:
            self._entered = False
            self._suspensions = 0
            self.input_bindings = None
            _scope_runtime = None

    def _assert_available(self) -> None:
        self.assert_owner_thread()
        if not self._entered:
            raise RuntimeError("Terminal runtime has no active scope")
        if self._suspensions:
            raise RuntimeError("Terminal runtime is suspended")

    def _acquire(self) -> bool:
        self._assert_available()
        if self._fd is not None:
            return True
        if self._attempted:
            return False
        self._attempted = True
        try:
            if self._injected_fd is None:
                if not sys.stdin.isatty() or not sys.stdout.isatty():
                    return False
                self._fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
            else:
                self._fd = os.dup(self._injected_fd)
            # Keep this snapshot for the entire scope, including suspensions.
            self._original_attrs = termios.tcgetattr(self._fd)
            tty.setraw(self._fd, termios.TCSANOW)
        except (OSError, termios.error, ValueError):
            try:
                self._release()
            except (OSError, termios.error):
                pass
            return False
        except BaseException:
            self._release()
            raise
        return True

    def _release(self) -> None:
        fd, attrs = self._fd, self._original_attrs
        self._fd = None
        self._original_attrs = None
        if fd is not None:
            try:
                if attrs is not None:
                    termios.tcsetattr(fd, termios.TCSAFLUSH, attrs)
            finally:
                os.close(fd)

    def fileno(self) -> int:
        """Return the owned descriptor; consumers must duplicate before closing."""
        self._assert_available()
        if self._fd is None:
            raise RuntimeError("Terminal runtime has no acquired descriptor")
        return self._fd

    def ensure_raw(self) -> None:
        """Reapply raw mode after curses without replacing the saved settings."""
        tty.setraw(self.fileno(), termios.TCSANOW)

    def prepare_shutdown(self) -> None:
        """Keep queued exit keys silent while allowing native interrupt signals."""
        fd = self.fileno()
        attrs = termios.tcgetattr(fd)
        attrs[3] = (attrs[3] | termios.ISIG) & ~(termios.ECHO | termios.ICANON)
        termios.tcsetattr(fd, termios.TCSANOW, attrs)

    @contextmanager
    def borrow(self) -> Iterator[TerminalRuntime]:
        """Acquire if needed, retaining ownership after every nested borrower."""
        if not self._acquire():
            raise OSError("No usable interactive terminal")
        self.ensure_raw()
        yield self

    @contextmanager
    def line_input(self) -> Iterator[None]:
        """Temporarily enable native line editing and signals for a prompt."""
        fd = self.fileno()
        before = termios.tcgetattr(fd)
        with self.suspend():
            try:
                attrs = termios.tcgetattr(fd)
                attrs[0] = (attrs[0] | termios.ICRNL) & ~(termios.IGNCR | termios.INLCR)
                attrs[1] |= termios.OPOST | termios.ONLCR
                attrs[3] |= termios.ECHO | termios.ICANON | termios.ISIG | termios.IEXTEN
                termios.tcsetattr(fd, termios.TCSAFLUSH, attrs)
                yield
            finally:
                termios.tcsetattr(fd, termios.TCSAFLUSH, before)

    @contextmanager
    def suspend(self) -> Iterator[None]:
        """Restore original settings for an external program, then resume raw."""
        self.assert_owner_thread()
        if not self._entered or self._fd is None or self._original_attrs is None:
            raise RuntimeError("Cannot suspend a terminal without active ownership")
        if not self._suspensions:
            termios.tcsetattr(self._fd, termios.TCSANOW, self._original_attrs)
        self._suspensions += 1
        try:
            yield
        finally:
            self._suspensions -= 1
            if not self._suspensions:
                self.ensure_raw()


def current_runtime() -> TerminalRuntime | None:
    """Return the acquired interactive owner, excluding idle/suspended scopes."""
    runtime = _scope_runtime
    if runtime is None or runtime._fd is None or runtime._suspensions:
        return None
    return runtime


@contextmanager
def borrow_terminal() -> Iterator[TerminalRuntime | None]:
    """Borrow the application owner or establish a standalone widget scope."""
    runtime = _scope_runtime
    if runtime is None:
        with TerminalRuntime():
            with borrow_terminal() as standalone:
                yield standalone
    elif runtime._acquire():
        with runtime.borrow() as borrowed:
            yield borrowed
    else:
        yield None


def terminal_application(function: Callable[_P, _R]) -> Callable[_P, _R]:
    """Retain lazy terminal ownership until this outer application call exits."""

    @wraps(function)
    def scoped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        from .transition import call_on_ui_thread, in_preparation

        if in_preparation():

            def invoke() -> _R:
                return function(*args, **kwargs)

            return call_on_ui_thread(invoke)
        if _scope_runtime is not None:
            _scope_runtime.assert_owner_thread()
            return function(*args, **kwargs)
        runtime = TerminalRuntime()
        runtime._enter(acquire=False)
        try:
            return function(*args, **kwargs)
        finally:
            runtime.__exit__(*sys.exc_info())

    return scoped
