"""User-facing status reporting.

Owns the ``info``/``ok``/``warn``/``err`` reporters used across the CLI.

Messages are logged and offered as data to a full-screen UI (such as the
player screen), which can register a *status sink* via
:func:`set_status_sink`; while a sink accepts messages they are routed to
it without drawing here. Plain-mode fallback and legacy ``console_status``
delivery print only outside guarded TUI output. Reporting imports no UI module.
Dependencies point downward only:

    reporting -> core.terminal (sanitize)
    reporting -> context.FLAGS (debug mode)

The sink contract: ``sink(formatted_message, ansi_color) -> bool`` —
return ``True`` when the message was accepted by the UI, ``False`` for logging
and any applicable console fallback. Acceptance does not require immediate paint.
"""

import logging
import sys
import threading
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from ..context import FLAGS
from .terminal import sanitize_terminal_text

_logger = logging.getLogger("media_cli.reporting")
_status_sink = None
_deferred_notices: deque[str] = deque(maxlen=16)
_notice_lock = threading.Lock()
_scoped_status_sink: ContextVar[Callable[[str, str], bool] | None] = ContextVar(
    "reporting_status_sink", default=None,
)


@contextmanager
def status_sink_scope(sink: Callable[[str, str], bool]) -> Iterator[None]:
    """Bind reports to their originating work, including after it is abandoned."""
    token = _scoped_status_sink.set(sink)
    try:
        yield
    finally:
        _scoped_status_sink.reset(token)


def set_status_sink(sink):
    """Register ``sink(message, color) -> bool`` as the active status sink.

    Pass ``None`` to remove the sink and restore plain printing.
    """
    global _status_sink
    _status_sink = sink


def _consumed_by_sink(message, color):
    sink = _scoped_status_sink.get()
    if sink is None:
        sink = _status_sink
    if sink is None:
        return False
    return bool(sink(message, color))


def _plain_fallback(message: str, consumed: bool) -> None:
    if not consumed and FLAGS.plain_mode and not getattr(sys.stdout, '_anyplay_diagnostics', False):
        print(message)


def info(m):
    m = sanitize_terminal_text(m)
    _logger.info("%s", m)
    _plain_fallback(m, _consumed_by_sink(f"[INFO] {m}", "\033[94m"))


def ok(m):
    m = sanitize_terminal_text(m)
    _logger.info("[OK] %s", m)
    _plain_fallback(m, _consumed_by_sink(f"[OK] {m}", "\033[92m"))


def progress(message: str) -> None:
    """Report unprefixed task progress through the current UI-owned sink."""
    message = sanitize_terminal_text(message)
    _logger.info("%s", message)
    _plain_fallback(message, _consumed_by_sink(message, "\033[1;92m"))


def warn(m):
    m = sanitize_terminal_text(m)
    _logger.warning("%s", m)
    _plain_fallback(m, _consumed_by_sink(f"[WARN] {m}", "\033[93m"))


def err(m):
    m = sanitize_terminal_text(m)
    if _consumed_by_sink(f"[ERR] {m}", "\033[91m"):
        _logger.debug("UI error report: %s", m[:500])
    else:
        _logger.error("%s", m)
        _plain_fallback(m, False)


def debug_warn(context, exc=None):
    if exc is not None:
        _logger.warning("%s: %s", context, exc, exc_info=FLAGS.debug_mode)
    else:
        _logger.warning("%s", context)
    if FLAGS.debug_mode:
        if exc is not None:
            warn(f"{context}: {exc}")
        else:
            warn(str(context))


def console_status(message: str, *, kind: str = 'info') -> None:
    """Report legacy console progress as data; print only outside a TUI.

    Services do not select a terminal row or write styled strings. Detailed text
    is retained in logs even when the screen consumes a shorter presentation.
    """
    prefix, color, level = {
        'info': ('INFO', '\033[94m', logging.INFO),
        'success': ('OK', '\033[92m', logging.INFO),
        'warning': ('WARN', '\033[93m', logging.WARNING),
        'error': ('ERR', '\033[91m', logging.ERROR),
    }[kind]
    clean = sanitize_terminal_text(message)
    _logger.log(level, '%s', clean)
    if not _consumed_by_sink(f'[{prefix}] {clean}', color):
        if not getattr(sys.stdout, '_anyplay_diagnostics', False):
            print(clean)


def defer_notice(message: str) -> None:
    """Workers retain a notice for the CLI owner after terminal restoration."""
    clean = sanitize_terminal_text(message).strip()[:500]
    with _notice_lock:
        if clean and clean not in _deferred_notices:
            _deferred_notices.append(clean)


def take_deferred_notices() -> tuple[str, ...]:
    with _notice_lock:
        messages = tuple(_deferred_notices)
        _deferred_notices.clear()
    return messages
