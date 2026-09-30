"""Optional OS secret storage helpers."""

import shutil
import subprocess

SERVICE = "allmanga-cli"
ANILIST_KEY = "anilist_token"
TMDB_KEY = "tmdb_token"


def _secret_tool():
    return shutil.which("secret-tool")


def backend_path():
    return _secret_tool() or ""


def is_available():
    return bool(_secret_tool())


def get_secret(key):
    tool = _secret_tool()
    if not tool:
        return ""
    try:
        result = subprocess.run(
            [tool, "lookup", "service", SERVICE, "key", key],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except Exception:
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.rstrip("\n")


def set_secret(key, value):
    tool = _secret_tool()
    if not tool:
        return False
    try:
        result = subprocess.run(
            [tool, "store", "--label", f"{SERVICE} {key}", "service", SERVICE, "key", key],
            input=str(value),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except Exception:
        return False
    return result.returncode == 0


def delete_secret(key):
    tool = _secret_tool()
    if not tool:
        return False
    try:
        result = subprocess.run(
            [tool, "clear", "service", SERVICE, "key", key],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except Exception:
        return False
    return result.returncode == 0
