"""Terminal display: alt-screen, posters, spinner + loading overlays.

Owns the terminal-presentation state that used to live in app_core: the
alt-screen flag, the configured spinner style, the PosterManager instance,
and the threaded loading overlays drawn while blocking work runs.

Dependencies point downward only:

    ui.display -> ui.poster / ui.picker_render / ui.spinner /
                  ui.terminal_images / ui.anilist_menu
    ui.display -> core.storage (cover cache dir), context.FLAGS

One upper-layer fact is injected via :func:`configure`:
``hovered_show_id_fn()`` — which show is currently hovered.  That state is
orchestrator/picker state owned by app_core (the app layer writes
``app_core._hovered_show_id``); the poster manager only ever needs to read
it, so it reads through the hook instead of display importing upward.

Alt-screen state is mutated from outside (picker enters the alt screen
itself) — those callers use :func:`set_alt_screen_active` rather than poking module
globals, so there is exactly one owner of the flag.
"""

import atexit
import os
import re
import sys
import termios
import threading
import time

from ..context import FLAGS
from ..core.storage import cover_cache_dir, cover_read_cache_dirs
from . import terminal_images
from .anilist_menu import loading_frame as _anilist_menu_loading_frame
from .picker_render import (
    loading_frame as _loading_frame,
)
from .poster import PosterManager
from .spinner import DEFAULT_SPINNER, spinner_from_config
from .spinner import loading_line as _loading_line

_hovered_show_id_fn = None


def configure(*, hovered_show_id_fn=None):
    """Inject the hovered-show lookup (see module docstring)."""
    global _hovered_show_id_fn
    if hovered_show_id_fn is not None:
        _hovered_show_id_fn = hovered_show_id_fn


def _hovered_show_id():
    return _hovered_show_id_fn() if _hovered_show_id_fn else None


# ---------------------------------------------------------------------------
# Spinner style
# ---------------------------------------------------------------------------

_spinner_style = DEFAULT_SPINNER


class suppress_terminal_echo:
    """Context manager to disable terminal echo and flush buffered input on exit."""
    def __enter__(self):
        try:
            self.fd = sys.stdin.fileno()
            self.old_attrs = termios.tcgetattr(self.fd)
            new_attrs = termios.tcgetattr(self.fd)
            new_attrs[3] = new_attrs[3] & ~(termios.ECHO | termios.ICANON)
            termios.tcsetattr(self.fd, termios.TCSANOW, new_attrs)
        except Exception:
            self.old_attrs = None
        return self

    def __exit__(self, *args):
        if getattr(self, "old_attrs", None) is not None:
            try:
                termios.tcflush(self.fd, termios.TCIFLUSH)
                termios.tcsetattr(self.fd, termios.TCSANOW, self.old_attrs)
            except Exception:
                pass


def _configured_loading_frame():
    return _loading_frame(_spinner_style)


def _configure_spinner_from_config(cfg):
    global _spinner_style
    _spinner_style = spinner_from_config(cfg)


# ---------------------------------------------------------------------------
# Poster manager
# ---------------------------------------------------------------------------

def _request_poster_redraw():
    import media_cli.ui.picker as _picker_mod
    _picker_mod._needs_redraw = True


_poster_manager = PosterManager(
    enabled=lambda: bool(FLAGS.show_image),
    cache_dir=cover_cache_dir,
    read_cache_dirs=cover_read_cache_dirs,
    hovered_show_id=_hovered_show_id,
    request_redraw=_request_poster_redraw,
    loading_frame=_configured_loading_frame,
)
try:
    import media_cli.ui.picker as _picker_mod
    _picker_mod._set_poster_tick_fn(_poster_manager.needs_tick)
except Exception:
    pass


def _clear_poster_downloads():
    _poster_manager.clear_downloads()


def clear_terminal_images():
    sys.stdout.write(terminal_images.clear_now())
    sys.stdout.flush()


def _poster_footer_line(show, default_text, width):
    # If default_text is a toast feedback message (not navigation shortcuts), show only the message
    clean_text = re.sub(r'\033\[[0-9;]*m', '', str(default_text or "")).strip()
    is_nav_footer = any(k in clean_text.lower() for k in ("enter", "left", "esc", "tab", "result"))
    if not is_nav_footer or clean_text.startswith('*') or clean_text.startswith('✓') or clean_text.startswith('✔'):
        return _poster_manager.footer_line(show, default_text, width)

    # Inject Provider name into navigation footer line only when show is actually linked to a provider
    provider_name = ""
    if show and isinstance(show, dict) and (show.get("_provider") or show.get("provider")) and show.get("_has_provider_link") is not False:
        provider_name = show.get("_provider_name") or (show.get("_provider") or show.get("provider") or "").title()

    if provider_name and provider_name not in default_text and "result(s)" not in default_text:
        default_text = f"{provider_name} • {default_text}"

    return _poster_manager.footer_line(show, default_text, width)


def _poster_needs_tick(show):
    return _poster_manager.needs_tick(show)


def _get_poster(show):
    globals()["_hovered_show_obj"] = show
    return _poster_manager.get(show)


# ---------------------------------------------------------------------------
# Alt screen
# ---------------------------------------------------------------------------

_alt_screen_active = False


def set_alt_screen_active(active):
    """External callers (e.g. picker) record alt-screen state
    changes they perform themselves."""
    global _alt_screen_active
    _alt_screen_active = bool(active)


def enter_alt_screen():
    from ..context import FLAGS
    if getattr(FLAGS, "plain_mode", False) or not sys.stdin.isatty():
        return
    global _alt_screen_active
    pending_image_clear = terminal_images.clear_if_active()
    if not _alt_screen_active:
        sys.stdout.write(pending_image_clear + "\033[?1049h\033[2J\033[?25l")
        sys.stdout.flush()
        _alt_screen_active = True
    elif pending_image_clear:
        sys.stdout.write(pending_image_clear)
        sys.stdout.flush()


_INITIAL_TERMIOS_ATTRS = None
try:
    if sys.stdin.isatty():
        _INITIAL_TERMIOS_ATTRS = termios.tcgetattr(sys.stdin.fileno())
except Exception:
    pass


def exit_alt_screen():
    from ..context import FLAGS
    if getattr(FLAGS, "plain_mode", False) or not sys.stdin.isatty():
        return
    global _alt_screen_active
    if _alt_screen_active:
        try:
            from .picker import invalidate_screen_buffer
            invalidate_screen_buffer()
        except Exception:
            pass
        sys.stdout.write("\033[?1049l\033[?25h")
        sys.stdout.flush()
        _alt_screen_active = False


def restore_terminal():
    """Fully restore terminal echo, canonical mode, cursor, alt screen, and clear active terminal images."""
    try:
        terminal_images.clear_if_active()
    except Exception:
        pass
    try:
        exit_alt_screen()
    except Exception:
        pass
    # Force exit alt-screen sequence unconditionally to ensure normal buffer is restored
    reset_seq = "\033[?1049l\033[?25h\033[0m"
    for stream in (sys.__stdout__, sys.stdout, sys.__stderr__, sys.stderr):
        try:
            if stream and not stream.closed:
                stream.write(reset_seq)
                stream.flush()
        except Exception:
            pass
    try:
        with open("/dev/tty", "w") as tty:
            tty.write(reset_seq)
            tty.flush()
    except Exception:
        pass
    try:
        if sys.stdin.isatty() and _INITIAL_TERMIOS_ATTRS is not None:
            fd = sys.stdin.fileno()
            termios.tcflush(fd, termios.TCIFLUSH)
            termios.tcsetattr(fd, termios.TCSANOW, _INITIAL_TERMIOS_ATTRS)
    except Exception:
        pass
    try:
        chapters_path = "/storage/emulated/0/Mpv/chapters.txt"
        if os.path.exists(chapters_path):
            os.remove(chapters_path)
    except Exception:
        pass


def fatal_terminal_exit(message: str, code: int = 1):
    """Restore terminal completely from alt-screen and print message on the primary terminal."""
    try:
        if sys.__stderr__ and not sys.__stderr__.closed:
            sys.stderr = sys.__stderr__
    except Exception:
        pass
    try:
        if sys.__stdout__ and not sys.__stdout__.closed:
            sys.stdout = sys.__stdout__
    except Exception:
        pass

    restore_terminal()
    time.sleep(0.02)

    clean_msg = f"\n{message.strip()}\n\n"
    written = False
    target_stream = sys.__stderr__ if (sys.__stderr__ and not sys.__stderr__.closed) else sys.stderr
    try:
        if target_stream and not target_stream.closed:
            target_stream.write(clean_msg)
            target_stream.flush()
            written = True
    except Exception:
        pass
    if not written:
        try:
            with open("/dev/tty", "w") as tty:
                tty.write(clean_msg)
                tty.flush()
        except Exception:
            pass
    time.sleep(0.05)
    os._exit(code)


atexit.register(restore_terminal)


# ---------------------------------------------------------------------------
# Loading overlays
# ---------------------------------------------------------------------------

def with_loading(msg, fn, *args, **kwargs):
    from ..context import FLAGS
    if getattr(FLAGS, "plain_mode", False) or not sys.stdin.isatty():
        return fn(*args, **kwargs)

    spinner_style = kwargs.pop("_spinner_style", _spinner_style)
    try:
        ts = os.get_terminal_size()
        w, h = ts.columns, ts.lines
    except OSError:
        w, h = 80, 24

    from ..core import reporting
    from ..core.terminal import sanitize_terminal_text

    current_msg = [str(msg)]

    def set_status(new_msg):
        if new_msg:
            current_msg[0] = str(new_msg)

    def _with_loading_sink(formatted_message, ansi_color):
        clean_text = sanitize_terminal_text(formatted_message)
        if clean_text:
            clean_sub = (
                clean_text
                .replace("[INFO] ", "")
                .replace("[OK] ", "")
                .replace("[WARN] ", "")
                .replace("[ERR] ", "")
                .strip()
            )
            if clean_sub:
                current_msg[0] = f"{msg} ({clean_sub})"
        return True

    old_sink = reporting._status_sink
    reporting.set_status_sink(_with_loading_sink)

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    result = {}

    def _runner():
        try:
            import inspect
            try:
                sig = inspect.signature(fn)
                if "status_cb" in sig.parameters:
                    kwargs["status_cb"] = set_status
            except Exception:
                pass
            result["value"] = fn(*args, **kwargs)
        except BaseException as exc:
            result["error"] = exc

    try:
        termios.tcflush(fd, termios.TCIFLUSH)
        cbreak_attr = termios.tcgetattr(fd)
        cbreak_attr[3] = cbreak_attr[3] & ~(termios.ECHO | termios.ICANON)
        termios.tcsetattr(fd, termios.TCSANOW, cbreak_attr)

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
        while thread.is_alive():
            sys.stdout.write(
                f"\033[{h};1H\033[2K"
                f"{_loading_line(current_msg[0], w, spinner_style)}"
                "\033[?25l"
            )
            sys.stdout.flush()
            thread.join(0.1)
        if "error" in result:
            raise result["error"]
        return result.get("value")
    finally:
        if not _alt_screen_active:
            sys.stdout.write(f"\033[{h};1H\033[2K")
        try:
            from .picker import invalidate_screen_row
            invalidate_screen_row(h)
        except Exception:
            pass
        reporting.set_status_sink(old_sink)
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        termios.tcflush(fd, termios.TCIFLUSH)
        sys.stdout.flush()


def render_anilist_menu_loading(status, loading_text=""):
    enter_alt_screen()
    try:
        size = os.get_terminal_size()
        columns, rows = size.columns, size.lines
    except OSError:
        columns, rows = 80, 24
    sys.stdout.write(
        "\033[?25l" + _anilist_menu_loading_frame(
            status, rows, columns, loading_text
        )
    )
    sys.stdout.flush()


def with_anilist_menu_loading(status, msg, fn, *args, **kwargs):
    result = {}

    def _runner():
        try:
            result["value"] = fn(*args, **kwargs)
        except BaseException as exc:
            result["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    while thread.is_alive():
        try:
            width = os.get_terminal_size().columns
        except OSError:
            width = 80
        render_anilist_menu_loading(
            status,
            _loading_line(msg, width, _spinner_style),
        )
        thread.join(0.1)
    if "error" in result:
        raise result["error"]
    return result.get("value")


def with_footer_loading(msg, fn, *args, **kwargs):
    result = {}

    def _runner():
        try:
            result["value"] = fn(*args, **kwargs)
        except BaseException as exc:
            result["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    while thread.is_alive():
        try:
            size = os.get_terminal_size()
            columns, rows = size.columns, size.lines
        except OSError:
            columns, rows = 80, 24
        sys.stdout.write(
            f"\033[{rows};1H\033[2K"
            f"{_loading_line(msg, columns, _spinner_style)}"
            "\033[?25l"
        )
        sys.stdout.flush()
        thread.join(0.1)
    if "error" in result:
        raise result["error"]
    return result.get("value")
