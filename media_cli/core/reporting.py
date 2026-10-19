"""User-facing status reporting.

Owns the ``info``/``ok``/``warn``/``err`` reporters used across the CLI.

By default messages print to the terminal.  When a full-screen UI (the
player screen) is active it can register a *status sink* via
:func:`set_status_sink`; while a sink accepts messages they are routed to
it instead of being printed, so reporting never has to know about — or
import — any UI module.  Dependencies point downward only:

    reporting -> core.terminal (sanitize)
    reporting -> context.FLAGS (debug mode)

The sink contract: ``sink(formatted_message, ansi_color) -> bool`` —
return ``True`` when the message was consumed (UI visible), ``False`` to
fall back to plain printing.
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from ..context import FLAGS
from .terminal import sanitize_terminal_text

_logger = logging.getLogger("media_cli.reporting")
_status_sink = None
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


def info(m):
    m = sanitize_terminal_text(m)
    if not _consumed_by_sink(f"[INFO] {m}", "\033[94m"):
        _logger.info("%s", m)


def ok(m):
    m = sanitize_terminal_text(m)
    if not _consumed_by_sink(f"[OK] {m}", "\033[92m"):
        _logger.info("[OK] %s", m)


def progress(message: str) -> None:
    """Report unprefixed task progress through the current UI-owned sink."""
    message = sanitize_terminal_text(message)
    if not _consumed_by_sink(message, "\033[1;92m"):
        _logger.info("%s", message)


def warn(m):
    m = sanitize_terminal_text(m)
    if not _consumed_by_sink(f"[WARN] {m}", "\033[93m"):
        _logger.warning("%s", m)


def err(m):
    m = sanitize_terminal_text(m)
    if _consumed_by_sink(f"[ERR] {m}", "\033[91m"):
        _logger.debug("UI error report: %s", m[:500])
    else:
        _logger.error("%s", m)


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
