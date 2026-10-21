"""Exercise the staged gate with real mypy, including deliberately broken code."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def typing_project(tmp_path):
    for name in (
        "pyproject.toml",
        "tools/check_types.py",
        "media_cli/core/network.py",
        "media_cli/core/workers.py",
        "media_cli/ui/fuzzy.py",
    ):
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    for package in ("media_cli", "media_cli/core", "media_cli/ui"):
        (tmp_path / package / "__init__.py").write_text("", encoding="utf-8")
    for name in ("picker_state.py", "view_model.py", "view_session.py", "terminal_runtime.py", "transition.py"):
        (tmp_path / "media_cli/ui" / name).write_text(
            "def boundary(value: str) -> str:\n    return value\n", encoding="utf-8"
        )
    # A legacy body error must stay outside the gate, but its public type
    # signature must still constrain calls made by the migrated module.
    (tmp_path / "media_cli/ui/dependency.py").write_text(
        "def typed(value: str) -> str:\n    return 42\n", encoding="utf-8"
    )
    (tmp_path / "media_cli/ui/footer.py").write_text(
        "from .dependency import typed\ndef render_footer(value: str) -> str:\n    return typed(value)\n",
        encoding="utf-8",
    )
    return tmp_path


def run_gate(project, *args, no_site=False):
    return subprocess.run(
        [sys.executable, *(["-S"] if no_site else []), str(project / "tools/check_types.py"), *args],
        cwd=project.parent,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_gate_checks_targets_from_any_working_directory(typing_project):
    result = run_gate(typing_project)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Success" in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("source", "error_code"),
    [
        ("def render_footer(value):\n    return value\n", "no-untyped-def"),
        (
            "from .dependency import typed\ndef render_footer(value: str) -> str:\n    return typed(42)\n",
            "arg-type",
        ),
        ("def render_footer(value: str) -> str:\n    return None\n", "return-value"),
        ("import anyplay_missing_typing_dependency\n", "import-not-found"),
    ],
)
def test_gate_rejects_contract_regressions(typing_project, source, error_code):
    (typing_project / "media_cli/ui/footer.py").write_text(source, encoding="utf-8")
    result = run_gate(typing_project)
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"[{error_code}]" in result.stdout


def test_audit_reports_legacy_errors_outside_gate(typing_project):
    result = run_gate(typing_project, "--audit")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "dependency.py" in result.stdout
    assert "[return-value]" in result.stdout


def test_audit_keeps_unmigrated_functions_permissive(typing_project):
    (typing_project / "media_cli/ui/dependency.py").write_text(
        "def typed(value: str) -> str:\n    return value\ndef legacy(value):\n    return value\n",
        encoding="utf-8",
    )
    result = run_gate(typing_project, "--audit")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""


@pytest.mark.parametrize("call", ['render_footer("invalid", 80)', 'render_footer(Footer(), "invalid")'])
def test_real_footer_api_rejects_wrong_input(typing_project, call):
    shutil.copyfile(ROOT / "media_cli/core/terminal.py", typing_project / "media_cli/core/terminal.py")
    source = (ROOT / "media_cli/ui/footer.py").read_text(encoding="utf-8")
    (typing_project / "media_cli/ui/footer.py").write_text(
        source + f"\n\ndef typing_probe() -> str:\n    return {call}\n", encoding="utf-8"
    )
    result = run_gate(typing_project)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "[arg-type]" in result.stdout


def test_missing_target_fails(typing_project):
    (typing_project / "media_cli/ui/footer.py").unlink()
    result = run_gate(typing_project)
    assert result.returncode != 0
    assert "footer.py" in result.stdout + result.stderr


def test_missing_checker_fails_with_install_instruction(typing_project):
    result = run_gate(typing_project, no_site=True)
    assert result.returncode == 2
    assert "mypy" in result.stderr
    assert ".[dev]" in result.stderr


@pytest.fixture
def shared_typing_project(tmp_path):
    for name in (
        "pyproject.toml",
        "tools/check_types.py",
        "media_cli/core/network.py",
        "media_cli/core/workers.py",
        "media_cli/core/terminal.py",
        "media_cli/ui/actions.py",
        "media_cli/ui/key_codes.py",
        "media_cli/ui/layout.py",
        "media_cli/ui/loading_key_feedback.py",
        "media_cli/ui/loading_lifecycle.py",
        "media_cli/ui/loading_model.py",
        "media_cli/ui/message_choices.py",
        "media_cli/ui/picker_actions.py",
        "media_cli/ui/query_cursor.py",
        "media_cli/ui/row_layout.py",
        "media_cli/ui/row_text.py",
        "media_cli/ui/styled_text.py",
        "media_cli/ui/theme.py",
        "media_cli/ui/footer.py",
        "media_cli/ui/fuzzy.py",
        "media_cli/ui/view_model.py",
        "media_cli/ui/picker_state.py",
        "media_cli/ui/view_session.py",
        "media_cli/ui/terminal_runtime.py",
        "media_cli/ui/transition.py",
    ):
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    for package in ("media_cli", "media_cli/core", "media_cli/ui"):
        (tmp_path / package / "__init__.py").write_text("", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    ("module", "source", "error_code"),
    (
        ("view_model", 'ViewItem(12, "label", details=Inherit)', "arg-type"),
        ("view_model", 'ViewItem("id", "label", details="inherit")', "arg-type"),
        ("view_model", 'ViewSpec("id", "prompt")', "call-arg"),
        ("picker_state", 'PickerState(scroll="invalid")', "arg-type"),
        ("view_session", 'UiEvent("selct", "root", 1)', "arg-type"),
        ("view_session", 'UpdateStatus("root", 1, "done")', "arg-type"),
        ("view_session", 'UpdateItems("root", 1, ("not an item",))', "arg-type"),
        ("view_session", 'UiEvent("select", "root", 1, value=object())', "arg-type"),
        (
            "view_session",
            'ViewSession(ViewSpec("root", "Pick", details=DetailLines(Blank, Blank, Blank))).apply(object())',
            "arg-type",
        ),
    ),
)
def test_shared_contracts_are_checked_by_real_gate(shared_typing_project, module, source, error_code):
    target = shared_typing_project / "media_cli/ui" / f"{module}.py"
    with target.open("a", encoding="utf-8") as output:
        if "DetailLines" in source:
            output.write("\nfrom .view_model import DetailLines, Blank\n")
        output.write(f"\ndef typing_probe() -> None:\n    {source}\n")
    result = run_gate(shared_typing_project)
    assert result.returncode == 1, result.stdout + result.stderr
    assert f"[{error_code}]" in result.stdout


def test_real_shared_contracts_pass_gate(shared_typing_project):
    result = run_gate(shared_typing_project)
    assert result.returncode == 0, result.stdout + result.stderr
