"""Exercise installed-runtime startup and keep declared tooling targets aligned."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]


def test_supported_python_floor_matches_static_checks():
    with (ROOT / "pyproject.toml").open("rb") as source:
        project = tomllib.load(source)
    assert project["project"]["requires-python"] == ">=3.10"
    assert project["tool"]["ruff"]["target-version"] == "py310"
    assert project["tool"]["mypy"]["python_version"] == "3.10"


@pytest.mark.parametrize("arguments", (("--help",), ("--version",), ("search", "--help"), ("providers", "--plain")))
def test_cli_starts_on_running_python(tmp_path, arguments):
    env = dict(
        os.environ,
        HOME=str(tmp_path),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
        XDG_STATE_HOME=str(tmp_path / "state"),
        ALLMANGA_NO_OS_EXIT="1",
        PYTHONPATH=str(ROOT),
    )
    result = subprocess.run(
        [sys.executable, "-m", "media_cli", *arguments],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr


def test_download_worker_forward_annotation_resolves():
    from typing import get_type_hints

    from media_cli.media.download_worker import DownloadItem, _append_progress_log

    assert get_type_hints(_append_progress_log)["item"] is DownloadItem
