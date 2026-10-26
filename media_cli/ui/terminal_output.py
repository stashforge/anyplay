"""Explicit Python stdout ownership while a TUI is drawing.

Native curses still draws to descriptor 1. Unowned Python writes go to the
existing descriptor-2 diagnostic consumer, never to a screen or a status callback.
"""

from __future__ import annotations

import os
import sys
import threading
from typing import Any


class _DiagnosticBuffer:
    def __init__(self) -> None:
        self._fd: int | None = os.dup(2)
        self._lock = threading.Lock()

    def write(self, data: bytes) -> int:
        # A retained stream must never start writing to the restored terminal.
        # Each in-flight write owns its pipe reference; close never waits on it.
        with self._lock:
            if self._fd is None:
                return len(data)
            fd = os.dup(self._fd)
        try:
            pending = memoryview(data)
            while pending:
                pending = pending[os.write(fd, pending):]
        except OSError:
            pass  # The diagnostic reader may already have finished shutdown.
        finally:
            os.close(fd)
        return len(data)

    def fileno(self) -> int:
        with self._lock:
            if self._fd is None:
                raise ValueError('Terminal diagnostic stream is closed')
            return self._fd

    def close(self) -> None:
        with self._lock:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None

    def flush(self) -> None:
        pass

    def writelines(self, lines: Any) -> None:
        for line in lines:
            self.write(line)


class _DiagnosticStdout:
    _anyplay_diagnostics = True

    def __init__(self, original: Any):
        self.original = original
        self.buffer = _DiagnosticBuffer()

    def write(self, text: str) -> int:
        self.buffer.write(text.encode(self.encoding or 'utf-8', errors='replace'))
        return len(text)

    def flush(self) -> None:
        pass

    def fileno(self) -> int:
        # Libraries explicitly passed this stream also inherit diagnostic output.
        return self.buffer.fileno()

    def writelines(self, lines: Any) -> None:
        for line in lines:
            self.write(line)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.original, name)


_guard: _DiagnosticStdout | None = None


def begin_stdout_guard() -> None:
    global _guard
    if _guard is None and sys.stdout.isatty():
        sys.stdout.flush()
        _guard = _DiagnosticStdout(sys.stdout)
        sys.stdout = _guard


def end_stdout_guard() -> None:
    global _guard
    if _guard is not None:
        if sys.stdout is _guard:
            sys.stdout = _guard.original
        _guard.buffer.close()
        _guard = None


def write_terminal(text: str, *, flush: bool = True) -> None:
    """UI-owned text only. Workers must enqueue data rather than call this."""
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError('Only the UI thread may draw to the terminal')
    stream = _guard.original if sys.stdout is _guard and _guard is not None else sys.stdout
    stream.write(text)
    if flush:
        stream.flush()
