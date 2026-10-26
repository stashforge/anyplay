import getpass
import importlib
import importlib.util
import os
import pty
import select
import subprocess
import sys
import time
import warnings
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from media_cli.cli.args import parse_cli_args
from media_cli.cli.completion import generate_completion
from media_cli.services.android_webview import client as transport


def command_module():
    name = "media_cli.cli.companion"
    assert importlib.util.find_spec(name) is not None, "Companion needs a CLI command owner"
    return importlib.import_module(name)


@pytest.fixture
def command(monkeypatch):
    module = command_module()
    client = SimpleNamespace(
        has_pairing=Mock(return_value=False),
        pair=Mock(return_value={"paired": True, "token": "must-not-print"}),
        health=Mock(return_value={"worker": "ready", "site_get": True, "token": "must-not-print"}),
    )
    factory = Mock(return_value=client)
    opening = Mock(return_value=True)
    prompt = Mock(return_value="123456789012")
    monkeypatch.setattr(module, "is_termux", lambda: True)
    monkeypatch.setattr(module.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(transport, "ProbeClient", factory)
    monkeypatch.setattr(transport, "open_companion", opening)
    monkeypatch.setattr(getpass, "getpass", prompt)
    return module, client, factory, opening, prompt


@pytest.mark.parametrize("action", ["pair", "status"])
def test_parser_accepts_companion_actions_and_global_flags(action):
    args, _ = parse_cli_args(["companion", action, "--debug", "--plain"])
    assert args.command == "companion" and args.companion_action == action
    assert args.debug and args.plain


@pytest.mark.parametrize("argv", [["companion"], ["companion", "reset"], ["companion", "pair", "123456789012"]])
def test_pairing_code_and_unapproved_actions_are_not_command_arguments(argv):
    with pytest.raises(SystemExit) as error:
        parse_cli_args(argv)
    assert error.value.code == 2


def test_help_exposes_companion_and_pairing_flow(capsys):
    with pytest.raises(SystemExit) as error:
        parse_cli_args(["companion", "--help"])
    assert error.value.code == 0
    output = capsys.readouterr().out
    assert "pair" in output and "status" in output and "Termux" in output


@pytest.mark.parametrize("shell", ["bash", "zsh", "fish"])
def test_all_shell_completions_include_companion_actions(shell):
    script = generate_completion(shell)
    assert "companion" in script and "pair" in script and "status" in script


def test_pair_delegates_hidden_code_to_shared_client_without_printing_secrets(command, capsys):
    module, client, _, opening, prompt = command
    assert module.handle_companion_command("pair") == 0
    opening.assert_called_once_with()
    prompt.assert_called_once()
    client.pair.assert_called_once_with("123456789012")
    client.health.assert_not_called()
    output = capsys.readouterr()
    assert "paired" in output.out.lower()
    assert "123456789012" not in output.out + output.err
    assert "must-not-print" not in output.out + output.err


def test_existing_pairing_is_not_replaced_or_reopened(command, capsys):
    module, client, _, opening, prompt = command
    client.has_pairing.return_value = True
    assert module.handle_companion_command("pair") == 0
    assert "already paired" in capsys.readouterr().out.lower()
    opening.assert_not_called()
    prompt.assert_not_called()
    client.pair.assert_not_called()
    client.health.assert_not_called()


def test_pair_refuses_redirected_input_before_opening_android(command, monkeypatch):
    module, client, _, opening, prompt = command
    monkeypatch.setattr(module.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    assert module.handle_companion_command("pair") == 1
    opening.assert_not_called()
    prompt.assert_not_called()
    client.pair.assert_not_called()


def test_pair_never_falls_back_to_echoed_input(command):
    module, client, _, _, prompt = command

    def echo_warning(*args, **kwargs):
        warnings.warn("Cannot disable echo", getpass.GetPassWarning, stacklevel=2)
        pytest.fail("A visible input fallback must never run")

    prompt.side_effect = echo_warning
    assert module.handle_companion_command("pair") == 1
    client.pair.assert_not_called()


@pytest.mark.parametrize("error", [KeyboardInterrupt(), EOFError()])
def test_cancelled_pairing_does_not_send_or_save_a_code(command, capsys, error):
    module, client, _, _, prompt = command
    prompt.side_effect = error
    assert module.handle_companion_command("pair") == 130
    assert "cancelled" in capsys.readouterr().err.lower()
    client.pair.assert_not_called()


def test_status_without_pairing_is_read_only_and_actionable(command, capsys):
    module, client, _, opening, prompt = command
    assert module.handle_companion_command("status") == 1
    assert "companion pair" in capsys.readouterr().out
    opening.assert_not_called()
    prompt.assert_not_called()
    client.health.assert_not_called()
    client.pair.assert_not_called()


def test_status_reports_saved_pairing_with_normally_stopped_service(command, capsys):
    module, client, _, opening, prompt = command
    client.has_pairing.return_value = True
    client.health.side_effect = transport.CompanionUnavailable("Local companion is stopped")
    assert module.handle_companion_command("status") == 0
    output = capsys.readouterr().out.lower()
    assert "saved" in output and "stopped" in output
    opening.assert_not_called()
    prompt.assert_not_called()
    client.pair.assert_not_called()


def test_status_only_prints_safe_authenticated_summary(command, capsys):
    module, client, _, opening, prompt = command
    client.has_pairing.return_value = True
    assert module.handle_companion_command("status") == 0
    client.health.assert_called_once_with(timeout=2)
    output = capsys.readouterr()
    assert "verified" in output.out.lower() and "running" in output.out.lower()
    assert "must-not-print" not in output.out + output.err
    opening.assert_not_called()
    prompt.assert_not_called()
    client.pair.assert_not_called()


@pytest.mark.parametrize("stage", ["has_pairing", "health"])
def test_status_storage_and_authentication_errors_never_reset_pairing(command, capsys, stage):
    module, client, _, opening, prompt = command
    client.has_pairing.return_value = True
    getattr(client, stage).side_effect = transport.ProbeError("Authentication or storage rejected")
    assert module.handle_companion_command("status") == 1
    assert "not changed" in capsys.readouterr().err.lower()
    opening.assert_not_called()
    prompt.assert_not_called()
    client.pair.assert_not_called()


@pytest.mark.parametrize("action", ["pair", "status"])
def test_non_termux_does_not_initialize_android_client(command, monkeypatch, capsys, action):
    module, _, factory, opening, prompt = command
    monkeypatch.setattr(module, "is_termux", lambda: False)
    assert module.handle_companion_command(action) == 1
    assert "Termux" in capsys.readouterr().err
    factory.assert_not_called()
    opening.assert_not_called()
    prompt.assert_not_called()


def test_missing_saved_pairing_check_does_not_create_storage(tmp_path):
    path = tmp_path / "missing" / "token"
    client = transport.ProbeClient(token_path=path)
    assert hasattr(client, "has_pairing"), "Expose safe pairing inspection without returning the token"
    assert client.has_pairing() is False
    assert not path.parent.exists()


def test_saved_pairing_inspection_validates_permissions_without_exposing_token(tmp_path):
    path = tmp_path / "private" / "token"
    client = transport.ProbeClient(token_path=path)
    assert hasattr(client, "has_pairing")
    client._store_token("a" * 64)
    assert client.has_pairing() is True
    path.chmod(0o644)
    with pytest.raises(transport.ProbeError):
        client.has_pairing()
    assert path.read_text().strip() == "a" * 64
    assert path.stat().st_mode & 0o777 == 0o644


def test_health_has_an_overall_deadline(monkeypatch, tmp_path):
    client = transport.ProbeClient(token_path=tmp_path / "token")
    request = Mock(return_value={})
    monkeypatch.setattr(client, "_request", request)
    before = time.monotonic()
    client.health(timeout=2)
    assert before < request.call_args.kwargs.get("deadline", 0) <= time.monotonic() + 2


@pytest.mark.parametrize("result", [0, 1, 130])
def test_orchestrator_dispatches_before_media_or_download_setup(monkeypatch, result):
    from media_cli.app import orchestrator

    module = command_module()
    dispatch = Mock(return_value=result)
    monkeypatch.setattr(module, "handle_companion_command", dispatch)
    args = SimpleNamespace(command="companion", companion_action="status", debug=False)
    monkeypatch.setattr(orchestrator, "parse_cli_args", lambda: (args, None))
    monkeypatch.setattr(orchestrator.signal, "signal", lambda *args: None)
    monkeypatch.setattr(orchestrator, "configure_file_logging", lambda **kwargs: None)
    for name in ("load_config", "load_downloads_db", "setup_download_directory"):
        monkeypatch.setattr(orchestrator, name, Mock(side_effect=AssertionError("Media setup must not run")))
    if result:
        with pytest.raises(SystemExit) as error:
            orchestrator.main()
        assert error.value.code == result
    else:
        assert orchestrator.main() is None
    dispatch.assert_called_once_with("status")


def test_hidden_pairing_input_is_not_echoed_on_a_real_terminal():
    script = """
from media_cli.cli import companion
from media_cli.services.android_webview import client
class FakeClient:
    def has_pairing(self): return False
    def pair(self, code):
        assert code == '123456789012'
        return {'paired': True}
client.ProbeClient = FakeClient
client.open_companion = lambda: True
companion.is_termux = lambda: True
raise SystemExit(companion.handle_companion_command('pair'))
"""
    master, slave = pty.openpty()
    process = subprocess.Popen([sys.executable, "-c", script], stdin=slave, stdout=slave, stderr=slave)
    output = bytearray()
    try:
        deadline = time.monotonic() + 8
        while b"Pairing code (hidden):" not in output:
            assert time.monotonic() < deadline, "Hidden input prompt did not appear"
            if select.select([master], [], [], 0.1)[0]:
                output.extend(os.read(master, 4096))
        os.write(master, b"123456789012\n")
        assert process.wait(timeout=5) == 0
        while select.select([master], [], [], 0.05)[0]:
            output.extend(os.read(master, 4096))
        assert b"paired successfully" in output
        assert b"123456789012" not in output
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(slave)
        os.close(master)


@pytest.mark.parametrize("words,index,expected", [
    ("anyplay companion p", 2, "pair"),
    ("anyplay companion s", 2, "status"),
])
def test_bash_completion_actually_suggests_actions(words, index, expected):
    script = generate_completion("bash") + f"""
COMP_WORDS=({words})
COMP_CWORD={index}
_anyplay_completion
printf '%s\\n' "${{COMPREPLY[@]}}"
"""
    result = subprocess.run(["bash", "--noprofile", "--norc"], input=script,
                            text=True, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout.splitlines()
