"""Private Linux profile leases; Chromium owns cookie and origin storage rules."""

from __future__ import annotations

import math
import os
import re
import stat
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event


class BrowserProfileError(RuntimeError):
    """Profile storage cannot be safely used."""


class ProfileBusyError(BrowserProfileError):
    """Another request or process owns this provider's persistent profile."""


class BrowserAccessCancelled(BrowserProfileError):
    """The caller cancelled before ownership could be acquired."""


@dataclass(frozen=True)
class BrowserProfile:
    provider_id: str
    path: Path = field(repr=False)
    persistent: bool = True


def _check_cancel(cancel: Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise BrowserAccessCancelled("Browser access cancelled")


@contextmanager
def _private_directory(path: Path | str, *, parent_fd: int | None = None) -> Iterator[int]:
    try:
        try:
            os.mkdir(path, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
    except OSError:
        raise BrowserProfileError("Browser profile directory is unavailable or unsafe") from None
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise BrowserProfileError("Browser profile directories must be private and owned by this user")
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _profile_lock(directory_fd: int, provider_id: str, timeout: float,
                  cancel: Event | None) -> Iterator[None]:
    import fcntl

    try:
        fd = os.open(provider_id + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=directory_fd)
    except OSError:
        raise BrowserProfileError("Browser profile lock is unavailable or unsafe") from None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise BrowserProfileError("Browser profile lock must be a private, unshared regular file")
        deadline = time.monotonic() + timeout
        while True:
            _check_cancel(cancel)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProfileBusyError("Browser profile is already in use") from None
                if cancel is None:
                    time.sleep(min(0.05, remaining))
                else:
                    cancel.wait(min(0.05, remaining))
        # Closing this descriptor releases only this lease's lock. Never unlink it:
        # waiters must continue locking the same inode, not a replacement file.
        _check_cancel(cancel)
        yield
    finally:
        os.close(fd)


class BrowserProfiles:
    """Caller supplies a trusted private root; leases outlive owned browser work."""

    def __init__(self, root: Path):
        self.root = Path(os.path.abspath(root))

    @contextmanager
    def lease(self, provider_id: str, *, incognito: bool = False, timeout: float = 0,
              cancel: Event | None = None) -> Iterator[BrowserProfile]:
        if not isinstance(provider_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", provider_id):
            raise ValueError("Invalid browser provider namespace")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError("Browser profile timeout must be finite and nonnegative")
        if not isinstance(incognito, bool):
            raise ValueError("Incognito must be boolean")
        _check_cancel(cancel)
        if sys.platform != "linux":
            raise BrowserProfileError("Shared browser profiles currently require Linux")
        if self.root.resolve() != self.root:
            raise BrowserProfileError("Browser profile paths must not traverse symlinks")
        # The root's parent is application-owned/trusted. No personal browser
        # directory or saved profile is opened in an incognito lease.
        with _private_directory(self.root) as root_fd:
            if incognito:
                with _private_directory("incognito", parent_fd=root_fd):
                    with tempfile.TemporaryDirectory(prefix=provider_id + "-", dir=self.root / "incognito") as folder:
                        _check_cancel(cancel)
                        yield BrowserProfile(provider_id, Path(folder), persistent=False)
                return
            with _private_directory("locks", parent_fd=root_fd) as lock_fd:
                with _profile_lock(lock_fd, provider_id, timeout, cancel):
                    with _private_directory("profiles", parent_fd=root_fd) as profiles_fd:
                        with _private_directory(provider_id, parent_fd=profiles_fd):
                            _check_cancel(cancel)
                            yield BrowserProfile(provider_id, self.root / "profiles" / provider_id)
