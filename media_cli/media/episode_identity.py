"""Bounded, read-only probing of embedded Anyplay download identity."""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import subprocess
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from functools import lru_cache

from ..core.processes import read_bounded_process_stdout

_LOG = logging.getLogger(__name__)
_TAG_NAMES = ("anyplay_title", "anyplay_episode", "anyplay_title_id", "anyplay_episode_id")
_Fingerprint = tuple[int, int, int, int, int]


@dataclass(frozen=True)
class EmbeddedEpisodeIdentity:
    title: str
    episode: str
    title_id: str
    episode_id: str


def _fingerprint(info: os.stat_result) -> _Fingerprint:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def episode_file_fingerprint(path: str) -> _Fingerprint | None:
    """Identify a file version without reading tags or including access time."""
    try:
        return _fingerprint(os.stat(path))
    except OSError:
        return None


def _decode_identity(data: object) -> EmbeddedEpisodeIdentity | None:
    if not isinstance(data, dict) or not isinstance(data.get("format"), dict):
        return None
    tags = data["format"].get("tags")
    if not isinstance(tags, dict):
        return None
    values: dict[str, str] = {}
    for key, value in tags.items():
        key = key.casefold()
        if key not in _TAG_NAMES:
            continue
        if (not isinstance(value, str) or len(value) > 4096
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            return None
        value = value.strip()
        if key in values and values[key] != value:
            return None
        values[key] = value
    if not any(values.values()):
        return None
    return EmbeddedEpisodeIdentity(*(values.get(key, "") for key in _TAG_NAMES))


@lru_cache(maxsize=512)
def _probe_identity(path: str, executable: str, fingerprint: _Fingerprint) -> EmbeddedEpisodeIdentity | None:
    process = subprocess.Popen(
        [executable, "-v", "error", "-protocol_whitelist", "file",
         "-show_entries", "format_tags=" + ",".join(_TAG_NAMES), "-of", "json", "-i", path],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    output = read_bounded_process_stdout(process, timeout=3, max_bytes=65536)
    if process.returncode != 0:
        raise RuntimeError(f"Identity probe exited with status {process.returncode}")
    return _decode_identity(json.loads(output))


def read_episode_identity(path: str | os.PathLike[str]) -> EmbeddedEpisodeIdentity | None:
    """Return supplied tags, never infer identity or treat partial tags as proof.

    Successful reads and absent tags are cached by file identity and change
    timestamps. Operational failures remain retryable. No database writes occur.
    """
    local_path = os.path.realpath(os.path.expanduser(os.fspath(path)))
    try:
        info = os.stat(local_path)
        if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
            return None
        executable = shutil.which("ffprobe")
        if executable is None:
            return None
        fingerprint = _fingerprint(info)
        identity = _probe_identity(local_path, executable, fingerprint)
        # Never attach the old file's tags to a concurrently replaced file.
        if _fingerprint(os.stat(local_path)) != fingerprint:
            return None
        return identity
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        _LOG.debug("Embedded episode identity unavailable: path=%r", local_path, exc_info=True)
        return None


def clear_identity_cache() -> None:
    """Drop cached read results without changing files or stored downloads."""
    _probe_identity.cache_clear()


def read_episode_identities(
    paths: Iterable[str | os.PathLike[str]],
) -> dict[str, EmbeddedEpisodeIdentity | None]:
    """Read one bounded batch; failed reads remain retryable in later batches."""
    from ..core.workers import DaemonExecutor

    def unique_paths() -> Iterator[str]:
        seen: set[str] = set()
        for path in paths:
            key = os.fspath(path)
            if key not in seen:
                seen.add(key)
                yield key

    def read(path: str) -> tuple[str, EmbeddedEpisodeIdentity | None]:
        return path, read_episode_identity(path)

    with DaemonExecutor(max_workers=4, max_pending=4) as executor:
        return dict(executor.map(read, unique_paths()))


def download_identity_conflicts(
    identifier: str, path: str, *,
    identity_reader: Callable[[str], EmbeddedEpisodeIdentity | None] | None = None,
) -> bool:
    """Prefer supplied embedded episode identity over a renamed filename."""
    from ..domain.episodes import episode_file_identity_conflicts

    identity = (identity_reader or read_episode_identity)(path)
    if identity is not None:
        if identity.episode_id and str(identifier) == identity.episode_id:
            return False
        if identity.episode:
            return episode_file_identity_conflicts(identifier, f"Episode {identity.episode}")
    return episode_file_identity_conflicts(identifier, path)


def find_matching_episode_identity(
    paths: Iterable[str], matches: Callable[[EmbeddedEpisodeIdentity], bool], *, timeout: float = 4.0,
) -> str | None:
    """Find a tagged file with four read-only probes and a bounded lookup wait."""
    import time
    from concurrent.futures import FIRST_COMPLETED, wait
    from ..core.workers import DaemonExecutor

    deadline = time.monotonic() + timeout
    executor = DaemonExecutor(max_workers=4, max_pending=4)
    iterator = iter(dict.fromkeys(paths))
    pending = {}

    def submit():
        path = next(iterator, None)
        if path is not None:
            pending[executor.submit(read_episode_identity, path)] = path

    try:
        for _ in range(4):
            submit()
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            done, _ = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            if not done:
                break
            for future in done:
                path = pending.pop(future)
                identity = future.result()
                if identity is not None and matches(identity):
                    return path
                submit()
        return None
    finally:
        # Running probes own no application state; their existing probe deadline
        # still applies, but navigation need not wait for them on an expired lookup.
        executor.shutdown(wait=False, cancel_futures=True)
