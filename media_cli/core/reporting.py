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

from ..context import FLAGS
from .terminal import sanitize_terminal_text

_logger = logging.getLogger("media_cli.reporting")
_status_sink = None


def set_status_sink(sink):
    """Register ``sink(message, color) -> bool`` as the active status sink.

    Pass ``None`` to remove the sink and restore plain printing.
    """
    global _status_sink
    _status_sink = sink


def _consumed_by_sink(message, color):
    if _status_sink is None:
        return False
    return bool(_status_sink(message, color))


def info(m):
    m = sanitize_terminal_text(m)
    if not _consumed_by_sink(f"[INFO] {m}", "\033[94m"):
        _logger.info("%s", m)


def ok(m):
    m = sanitize_terminal_text(m)
    if not _consumed_by_sink(f"[OK] {m}", "\033[92m"):
        _logger.info("[OK] %s", m)


def warn(m):
    m = sanitize_terminal_text(m)
    if not _consumed_by_sink(f"[WARN] {m}", "\033[93m"):
        _logger.warning("%s", m)


def err(m):
    m = sanitize_terminal_text(m)
    if not _consumed_by_sink(f"[ERR] {m}", "\033[91m"):
        _logger.error("%s", m)


def debug_warn(context, exc=None):
    if FLAGS.debug_mode:
        if exc is not None:
            warn(f"{context}: {exc}")
        else:
            warn(str(context))
