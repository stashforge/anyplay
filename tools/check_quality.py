"""Check migrated code, optionally audit legacy code and run the full tests."""

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
    "tools",
    "tests/test_type_checking.py",
    "tests/test_quality_checking.py",
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", action="store_true", help="Report full-project debt instead of only migrated files")
    parser.add_argument("--tests", action="store_true", help="Also run the full pytest suite")
    args = parser.parse_args(argv)
    required = ("ruff", "mypy", "pytest") if args.tests else ("ruff", "mypy")
    missing = tuple(name for name in required if importlib.util.find_spec(name) is None)
    if missing:
        print(
            f'Missing developer tools: {", ".join(missing)}. Install with: python -m pip install -e ".[dev]"',
            file=sys.stderr,
        )
        return 2
    targets = ("media_cli", "tools", "tests") if args.audit else TARGETS
    commands = [
        ("Lint", (sys.executable, "-m", "ruff", "check", *targets)),
        ("Format", (sys.executable, "-m", "ruff", "format", "--check", *targets)),
        ("Types", (sys.executable, str(ROOT / "tools/check_types.py"), *(("--audit",) if args.audit else ()))),
    ]
    if args.tests:
        commands.append(("Tests", (sys.executable, "-m", "pytest", "-q")))
    result = 0
    for label, command in commands:
        print(f"\n{label}:", flush=True)
        code = subprocess.run(command, cwd=ROOT).returncode
        if code and not result:
            result = code
    return result


if __name__ == "__main__":
    raise SystemExit(main())
