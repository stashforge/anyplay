"""Run the strict migrated-module gate, or the full legacy diagnostic audit."""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (
    "media_cli/ui/footer.py",
    "media_cli/ui/fuzzy.py",
    "media_cli/core/network.py",
    "media_cli/core/workers.py",
    "media_cli/ui/view_model.py",
    "media_cli/ui/picker_state.py",
    "media_cli/ui/view_session.py",
    "media_cli/ui/terminal_runtime.py",
    "media_cli/ui/transition.py",
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true", help="Report full-project diagnostics; not a passing gate yet")
    args = parser.parse_args(argv)
    if importlib.util.find_spec("mypy") is None:
        print('mypy is required. Install developer tools with: python -m pip install -e ".[dev]"', file=sys.stderr)
        return 2
    command = [sys.executable, "-m", "mypy", "--config-file", str(ROOT / "pyproject.toml"), "--show-error-codes"]
    if args.audit:
        command.append("media_cli")
    else:
        command.extend(("--follow-imports=silent", *TARGETS))
    return subprocess.run(command, cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())
