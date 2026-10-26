"""Own native stderr while a TUI owns the terminal; never log through stderr."""

from __future__ import annotations

import codecs
import os
import select
import stat
import sys
import threading
from pathlib import Path
from typing import Any

from ..core.terminal import sanitize_terminal_text


class _PrivateLog:
    def __init__(self, path: Path, max_bytes: int):
        self.path, self.max_bytes = path, max_bytes
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.fd = self._open()

    def _open(self) -> int:
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                raise OSError("Terminal diagnostics require a private regular file")
            os.fchmod(fd, 0o600)
            return fd
        except BaseException:
            os.close(fd)
            raise

    def write(self, text: str) -> None:
        clean = sanitize_terminal_text(text)
        if not clean.strip():
            return
        encoded = (clean + "\n").encode("utf-8", errors="replace")
        for offset in range(0, len(encoded), self.max_bytes):
            piece = encoded[offset:offset + self.max_bytes]
            if os.fstat(self.fd).st_size + len(piece) > self.max_bytes:
                os.close(self.fd)
                self.fd = -1
                os.replace(self.path, str(self.path) + ".1")
                self.fd = self._open()
            pending = memoryview(piece)
            while pending:
                pending = pending[os.write(self.fd, pending):]

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


class StderrCapture:
    """Process-wide descriptor ownership; main thread starts/restores it once."""

    def __init__(self, path: Path, *, max_bytes: int = 1_048_576):
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 256:
            raise ValueError("Terminal log limit must be at least 256 bytes")
        self.path, self.max_bytes = Path(path), max_bytes
        self._saved: int | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.write_failed = False

    def __enter__(self) -> StderrCapture:
        global _active
        self._assert_owner()
        if self._saved is not None or _active is not None:
            raise RuntimeError("Terminal stderr already has an owner")
        _retiring[:] = [capture for capture in _retiring
                        if capture._thread is not None and capture._thread.is_alive()]
        if _retiring:
            raise RuntimeError("Previous terminal diagnostics are still draining")
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("Previous terminal diagnostics are still draining")
        self._stop.clear()
        self.write_failed = False
        sink = _PrivateLog(self.path, self.max_bytes)
        read_fd = write_fd = None
        redirected = False
        try:
            read_fd, write_fd = os.pipe()
            os.set_blocking(read_fd, False)
            self._inheritable = os.get_inheritable(2)
            self._saved = os.dup(2)
            _flush_stderr()
            os.dup2(write_fd, 2, inheritable=self._inheritable)
            redirected = True
            self._thread = threading.Thread(target=self._drain, args=(read_fd, sink), daemon=True,
                                            name="terminal-stderr")
            self._thread.start()
        except BaseException:
            if self._saved is not None:
                if redirected:
                    os.dup2(self._saved, 2, inheritable=self._inheritable)
                os.close(self._saved)
                self._saved = None
            if read_fd is not None:
                os.close(read_fd)
            sink.close()
            raise
        finally:
            if write_fd is not None:
                os.close(write_fd)
        _active = self
        return self

    def _assert_owner(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Terminal stderr ownership requires the main UI thread")

    def _drain(self, fd: int, sink: _PrivateLog) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        drained = 0
        try:
            poller = select.poll()
            poller.register(fd, select.POLLIN)
            while True:
                stopping = self._stop.is_set()
                if stopping and drained >= 262144:
                    break
                ready = poller.poll(0 if stopping else 50)
                if not ready:
                    if stopping:
                        break
                    continue
                try:
                    chunk = os.read(fd, 4096)
                except BlockingIOError:
                    continue
                if not chunk:
                    break
                if stopping:
                    drained += len(chunk)
                text = decoder.decode(chunk)
                try:
                    sink.write(text)
                except OSError:
                    # Keep draining on disk failure; never recurse through logging
                    # or fall back to the terminal from this background consumer.
                    self.write_failed = True
            tail = decoder.decode(b"", final=True)
            if tail:
                sink.write(tail)
        except OSError:
            self.write_failed = True
        finally:
            try:
                sink.close()
            except OSError:
                self.write_failed = True
            try:
                os.close(fd)
            except OSError:
                self.write_failed = True
            if self.write_failed:
                from ..core.reporting import defer_notice
                defer_notice("Some diagnostics could not be saved; check log-directory permissions and disk space.")

    def close(self) -> None:
        global _active
        self._assert_owner()
        if self._saved is None:
            return
        from .terminal_output import end_stdout_guard

        end_stdout_guard()
        # Never acquire sys.stderr's lock here: a writer may hold it while
        # blocked on a full pipe. Restore the descriptor before bounded draining.
        os.dup2(self._saved, 2, inheritable=self._inheritable)
        os.close(self._saved)
        self._saved = None
        _active = None
        self._stop.set()
        if self._thread is not None:
            self._thread.join(.5)
            if self._thread.is_alive():
                _retiring.append(self)

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


_active: StderrCapture | None = None
_retiring: list[StderrCapture] = []


def _flush_stderr() -> None:
    try:
        if sys.stderr is not None:
            sys.stderr.flush()
    except (OSError, ValueError):
        pass


def begin_stderr_capture() -> None:
    """TUI entry/handoff is idempotent; respect caller-owned stderr redirection."""
    if _active is not None:
        return
    if not os.isatty(2):
        from .terminal_output import begin_stdout_guard
        begin_stdout_guard()
        return
    from ..core.diagnostics import get_log_paths

    capture = StderrCapture(Path(get_log_paths()["native"]))
    try:
        capture.__enter__()
        from .terminal_output import begin_stdout_guard

        begin_stdout_guard()
    except (OSError, RuntimeError):
        capture.close()
        from ..core.api import ProviderDependencyError

        raise ProviderDependencyError(
            "Could not prepare terminal diagnostics. Check log-directory permissions, or use --plain."
        ) from None
    except BaseException:
        capture.close()
        raise


def end_stderr_capture() -> None:
    if _active is not None:
        _active.close()
    else:
        from .terminal_output import end_stdout_guard
        end_stdout_guard()
