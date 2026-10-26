"""Linux Chromium ownership; never attach to or terminate a personal browser."""

from __future__ import annotations

import math
import os
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

from ...core.api import ProviderDependencyError
from .bridge import BrowserBridge
from .policy import BrowserPolicy
from .profiles import BrowserAccessCancelled, BrowserProfile


class BrowserProcessError(RuntimeError):
    """The private browser could not be launched, controlled or stopped."""


class BrowserSetupRequired(BrowserProcessError, ProviderDependencyError):
    """Known prerequisites are unavailable; verification itself has not failed."""


class BrowserWindowNotReady(BrowserProcessError):
    """The helper encountered a transient window/tab operation failure."""


class _RequestCancellation(threading.Event):
    """Bridge checks is_set only; combine process exit and caller cancellation."""

    def __init__(self, closed: threading.Event, caller: threading.Event | None):
        super().__init__()
        self.closed, self.caller = closed, caller

    def is_set(self) -> bool:
        return self.closed.is_set() or (self.caller is not None and self.caller.is_set())


class OwnedChromium:
    """Hold the profile/extension contexts outside this owner, until it exits.

    Launch is lazy, native app mode and quiet-first (minimized, not headless).
    Minimization may flash or leave a taskbar entry; it is not invisibility.
    """

    def __init__(self, profile: BrowserProfile, policy: BrowserPolicy, extension: Path,
                 startup_url: str, bridge: BrowserBridge, *, shutdown_seconds: float = 3):
        if (isinstance(shutdown_seconds, bool) or not isinstance(shutdown_seconds, (int, float))
                or not math.isfinite(shutdown_seconds) or not 0 < shutdown_seconds <= 10):
            raise ValueError("Invalid browser shutdown deadline")
        self.profile, self.policy = profile, policy
        self.extension, self.startup_url, self.bridge = Path(extension), startup_url, bridge
        self.shutdown_seconds = shutdown_seconds
        self.closed = threading.Event()
        self._exited = threading.Event()
        self._observation_failed = threading.Event()
        self._process: subprocess.Popen[bytes] | None = None
        self._observer: threading.Thread | None = None
        self._lock = threading.Lock()
        self._started = False
        self._cleaned = False

    def _arguments(self) -> list[str]:
        if sys.platform != "linux" or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            raise BrowserSetupRequired(
                "Browser verification requires a Linux desktop session. "
                "Run from the desktop, or choose a provider that does not need browser verification."
            )
        self.policy.validate_url(self.startup_url)
        if self.profile.provider_id != self.policy.provider_id:
            raise BrowserProcessError("Browser profile and provider do not match")
        try:
            for path in (self.profile.path, self.extension):
                info = path.lstat()
                if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                        or stat.S_IMODE(info.st_mode) != 0o700 or path.resolve() != path):
                    raise OSError
            if self.extension.parent != self.profile.path:
                raise OSError
        except OSError:
            raise BrowserProcessError("Browser requires a private owned profile and helper") from None
        executable = shutil.which("chromium") or shutil.which("chromium-browser")
        if not executable:
            raise BrowserSetupRequired(
                "Chromium is required for browser verification. "
                "Install Chromium using your system package manager, then retry."
            )
        return [executable, f"--user-data-dir={self.profile.path}",
                f"--load-extension={self.extension}", f"--disable-extensions-except={self.extension}",
                "--no-first-run", "--no-default-browser-check", "--no-startup-window",
                "--window-size=760,640"]

    def __enter__(self) -> OwnedChromium:
        with self._lock:
            if self._started or self._cleaned:
                raise BrowserProcessError("Browser owner cannot be restarted or entered twice")
            args = self._arguments()
            self._started = True
            try:
                self._process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                 stderr=subprocess.DEVNULL, close_fds=True, start_new_session=True)
            except (FileNotFoundError, PermissionError):
                self.closed.set()
                raise BrowserSetupRequired(
                    "Chromium could not be executed. Check its installation and executable permissions, then retry."
                ) from None
            except OSError:
                self.closed.set()
                raise BrowserProcessError("Browser could not start") from None
            try:
                self._observer = threading.Thread(target=self._observe_exit, daemon=True)
                self._observer.start()
            except BaseException as error:
                self.closed.set()
                self._stop_process()
                self._cleaned = True
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
                raise BrowserProcessError("Browser exit observer could not start") from None
        return self

    def _observe_exit(self) -> None:
        assert self._process is not None
        try:
            # Keep the exited leader unreaped until group cleanup finishes. Its
            # PID/PGID cannot be recycled into an unrelated process meanwhile.
            os.waitid(os.P_PID, self._process.pid, os.WEXITED | os.WNOWAIT)
        except OSError:
            self._observation_failed.set()
        finally:
            self._exited.set()
            self.closed.set()

    def __exit__(self, *args) -> None:
        self.close()

    def _signal_group(self, sig: signal.Signals) -> None:
        assert self._process is not None
        if self._observation_failed.is_set():
            raise BrowserProcessError("Owned browser process identity is unavailable")
        try:
            # Only the fresh session group returned by our own Popen. No name,
            # executable or profile searches, and never signal a caller's group.
            os.killpg(self._process.pid, sig)
        except ProcessLookupError:
            pass
        except OSError:
            raise BrowserProcessError("Owned browser process group could not be stopped") from None

    def _stop_process(self) -> None:
        if self._process is None:
            return
        self._signal_group(signal.SIGTERM)
        # The leader may exit before children finish flushing cookies/files.
        # They receive the full bounded grace, not just the leader's lifetime.
        time.sleep(self.shutdown_seconds)
        # Finish descendants that ignored TERM before reaping the group leader,
        # even if the leader itself exited promptly.
        self._signal_group(signal.SIGKILL)
        if self._observer is not None and self._observer.is_alive():
            self._observer.join(timeout=self.shutdown_seconds)
            if self._observer.is_alive():
                raise BrowserProcessError("Browser exit observer did not stop")
        try:
            self._process.wait(timeout=self.shutdown_seconds)
        except subprocess.TimeoutExpired:
            raise BrowserProcessError("Owned browser did not exit after cancellation") from None

    def close(self) -> None:
        with self._lock:
            if self._cleaned:
                return
            self.closed.set()
            self._stop_process()
            if self._observer is not None and self._observer.is_alive():
                self._observer.join(timeout=self.shutdown_seconds)
                if self._observer.is_alive():
                    raise BrowserProcessError("Browser exit observer did not stop")
            self._cleaned = True

    def set_window_state(self, state: str, *, timeout: float = 5,
                         cancel: threading.Event | None = None) -> None:
        if state not in ("normal", "minimized"):
            raise BrowserProcessError("Unsupported browser window state")
        cancellation = _RequestCancellation(self.closed, cancel)
        if cancellation.is_set():
            raise BrowserAccessCancelled("Browser verification cancelled")
        if not self._started or self._process is None:
            raise BrowserProcessError("Browser is not running")
        reply = self.bridge.request({"operation": "window", "state": state}, timeout=timeout, cancel=cancellation)
        if cancellation.is_set() or reply == {"error": "browser_closed"}:
            raise BrowserAccessCancelled("Browser verification cancelled")
        if reply == {"error": "browser_operation_failed"}:
            raise BrowserWindowNotReady("Browser window is not ready")
        if reply != {"state": state}:
            raise BrowserProcessError("Browser window state could not be changed")
