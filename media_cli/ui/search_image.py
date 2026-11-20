"""Optional local landing image, rendered once per file/size with Chafa."""
from functools import lru_cache
import logging
import os
from pathlib import Path
import subprocess


@lru_cache(maxsize=16)
def _render(path, stamp, size_bytes, columns, rows):
    try:
        result = subprocess.run([
            "chafa", "--relative=on", "--probe=off", "--animate=off",
            "--colors=full", f"--size={columns}x{rows}", "--", path,
        ], capture_output=True, text=True, timeout=2, check=True)
        from .covers import poster_uses_native_protocol
        if poster_uses_native_protocol(result.stdout):
            return (result.stdout,) + ("",) * (rows - 1)
        return tuple(result.stdout.splitlines())
    except (OSError, subprocess.SubprocessError, UnicodeError) as error:
        logging.getLogger(__name__).debug("Search image unavailable: %s", error)
        return ()


def search_image_width(config, width):
    try:
        requested = int(config.get("search_image_size", 15))
    except (TypeError, ValueError, OverflowError):
        requested = 15
    return min(max(8, min(30, requested)), max(1, width - 6))


def search_image_lines(config, width, height):
    enabled = str(config.get("search_image", True)).strip().lower()
    if enabled in {"false", "off", "no", "0"} or height < 28 or width < 30:
        return ()
    columns = search_image_width(config, width)
    rows = min(8, max(1, height - 24))
    default = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "anyplay/search-image.png"
    path = Path(str(config.get("search_image_path") or default)).expanduser()
    try:
        stat = path.stat()
        if not path.is_file():
            return ()
        return _render(str(path), stat.st_mtime_ns, stat.st_size, columns, rows)
    except (OSError, ValueError):
        return ()
