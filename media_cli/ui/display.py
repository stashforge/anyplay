"""Terminal display: alt-screen, posters, spinner + loading overlays.

Owns the terminal-presentation state that used to live in app_core: the
alt-screen flag, the configured spinner style, the PosterManager instance,
and presentation adapters for the shared blocking-work loading driver.

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
import time
from itertools import count

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
from .terminal_runtime import terminal_application

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
_loading_activations = count(1)


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
    from .footer import Footer

    if isinstance(default_text, Footer):
        return _poster_manager.footer_line(show, default_text, width)

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


def is_alt_screen_active():
    return bool(_alt_screen_active)


@terminal_application
def enter_alt_screen():
    from ..context import FLAGS
    if getattr(FLAGS, "plain_mode", False) or not sys.stdin.isatty() or not sys.stdout.isatty():
        return
    global _alt_screen_active
    if not _alt_screen_active:
        pending_image_clear = terminal_images.clear_if_active()
        sys.stdout.write(pending_image_clear + "\033[?1049h\033[2J\033[?25l")
        sys.stdout.flush()
        _alt_screen_active = True


_INITIAL_TERMIOS_ATTRS = None
try:
    if sys.stdin.isatty():
        _INITIAL_TERMIOS_ATTRS = termios.tcgetattr(sys.stdin.fileno())
except Exception:
    pass


@terminal_application
def exit_alt_screen():
    from ..context import FLAGS
    if getattr(FLAGS, "plain_mode", False) or not sys.stdin.isatty() or not sys.stdout.isatty():
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


@terminal_application
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

def _invoke_loading_work(fn, args, kwargs, status_cb, *, preserve_callback=False):
    import inspect
    from ..core.terminal import sanitize_terminal_text

    def status(message):
        # Legacy callbacks are permissive; typed task records remain strict.
        if isinstance(message, (list, tuple)):
            status_cb(message)
        elif message:
            clean = sanitize_terminal_text(str(message)).strip()
            if clean:
                status_cb(clean)

    work_kwargs = dict(kwargs)
    try:
        if "status_cb" in inspect.signature(fn).parameters:
            if preserve_callback:
                work_kwargs.setdefault("status_cb", status)
            else:
                work_kwargs["status_cb"] = status
    except (TypeError, ValueError):
        pass
    return fn(*args, **work_kwargs)


def _run_loading_helper(msg, fn, args, kwargs, paint, *, preserve_callback=False):
    from .loading_model import LoadingInteraction, LoadingOwner, LoadingPlacement, LoadingTaskSpec
    from .loading_preparation import LoadingPreparation
    from .picker_actions import picker_bindings
    from .terminal_runtime import borrow_terminal
    from .transition import in_preparation, set_preparation_status

    spinner_style = kwargs.pop("_spinner_style", _spinner_style)
    if in_preparation():
        set_preparation_status(msg)
        return _invoke_loading_work(fn, args, kwargs, set_preparation_status, preserve_callback=True)
    if getattr(FLAGS, "plain_mode", False) or not sys.stdin.isatty() or not sys.stdout.isatty():
        return fn(*args, **kwargs)

    from ..core import reporting
    from ..core.terminal import sanitize_terminal_text

    with borrow_terminal() as runtime:
        if runtime is None:
            return fn(*args, **kwargs)
        runtime.assert_owner_thread()
        enter_alt_screen()
        painted_rows = set()

        def bindings():
            return runtime.input_bindings or picker_bindings()

        def render(snapshot):
            row = paint(snapshot, runtime.fileno(), spinner_style)
            if row is not None:
                painted_rows.add(row)

        spec = LoadingTaskSpec(
            "work", LoadingOwner("preparation", next(_loading_activations)), msg,
            LoadingInteraction.BLOCKING, LoadingPlacement.FOOTER, bindings(),
        )
        driver = LoadingPreparation(
            spec, fd=runtime.fileno(), render=render,
            active=is_alt_screen_active, bindings=bindings,
        )

        def status_sink(formatted_message, ansi_color):
            clean_text = sanitize_terminal_text(formatted_message)
            for prefix in ("[INFO] ", "[OK] ", "[WARN] ", "[ERR] "):
                clean_text = clean_text.replace(prefix, "")
            if clean_text.strip():
                from .message_choices import MAX_MESSAGE_LENGTH
                # Reports are legacy log text, not caller-supplied message choices.
                message = f"{msg} ({clean_text.strip()})" if isinstance(msg, str) else clean_text.strip()
                if len(message) > MAX_MESSAGE_LENGTH:
                    message = message[:MAX_MESSAGE_LENGTH - 3] + "..."
                driver.update(message)
            return True

        old_sink = reporting._status_sink
        reporting.set_status_sink(status_sink)

        def prepare(status):
            # Scope only the worker; child UI must be free to install its own sink.
            with reporting.status_sink_scope(status_sink):
                return _invoke_loading_work(
                    fn, args, kwargs, status, preserve_callback=preserve_callback,
                )

        try:
            return driver.run(prepare)
        finally:
            reporting.set_status_sink(old_sink)
            from .picker import invalidate_screen_row
            for row in painted_rows:
                invalidate_screen_row(row)
            sys.stdout.flush()


def _paint_footer_loading(snapshot, fd, spinner_style):
    try:
        size = os.get_terminal_size(fd)
        w, h = size.columns, size.lines
    except OSError:
        w, h = 80, 24
    os.write(fd, (
        f"\033[{h};1H\033[2K"
        f"{_loading_line(snapshot.message, w, spinner_style)}"
        "\033[?25l"
    ).encode())
    from .picker import invalidate_screen_row
    invalidate_screen_row(h)
    return h


def with_loading(msg, fn, *args, **kwargs):
    return _run_loading_helper(msg, fn, args, kwargs, _paint_footer_loading)


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
    def paint(snapshot, fd, spinner_style):
        try:
            width = os.get_terminal_size(fd).columns
        except OSError:
            width = 80
        render_anilist_menu_loading(status, _loading_line(snapshot.message, width, spinner_style))

    return _run_loading_helper(msg, fn, args, kwargs, paint, preserve_callback=True)


def with_footer_loading(msg, fn, *args, **kwargs):
    return with_loading(msg, fn, *args, **kwargs)
