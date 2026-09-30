import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from allmanga_cli.cli.args import parse_cli_args
from allmanga_cli.cli.completion import (
    COMMANDS,
    CONFIG_OPTIONS,
    DOWNLOAD_OPTIONS,
    DOWNLOADS_OPTIONS,
    ROOT_OPTIONS,
    SEARCH_OPTIONS,
    generate_completion,
    completion_install_path,
)
from allmanga_cli.state.config import resolve_editor, open_config_editor
from allmanga_cli.app.orchestrator import handle_config_command


class TestConfigCommand(unittest.TestCase):
    def test_parse_config_no_action(self):
        args, _ = parse_cli_args(["config"])
        self.assertEqual(args.command, "config")
        self.assertIsNone(args.config_action)
        self.assertIsNone(args.config_editor)

    def test_parse_config_with_editor_flag(self):
        args, _ = parse_cli_args(["config", "-e", "micro"])
        self.assertEqual(args.command, "config")
        self.assertIsNone(args.config_action)
        self.assertEqual(args.config_editor, "micro")

    def test_parse_config_with_long_editor_flag(self):
        args, _ = parse_cli_args(["config", "--editor", "code -w"])
        self.assertEqual(args.command, "config")
        self.assertIsNone(args.config_action)
        self.assertEqual(args.config_editor, "code -w")

    def test_parse_config_set(self):
        args, _ = parse_cli_args(["config", "set", "quality", "720p"])
        self.assertEqual(args.command, "config")
        self.assertEqual(args.config_action, "set")
        self.assertEqual(args.config_key, "quality")
        self.assertEqual(args.config_value, "720p")

    def test_resolve_editor_override(self):
        self.assertEqual(resolve_editor("micro"), "micro")

    def test_resolve_editor_env_visual(self):
        with patch.dict(os.environ, {"VISUAL": "subl", "EDITOR": "nano"}):
            self.assertEqual(resolve_editor(None), "subl")

    def test_resolve_editor_env_editor(self):
        with patch.dict(os.environ, {"VISUAL": "", "EDITOR": "vim"}):
            self.assertEqual(resolve_editor(None), "vim")

    @patch("shutil.which")
    def test_resolve_editor_fallback(self, mock_which):
        mock_which.side_effect = lambda cmd: cmd == "nano"
        with patch.dict(os.environ, {"VISUAL": "", "EDITOR": ""}):
            self.assertEqual(resolve_editor(None), "nano")

    @patch("subprocess.run")
    def test_open_config_editor_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        with patch("allmanga_cli.state.config.resolve_editor", return_value="nano"):
            code = open_config_editor()
            self.assertEqual(code, 0)
            mock_run.assert_called_once()
            call_cmd = mock_run.call_args[0][0]
            self.assertEqual(call_cmd[0], "nano")
            self.assertTrue(call_cmd[1].endswith("config.conf"))


class TestShellCompletion(unittest.TestCase):
    def test_commands_include_config_and_providers(self):
        self.assertIn("config", COMMANDS)
        self.assertIn("providers", COMMANDS)
        self.assertIn("search", COMMANDS)
        self.assertIn("download", COMMANDS)
        self.assertIn("downloads", COMMANDS)

    def test_options_include_download_dir(self):
        self.assertIn("-d", DOWNLOAD_OPTIONS)
        self.assertIn("--download-dir", DOWNLOAD_OPTIONS)
        self.assertIn("-d", DOWNLOADS_OPTIONS)
        self.assertIn("--download-dir", DOWNLOADS_OPTIONS)
        self.assertIn("-d", SEARCH_OPTIONS)
        self.assertIn("--download-dir", SEARCH_OPTIONS)

    def test_bash_completion_content(self):
        script = generate_completion("bash")
        self.assertIn("config", script)
        self.assertIn("providers", script)
        self.assertIn("-d|--download-dir|--dir", script)
        self.assertIn("-e|--editor", script)
        self.assertIn("download_dir", script)
        self.assertIn("translation_type", script)

    def test_zsh_completion_content(self):
        script = generate_completion("zsh")
        self.assertIn("config:Manage or open configuration", script)
        self.assertIn("providers:List available providers", script)

    def test_fish_completion_content(self):
        script = generate_completion("fish")
        self.assertIn("config", script)
        self.assertIn("providers", script)

    def test_completion_install_path_termux(self):
        with tempfile.TemporaryDirectory() as tmp_prefix:
            bash_dir = os.path.join(tmp_prefix, "share/bash-completion/completions")
            zsh_dir = os.path.join(tmp_prefix, "share/zsh/site-functions")
            os.makedirs(bash_dir)
            os.makedirs(zsh_dir)
            with patch.dict(os.environ, {"PREFIX": tmp_prefix}):
                path_bash = completion_install_path("bash")
                self.assertEqual(str(path_bash), os.path.join(bash_dir, "allmanga-cli"))
                path_zsh = completion_install_path("zsh")
                self.assertEqual(str(path_zsh), os.path.join(zsh_dir, "_allmanga-cli"))
