"""Bounded subprocess output and timeout cleanup."""

import subprocess
import threading

MAX_YTDLP_JSON_BYTES = 8 * 1024 * 1024


def communicate_with_cleanup(process, timeout, shutdown_timeout=2):
    try:
        return process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.communicate(timeout=shutdown_timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
        raise


def _stop_process(process, shutdown_timeout=2):
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=shutdown_timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _close_process_stdout(process):
    stdout = getattr(process, "stdout", None)
    if stdout is not None:
        try:
            stdout.close()
        except Exception:
            pass


def read_bounded_process_stdout(
    process,
    timeout,
    max_bytes=MAX_YTDLP_JSON_BYTES,
    shutdown_timeout=2,
):
    max_bytes = max(1, int(max_bytes))
    chunks = []
    reader_errors = []
    oversized = threading.Event()

    def reader():
        remaining = max_bytes + 1
        try:
            while remaining > 0:
                chunk = process.stdout.read(min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            if sum(len(chunk) for chunk in chunks) > max_bytes:
                oversized.set()
                try:
                    process.kill()
                except Exception:
                    pass
        except Exception as exc:
            reader_errors.append(exc)

    reader_thread = threading.Thread(
        target=reader,
        name="bounded-process-stdout",
        daemon=True,
    )
    reader_thread.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _stop_process(process, shutdown_timeout)
        reader_thread.join(shutdown_timeout)
        _close_process_stdout(process)
        raise

    reader_thread.join(shutdown_timeout)
    if reader_thread.is_alive():
        _stop_process(process, shutdown_timeout)
        _close_process_stdout(process)
        raise RuntimeError("Subprocess output reader did not stop")
    _close_process_stdout(process)
    if reader_errors:
        raise reader_errors[0]

    output = b"".join(chunks)
    if oversized.is_set() or len(output) > max_bytes:
        raise ValueError("yt-dlp JSON output is too large")
    return output


_active_subprocesses = []


def register_subprocess(proc):
    if proc not in _active_subprocesses:
        _active_subprocesses.append(proc)


def unregister_subprocess(proc):
    if proc in _active_subprocesses:
        _active_subprocesses.remove(proc)


def kill_active_subprocesses():
    import os
    import signal
    for proc in _active_subprocesses:
        try:
            if os.name == 'posix':
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except Exception:
            pass


def is_termux():
    import os
    return (os.environ.get("PREFIX", "").startswith("/data/data/com.termux")
            or os.path.exists("/data/data/com.termux"))


def check_deps():
    import shutil
    import sys

    from .reporting import err
    needed = ["openssl"] + (["am"] if is_termux() else ["mpv"])
    for p in needed:
        if not shutil.which(p):
            err(f"'{p}' not found.")
            sys.exit(1)


