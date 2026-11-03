"""Startup reuses resolved configuration rather than re-reading secrets/config."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from media_cli import app_core
from media_cli.app import orchestrator, terminal_controller
from media_cli.cli.args import parse_cli_args
from media_cli.context import CliFlags
from media_cli.state.config import DEFAULT_CONFIG


@pytest.mark.parametrize("command", [["downloads"], ["history"], ["search", "example"]])
def test_startup_loads_configuration_once_and_keeps_resolved_directory(monkeypatch, tmp_path, command):
    args, parser = parse_cli_args(command)
    config = deepcopy(DEFAULT_CONFIG)
    config.update(anilist_token="", download_dir=str(tmp_path / "configured"), provider="anipm")
    reads = Mock(side_effect=lambda: deepcopy(config))
    resolved = str(tmp_path / "resolved")
    captured = []
    monkeypatch.setattr(orchestrator, "parse_cli_args", lambda: (args, parser))
    monkeypatch.setattr(orchestrator, "runtime_flags", CliFlags())
    monkeypatch.setattr(orchestrator, "load_config", reads)
    monkeypatch.setattr(orchestrator, "load_downloads_db", lambda: {"shows": {}})
    monkeypatch.setattr(orchestrator, "setup_download_directory", lambda *a: resolved)
    monkeypatch.setattr(orchestrator, "configure_file_logging", lambda **kw: None)
    monkeypatch.setattr(orchestrator, "set_incognito_history_allowed", lambda *a: None)
    monkeypatch.setattr(orchestrator, "get_provider", Mock(side_effect=AssertionError("Unused default provider initialized")))
    monkeypatch.setattr(orchestrator, "_resolve_quit_with_active_downloads", lambda: "QUIT")
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr(app_core, "_ipc_player", SimpleNamespace(quit=lambda: None))

    def screen(handler, flags, ui, ms, cfg, *extra, **kw):
        captured.append(deepcopy(cfg))
        return "QUIT"

    monkeypatch.setattr(terminal_controller, "run_screen_controller", screen)
    orchestrator.main()
    reads.assert_called_once()
    assert captured[0]["download_dir"] == resolved
    assert args.download_dir == resolved
    assert args.provider == "anipm"


@pytest.mark.parametrize("command", [["history"], ["downloads"], ["search", "example"]])
def test_unspecified_provider_remains_unset_until_configuration_is_loaded(command):
    args, _ = parse_cli_args(command)
    assert args.provider is None


@pytest.mark.parametrize("command,provider", [(["search", "anikoto:example"], "anikoto"),
                                            (["search", "example", "--provider", "anidb"], "anidb")])
def test_explicit_provider_still_takes_precedence(command, provider):
    args, _ = parse_cli_args(command)
    assert args.provider == provider


@pytest.mark.parametrize("explicit,expected", [(None, "anikoto"), ("anidb", "anidb")])
def test_search_input_uses_configured_provider_without_legacy_default(monkeypatch, explicit, expected):
    from media_cli.app import search
    from media_cli.context import MachineState, UiState

    shown = []
    monkeypatch.setattr(app_core, "_clear_poster_downloads", lambda: None)
    monkeypatch.setattr(app_core, "provider_display_name", lambda provider: provider)
    monkeypatch.setattr(search, "prompt_search_query", lambda *a, **kw: shown.append(kw["provider_name"]) or -2)
    result = search.handle_search_state(
        CliFlags(), UiState(), MachineState(), {"provider": "anikoto"},
        SimpleNamespace(provider=explicit), "sub", None,
    )
    assert result == "QUIT" and shown == [expected]


def test_preparsed_media_arguments_reach_orchestrator_without_reparsing(monkeypatch):
    parsed = parse_cli_args(["config", "get", "provider"])
    handler = Mock()
    monkeypatch.setattr(orchestrator, "parse_cli_args", Mock(side_effect=AssertionError("Already parsed")))
    monkeypatch.setattr(orchestrator, "configure_file_logging", lambda **kwargs: None)
    monkeypatch.setattr(orchestrator, "handle_config_command", handler)
    app_core.app_main(parsed)
    handler.assert_called_once_with(parsed[0])
