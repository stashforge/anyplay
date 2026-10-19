"""Exercise the development gate with real checkers and broken fixtures."""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def quality_project(tmp_path):
    for name in (
        "pyproject.toml",
        "tools/check_types.py",
        "tools/check_quality.py",
        "tests/test_quality_checking.py",
        "tests/test_type_checking.py",
    ):
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    for package in ("media_cli", "media_cli/ui", "media_cli/core"):
        directory = tmp_path / package
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "__init__.py").write_text("", encoding="utf-8")
    for name in (
        "ui/footer.py",
        "ui/fuzzy.py",
        "core/network.py",
        "core/workers.py",
        "ui/view_model.py",
        "ui/picker_state.py",
        "ui/view_session.py",
        "ui/terminal_runtime.py",
        "ui/transition.py",
    ):
        (tmp_path / "media_cli" / name).write_text('def example() -> str:\n    return "ok"\n', encoding="utf-8")
    return tmp_path


def run_quality(project, *args, no_site=False):
    return subprocess.run(
        [sys.executable, *(["-S"] if no_site else []), str(project / "tools/check_quality.py"), *args],
        cwd=project.parent,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_quality_gate_runs_from_other_directory(quality_project):
    result = run_quality(quality_project)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Lint" in result.stdout
    assert "Format" in result.stdout
    assert "Types" in result.stdout


@pytest.mark.parametrize(
    ("source", "diagnostic"),
    [
        ("import os\n", "F401"),
        ('def example() -> str:\n return "ok"\n', "would be reformatted"),
        ("def example() -> str:\n    return 42\n", "return-value"),
    ],
)
def test_quality_gate_rejects_regressions_without_editing(quality_project, source, diagnostic):
    target = quality_project / "media_cli/ui/footer.py"
    target.write_text(source, encoding="utf-8")
    result = run_quality(quality_project)
    assert result.returncode != 0
    assert diagnostic in result.stdout + result.stderr
    assert "Format:" in result.stdout
    assert "Types:" in result.stdout
    assert target.read_text(encoding="utf-8") == source


def test_quality_audit_reports_unmigrated_files(quality_project):
    target = quality_project / "media_cli/legacy.py"
    target.write_text("import os\n", encoding="utf-8")
    assert run_quality(quality_project).returncode == 0
    result = run_quality(quality_project, "--audit")
    assert result.returncode != 0
    assert "legacy.py" in result.stdout + result.stderr


def test_quality_gate_requires_developer_tools(quality_project):
    result = run_quality(quality_project, no_site=True)
    assert result.returncode == 2
    assert "ruff" in result.stderr
    assert ".[dev]" in result.stderr


def test_quality_gate_requires_pytest_when_tests_requested(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("quality_runner", ROOT / "tools/check_quality.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    original = importlib.util.find_spec
    monkeypatch.setattr(runner.importlib.util, "find_spec", lambda name: None if name == "pytest" else original(name))
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: pytest.fail("Checks ran with missing pytest"))
    assert runner.main(("--tests",)) == 2
    assert "pytest" in capsys.readouterr().err


def test_quality_gate_rejects_missing_target(quality_project):
    (quality_project / "media_cli/ui/footer.py").unlink()
    result = run_quality(quality_project)
    assert result.returncode != 0
    assert "footer.py" in result.stdout + result.stderr


@pytest.mark.parametrize("passing", (True, False))
def test_quality_gate_runs_tests_and_propagates_failure(quality_project, passing):
    # Replace copied tooling tests to avoid recursively invoking this test.
    for name in ("test_quality_checking.py", "test_type_checking.py"):
        (quality_project / "tests" / name).write_text(f"def test_example():\n    assert {passing}\n", encoding="utf-8")
    result = run_quality(quality_project, "--tests")
    assert (result.returncode == 0) is passing, result.stdout + result.stderr
    assert "Tests:" in result.stdout
    assert ("2 passed" if passing else "2 failed") in result.stdout
