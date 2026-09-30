"""Secure and atomic filesystem operations."""

import json
import os
import tempfile


def atomic_write_json(path, data, indent=None, disabled=False):
    if disabled:
        return False
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(path)}.",
        suffix=".tmp",
        dir=directory,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=indent)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        return True
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        try:
            os.remove(temp_path)
        except Exception:
            pass
        raise


def write_private_text(directory, filename, content):
    os.makedirs(directory, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    path = os.path.join(directory, os.path.basename(filename))
    fd, temp_path = tempfile.mkstemp(
        prefix=".log.",
        suffix=".tmp",
        dir=directory,
        text=True,
    )
    try:
        os.chmod(temp_path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            if content and not content.endswith("\n"):
                handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
        return path
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        try:
            os.remove(temp_path)
        except Exception:
            pass
        raise
