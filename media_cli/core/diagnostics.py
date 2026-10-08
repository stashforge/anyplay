"""Rotating file diagnostics for normal and debug runs."""

from __future__ import annotations

import logging
import os
from collections import deque
from logging.handlers import RotatingFileHandler
from typing import Iterable

from ..state import paths

DEFAULT_LOG_MAX_BYTES = 1_048_576
DEFAULT_LOG_BACKUP_COUNT = 5

_HANDLER_NAME = "anyplay-file-diagnostics"
_file_handler: RotatingFileHandler | None = None
_previous_root_level: int | None = None


def get_log_paths() -> dict[str, str]:
    """Return the primary log file paths."""
    return {
        "anyplay": os.path.join(paths.LOG_DIR, "anyplay.log"),
        "crash": os.path.join(paths.LOG_DIR, "crash.log"),
    }


def configure_file_logging(
    *,
    debug: bool = False,
    max_bytes: int = DEFAULT_LOG_MAX_BYTES,
    backup_count: int = DEFAULT_LOG_BACKUP_COUNT,
) -> str:
    """Attach a rotating log handler.

    Normal mode records warnings and errors. Debug mode records debug and above.
    The returned path is the primary log file.
    """
    global _file_handler, _previous_root_level

    log_path = get_log_paths()["anyplay"]
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    root = logging.getLogger()
    if _previous_root_level is None:
        _previous_root_level = root.level

    if _file_handler is not None:
        root.removeHandler(_file_handler)
        try:
            _file_handler.close()
        except Exception:
            pass
        _file_handler = None

    level = logging.DEBUG if debug else logging.WARNING
    handler = RotatingFileHandler(
        log_path,
        maxBytes=max(1, int(max_bytes)),
        backupCount=max(0, int(backup_count)),
        encoding="utf-8",
    )
    handler.name = _HANDLER_NAME
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(handler)
    if root.level > level:
        root.setLevel(level)
    _file_handler = handler
    return log_path


def reset_file_logging_for_tests() -> None:
    """Remove the Anyplay rotating handler and restore root logging state."""
    global _file_handler, _previous_root_level

    root = logging.getLogger()
    if _file_handler is not None:
        root.removeHandler(_file_handler)
        try:
            _file_handler.close()
        except Exception:
            pass
        _file_handler = None
    if _previous_root_level is not None:
        root.setLevel(_previous_root_level)
        _previous_root_level = None


def tail_file(path: str, lines: int = 50) -> list[str]:
    """Read the last ``lines`` lines from a UTF-8 text file."""
    if lines <= 0:
        return []
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return list(deque(fh, maxlen=lines))


def append_rotating_text(
    path: str,
    content: str,
    *,
    max_bytes: int = DEFAULT_LOG_MAX_BYTES,
    backup_count: int = DEFAULT_LOG_BACKUP_COUNT,
) -> str:
    """Append text to a file with simple size-based rotation."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    max_bytes = max(1, int(max_bytes))
    backup_count = max(0, int(backup_count))
    encoded_size = len(content.encode("utf-8", errors="replace"))
    if os.path.exists(path) and os.path.getsize(path) + encoded_size > max_bytes:
        if backup_count > 0:
            oldest = f"{path}.{backup_count}"
            if os.path.exists(oldest):
                os.remove(oldest)
            for idx in range(backup_count - 1, 0, -1):
                src = f"{path}.{idx}"
                dst = f"{path}.{idx + 1}"
                if os.path.exists(src):
                    os.replace(src, dst)
            os.replace(path, f"{path}.1")
        else:
            os.remove(path)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(content)
        if not content.endswith("\n"):
            fh.write("\n")
    return path


def format_log_summary(log_paths: dict[str, str] | None = None) -> str:
    paths_map = log_paths or get_log_paths()
    ordered: Iterable[tuple[str, str]] = (
        ("anyplay", paths_map.get("anyplay", "")),
        ("crash", paths_map.get("crash", "")),
    )
    rows = ["Anyplay logs:"]
    for name, path in ordered:
        status = "exists" if path and os.path.exists(path) else "not created yet"
        rows.append(f"  {name}: {path} ({status})")
    return "\n".join(rows)
