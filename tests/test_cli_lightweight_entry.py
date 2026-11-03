"""Fresh processes prove utility commands do not depend on media/providers."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARD = """
import importlib.abc, sys
blocked = []
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        roots = ('curl_cffi', 'bs4', 'm3u8', 'requests', 'cryptography', 'Crypto',
                 'playwright', 'libtorrent', 'media_cli.providers',
                 'media_cli.app_core', 'media_cli.app', 'media_cli.playback',
                 'media_cli.extractors', 'media_cli.services.http',
                 'media_cli.ui.player_screen', 'media_cli.ui.terminal_runtime')
        if any(fullname == root or fullname.startswith(root + '.') for root in roots):
            blocked.append(fullname)
            raise AssertionError('Unrelated import: ' + fullname)
sys.meta_path.insert(0, Guard())
"""


def run_fresh(code, home):
    return subprocess.run(
        [sys.executable, "-c", GUARD + code], cwd=ROOT,
        env={**os.environ, "HOME": str(home), "NO_COLOR": "1"},
        capture_output=True, text=True, timeout=15,
    )


@pytest.mark.parametrize(("arguments", "exit_code", "text"), [
    (["--help"], 0, "Main commands"),
    (["--version"], 0, "anyplay 0.9.2"),
    (["search", "--help"], 0, "Search query"),
    (["search", "senshi:", "--help"], 0, "Senshi"),
    (["companion", "--help"], 0, "pair"),
    (["health", "--help"], 0, "reports"),
    (["health", "reports"], 0, "No saved health reports yet."),
    (["health", "report", "latest"], 1, "No saved health reports yet."),
    (["companion", "status"], 1, "Termux only"),
    (["completion", "bash"], 0, "_anyplay_completion"),
    (["completion", "zsh"], 0, "#compdef"),
    (["completion", "fish"], 0, "complete -c anyplay"),
    (["completion", "install", "fish"], 0, "Installed fish completion"),
    (["companion", "unknown"], 2, "Unknown companion_action"),
    (["search", "zzzzzz:example"], 2, "Unknown provider"),
    ([], 0, "Main commands"),
])
def test_utility_commands_work_without_optional_or_media_imports(tmp_path, arguments, exit_code, text):
    result = run_fresh(f"""
sys.argv = ['anyplay', *{arguments!r}]
from media_cli.cli.main import run
try:
    result = run()
except SystemExit as error:
    result = error.code
assert not blocked, blocked
raise SystemExit(result)
""", tmp_path)
    assert result.returncode == exit_code, result.stderr + result.stdout
    assert text in result.stdout + result.stderr
    assert "\x1b[?1049" not in result.stdout + result.stderr
    assert not (tmp_path / ".local/state/anyplay").exists()


def test_importing_entry_point_has_no_media_side_effects(tmp_path):
    result = run_fresh("import media_cli.cli.main\nassert not blocked, blocked\n", tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
    assert list(tmp_path.iterdir()) == []


def test_parser_uses_bundled_version_without_loading_package_metadata(tmp_path):
    result = run_fresh("""
from media_cli.cli.args_common import __version__
assert __version__
assert 'importlib.metadata' not in sys.modules
""", tmp_path)
    assert result.returncode == 0, result.stderr


def test_media_dispatch_passes_parsed_arguments_without_loading_application_twice(tmp_path):
    result = run_fresh("""
from types import ModuleType
application = ModuleType('media_cli.cli.application')
seen = []
def dispatch(parsed):
    args, parser = parsed
    assert args.command == 'search' and args.provider == 'senshi'
    assert args.query == ['bocchi'] and args.debug
    seen.append(args)
    return 7
application.run = dispatch
sys.modules[application.__name__] = application
sys.argv = ['anyplay', 'search', 'senshi:bocchi', '--debug']
from media_cli.cli.main import run
assert run() == 7
assert len(seen) == 1
assert not blocked, blocked
""", tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.parametrize("exit_code", [0, 1, 130])
def test_companion_entry_preserves_handler_exit_code(tmp_path, exit_code):
    result = run_fresh(f"""
from types import ModuleType
companion = ModuleType('media_cli.cli.companion')
companion.handle_companion_command = lambda action: {exit_code} if action == 'status' else 99
sys.modules[companion.__name__] = companion
sys.argv = ['anyplay', 'companion', 'status', '--debug']
from media_cli.cli.main import run
assert run() == {exit_code}
assert not blocked, blocked
""", tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout


def test_completion_install_error_is_visible_without_media_error_handling(tmp_path):
    result = run_fresh("""
from media_cli.cli import completion
def unavailable(args):
    raise OSError('destination is read-only')
completion.handle_completion_command = unavailable
sys.argv = ['anyplay', 'completion', 'install', 'fish']
from media_cli.cli.main import run
assert run() == 1
assert not blocked, blocked
""", tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Could not write shell completion: destination is read-only" in result.stderr


@pytest.mark.parametrize("legacy", [False, True])
def test_metadata_respects_disabled_aliases_without_migrating_config(tmp_path, legacy):
    directory = tmp_path / ".config" / ("allmanga" if legacy else "anyplay")
    directory.mkdir(parents=True)
    path = directory / "config.json"
    content = json.dumps({"disabled_providers": "allanime;senshi"})
    path.write_text(content)
    result = run_fresh("""
from media_cli.cli.args import parse_cli_args
from media_cli.cli.args_common import _provider_command_names
names = _provider_command_names()
assert 'mkissa' not in names and 'senshi' not in names
assert 'anipm' in names
from media_cli.cli.completion import SEARCH_PROVIDERS
assert SEARCH_PROVIDERS.count('animethemes') == 1
assert 'senshi' not in SEARCH_PROVIDERS
assert not blocked, blocked
""", tmp_path)
    assert result.returncode == 0, result.stderr + result.stdout
    assert path.read_text() == content
    assert not (directory / "config.conf").exists()
    if legacy:
        assert not (tmp_path / ".config/anyplay").exists()
