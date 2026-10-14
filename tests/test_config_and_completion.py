import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from media_cli.cli.args import parse_cli_args
from media_cli.cli.completion import (
    COMMANDS,
    CONFIG_OPTIONS,
    DOWNLOAD_OPTIONS,
    DOWNLOADS_OPTIONS,
    ROOT_OPTIONS,
    SEARCH_OPTIONS,
    generate_completion,
    completion_install_path,
)
from media_cli.state.config import load_config_file, open_config_editor, resolve_editor, serialize_conf
from media_cli.app.orchestrator import handle_config_command


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

    def test_parse_config_get(self):
        args, _ = parse_cli_args(["config", "get", "quality"])
        self.assertEqual(args.command, "config")
        self.assertEqual(args.config_action, "get")
        self.assertEqual(args.config_key, "quality")
        self.assertIsNone(args.config_value)

    def test_handle_config_get_success(self):
        args, _ = parse_cli_args(["config", "get", "quality"])
        with patch("media_cli.app.orchestrator.load_config", return_value={"quality": "1080p"}):
            with patch("builtins.print") as mock_print:
                handle_config_command(args)
                mock_print.assert_called_once_with("1080p")

    def test_handle_config_get_boolean(self):
        args, _ = parse_cli_args(["config", "get", "binge"])
        with patch("media_cli.app.orchestrator.load_config", return_value={"binge": False}):
            with patch("builtins.print") as mock_print:
                handle_config_command(args)
                mock_print.assert_called_once_with("false")

    def test_handle_config_get_not_found(self):
        args, _ = parse_cli_args(["config", "get", "unknown_key"])
        with patch("media_cli.app.orchestrator.load_config", return_value={"quality": "1080p"}):
            with self.assertRaises(SystemExit) as cm:
                handle_config_command(args)
            self.assertEqual(cm.exception.code, 1)

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
        with patch("media_cli.state.config.resolve_editor", return_value="nano"):
            code = open_config_editor()
            self.assertEqual(code, 0)
            mock_run.assert_called_once()
            call_cmd = mock_run.call_args[0][0]
            self.assertEqual(call_cmd[0], "nano")
            self.assertTrue(call_cmd[1].endswith("config.conf"))

    def test_missing_config_keys_are_added_without_overwriting_existing_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.conf")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("quality = 720p\n")
                handle.write("torrent_min_seeders = 7\n")
                handle.write("disabled_providers = animegg\n")

            cfg = load_config_file(path)

            self.assertEqual(cfg["quality"], "720p")
            self.assertEqual(cfg["torrent_min_seeders"], 7)
            self.assertEqual(cfg["disabled_providers"], "animegg")
            self.assertIn("torrent_fallback", cfg)

            with open(path, encoding="utf-8") as handle:
                saved = handle.read()
            self.assertIn("torrent_fallback = true", saved)
            self.assertIn("disabled_providers = animegg", saved)

    def test_serialized_config_shows_torrent_and_provider_settings(self):
        text = serialize_conf({})

        self.assertIn("# Providers", text)
        self.assertIn("disabled_providers =", text)
        self.assertIn("# Torrent Search & Fallback", text)
        self.assertIn("torrent_min_seeders = 2", text)
        self.assertIn("auto_download_next = adaptive", text)
        self.assertNotIn("subtitle_mode", text)

        legacy_false = serialize_conf({"auto_download_next": False})
        legacy_true = serialize_conf({"auto_download_next": True})
        self.assertIn("auto_download_next = off", legacy_false)
        self.assertIn("auto_download_next = adaptive", legacy_true)

    def test_completion_no_longer_exposes_sidecar_subtitle_option(self):
        self.assertNotIn("--sidecar-subs", SEARCH_OPTIONS)


class TestShellCompletion(unittest.TestCase):
    def test_commands_include_config_and_providers(self):
        self.assertIn("config", COMMANDS)
        self.assertIn("providers", COMMANDS)
        self.assertIn("search", COMMANDS)
        self.assertNotIn("download", COMMANDS)
        self.assertIn("downloads", COMMANDS)
        self.assertIn("--download", SEARCH_OPTIONS)

    def test_options_include_download_dir(self):
        self.assertIn("-d", DOWNLOAD_OPTIONS)
        self.assertIn("--download", DOWNLOAD_OPTIONS)
        self.assertIn("--download-dir", DOWNLOAD_OPTIONS)
        self.assertIn("--download-dir", DOWNLOADS_OPTIONS)
        self.assertIn("-d", SEARCH_OPTIONS)
        self.assertIn("--download", SEARCH_OPTIONS)
        self.assertIn("--download-dir", SEARCH_OPTIONS)

    def test_bash_completion_content(self):
        script = generate_completion("bash")
        self.assertIn("config", script)
        self.assertIn("providers", script)
        self.assertIn("--download-dir|--dir", script)
        self.assertIn("--download", script)
        self.assertIn("-d", script)
        self.assertIn("-e|--editor", script)
        self.assertIn("download_dir", script)
        self.assertIn("translation_type", script)
        self.assertIn("disabled_providers", script)
        self.assertIn("torrent_fallback", script)

    def test_zsh_completion_content(self):
        script = generate_completion("zsh")
        self.assertIn("config:Manage or open configuration", script)
        self.assertIn("providers:List available providers", script)

    def test_fish_completion_content(self):
        script = generate_completion("fish")
        self.assertIn("config", script)
        self.assertIn("providers", script)

    def test_completion_install_path_termux(self):
        from media_cli.constants import APP_NAME
        with tempfile.TemporaryDirectory() as tmp_prefix:
            bash_dir = os.path.join(tmp_prefix, "share/bash-completion/completions")
            zsh_dir = os.path.join(tmp_prefix, "share/zsh/site-functions")
            os.makedirs(bash_dir)
            os.makedirs(zsh_dir)
            with patch.dict(os.environ, {"PREFIX": tmp_prefix}):
                path_bash = completion_install_path("bash")
                self.assertEqual(str(path_bash), os.path.join(bash_dir, APP_NAME))
                path_zsh = completion_install_path("zsh")
                self.assertEqual(str(path_zsh), os.path.join(zsh_dir, f"_{APP_NAME}"))


class TestProvidersDisplay(unittest.TestCase):
    def test_providers_display_wide(self):
        from io import StringIO
        from media_cli.app import orchestrator
        from media_cli.cli.args import parse_cli_args

        mock_args, mock_pa = parse_cli_args(["providers"])
        out = StringIO()
        with patch("sys.stdout", out), \
             patch("shutil.get_terminal_size", return_value=os.terminal_size((100, 24))), \
             patch("media_cli.app.orchestrator.parse_cli_args", return_value=(mock_args, mock_pa)):
            orchestrator.main()

        output = out.getvalue()
        self.assertIn("Available Streaming Providers:", output)
        self.assertIn("Name", output)
        self.assertIn("Engine", output)
        self.assertIn("Type", output)
        self.assertIn("Languages", output)
        self.assertIn("AniDB", output)
        self.assertIn("AnimeThemes", output)
        self.assertIn("songs", output)

    def test_source_registry_entry_is_listed_but_not_episode_provider(self):
        from io import StringIO
        from media_cli.app import orchestrator
        from media_cli.cli.args import parse_cli_args
        from media_cli.providers import available_providers

        self.assertNotIn("animethemes", available_providers())

        mock_args, mock_pa = parse_cli_args(["providers"])
        out = StringIO()
        with patch("sys.stdout", out), \
             patch("shutil.get_terminal_size", return_value=os.terminal_size((100, 24))), \
             patch("media_cli.app.orchestrator.parse_cli_args", return_value=(mock_args, mock_pa)):
            orchestrator.main()

        output = out.getvalue()
        self.assertIn("animethemes", output)
        self.assertIn("AnimeThemes", output)

    def test_inactive_provider_hidden_from_cli_choices(self):
        from media_cli.providers import available_providers, is_provider_active

        self.assertTrue(is_provider_active("anidb"))
        self.assertIn("anidb", available_providers())

    def test_user_disabled_provider_is_inactive(self):
        from media_cli import providers

        with patch(
            "media_cli.providers.disabled_providers_from_config",
            return_value={"anidb"},
        ):
            self.assertFalse(providers.is_provider_active("anidb"))
            self.assertNotIn("anidb", providers.available_providers())

    def test_providers_display_narrow(self):
        from io import StringIO
        from media_cli.app import orchestrator
        from media_cli.cli.args import parse_cli_args

        mock_args, mock_pa = parse_cli_args(["providers"])
        out = StringIO()
        with patch("sys.stdout", out), \
             patch("shutil.get_terminal_size", return_value=os.terminal_size((55, 24))), \
             patch("media_cli.app.orchestrator.parse_cli_args", return_value=(mock_args, mock_pa)):
            orchestrator.main()

        output = out.getvalue()
        self.assertIn("Available Streaming Providers:", output)
        # Narrow layout has dots and bracketed types
        self.assertIn("● active", output)
        self.assertIn("[anime]", output)
        self.assertIn("•", output)
        self.assertIn("▸ = default provider", output)
