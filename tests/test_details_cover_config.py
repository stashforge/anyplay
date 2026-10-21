import pytest

from media_cli.cli.completion import CONFIG_KEYS, bash_completion
from media_cli.state.config import DEFAULT_CONFIG, load_config_file, parse_conf_text, serialize_conf


def test_details_cover_defaults_on_without_changing_global_cover_default():
    assert DEFAULT_CONFIG["details_cover"] is True
    assert DEFAULT_CONFIG["cover"] is False
    text = serialize_conf({})
    assert "details_cover = true" in text
    assert text.count("details_cover =") == 1


@pytest.mark.parametrize("value", [True, False])
def test_details_cover_round_trips_and_old_configs_get_default(tmp_path, value):
    path = tmp_path / "config.conf"
    path.write_text(serialize_conf({"cover": True, "details_cover": value}))
    cfg = load_config_file(str(path))
    assert cfg["cover"] is True
    assert cfg["details_cover"] is value
    assert parse_conf_text(serialize_conf(cfg))["details_cover"] is value
    path.write_text("cover = true\n")
    assert load_config_file(str(path))["details_cover"] is True


def test_details_cover_has_key_and_boolean_value_completion():
    assert "details_cover" in CONFIG_KEYS
    assert "cover|details_cover|aniskip" in bash_completion()


@pytest.mark.parametrize("value,expected", [("false", False), ("true", True)])
def test_config_command_saves_details_cover_as_boolean(monkeypatch, value, expected):
    from media_cli.app import orchestrator
    from media_cli.cli.args import parse_cli_args

    saved = []
    monkeypatch.setattr(orchestrator, "load_config", lambda: {"cover": True})
    monkeypatch.setattr(orchestrator, "save_config", lambda cfg: saved.append(dict(cfg)))
    args, _ = parse_cli_args(["config", "set", "details_cover", value])
    orchestrator.handle_config_command(args)
    assert saved == [{"cover": True, "details_cover": expected}]


@pytest.mark.parametrize("global_cover,details_cover", [(True, True), (True, False), (False, True), (False, False)])
def test_live_menu_honors_global_master_and_details_preference(monkeypatch, global_cover, details_cover):
    from types import SimpleNamespace

    from media_cli.app import playback_menu
    from media_cli.app.details_menu_controller import DetailsMenuResult
    from media_cli.context import CliFlags, MachineState, UiState

    show = dict(
        _id="one",
        _provider="example",
        name="Example",
        thumbnail="https://images.test/one.png",
        _episode_ids=["1"],
        _local_progress=0,
        availableEpisodes={"sub": 1},
    )
    core = playback_menu.app_core
    for name in ("prepare_show_display_state", "get_show_anilist_id"):
        monkeypatch.setattr(core, name, lambda *a, **kw: None)
    monkeypatch.setattr(core, "ensure_episode_ids", lambda *a: ("1",))
    monkeypatch.setattr(core, "get_resume_time", lambda *a: 0)
    monkeypatch.setattr(core, "get_local_progress", lambda *a: 0)
    monkeypatch.setattr(core, "is_confirmed_offline", lambda: False)
    monkeypatch.setattr("media_cli.domain.titles.get_cached_title_language", lambda: "romaji")
    captured = []

    def menu(flags, ui, *, read_view, **kwargs):
        captured.append(read_view())
        return DetailsMenuResult("back")

    monkeypatch.setattr(playback_menu, "run_details_menu", menu)
    playback_menu.handle_action_menu_state(
        CliFlags(show_image=global_cover),
        UiState(ui_show_ctx=show),
        MachineState(current_ep="1"),
        {"details_cover": details_cover},
        SimpleNamespace(),
        "sub",
        lambda *a: False,
    )
    assert (captured[0].image is not None) is (global_cover and details_cover)
