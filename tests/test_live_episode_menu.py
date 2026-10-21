"""Live episode selection routes shared UI identities into application actions."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from media_cli import app_core
from media_cli.app import playback_episodes
from media_cli.app.details_menu_controller import DetailsMenuResult
from media_cli.context import CliFlags, MachineState, UiState


@pytest.fixture
def episode_menu(monkeypatch):
    ids = ["first", "second", "third"]
    show = dict(_id="show", name="Example", _provider="example", _episode_ids=ids,
                _episode_labels={"first": "1", "second": "2", "third": "3"},
                watched_episodes=["1"], _local_progress=1)
    ui = UiState(ui_show_ctx=show, ep_prev_state="DETAILS")
    ms = MachineState(show_id="show", show_title="Example", current_ep="second", current_ep_index=1)
    args = SimpleNamespace(download=False)
    monkeypatch.setattr(app_core, "load_episode_ids_for_selection", lambda *a: ids)
    monkeypatch.setattr(app_core, "with_loading", lambda msg, fn, *a: fn(*a))
    monkeypatch.setattr(app_core, "get_episode_order", lambda *a: "asc")
    monkeypatch.setattr(app_core, "toggle_episode_order", lambda *a: "desc")
    monkeypatch.setattr(app_core, "prepare_show_display_state", lambda *a, **kw: None)
    monkeypatch.setattr(app_core, "_clear_streams", lambda: None)
    monkeypatch.setattr(playback_episodes, "_build_offline_file_lookup", lambda *a: {})
    return ui, ms, args


def test_selection_after_flip_uses_provider_identity(monkeypatch, episode_menu):
    ui, ms, args = episode_menu

    def pick(flags, ui, **hooks):
        view = hooks["read_view"]()
        assert hooks["initial_selected_id"] == "episode:sub:second"
        assert view.items[0].label.runs[0].role == "muted"
        hooks["action_handlers"]["flip"]("second")
        assert hooks["read_view"]().items[0].reference == "third"
        return DetailsMenuResult("selected", "third")

    monkeypatch.setattr(playback_episodes, "run_details_menu", pick, raising=False)
    assert playback_episodes.handle_episode_state(CliFlags(), ui, ms, {}, args, "sub", None) == "PLAY"
    assert (ms.current_ep, ms.current_ep_index) == ("third", 2)
    assert ui.action_prev_state == "EPISODE"


def test_translation_selection_uses_new_catalogue(monkeypatch, episode_menu):
    ui, ms, args = episode_menu

    def translate(show, ttype):
        show.update(_episode_ids=["dub-one", "dub-two"], _episode_ids_ttype=ttype,
                    _episode_labels={"dub-one": "1", "dub-two": "2"})
        return show["_episode_ids"]

    monkeypatch.setattr(app_core, "check_translation_switch_capability", lambda *a: (True, ""))
    monkeypatch.setattr(app_core, "ensure_episode_ids", translate)

    def pick(flags, ui, **hooks):
        hooks["switch_translation"]()
        assert ui.ui_show_ctx["_action_feedback"] == "Switched to Dub."
        assert hooks["read_view"]().items[1].id == "episode:dub:dub-two"
        return DetailsMenuResult("selected", "dub-two")

    monkeypatch.setattr(playback_episodes, "run_details_menu", pick)
    assert playback_episodes.handle_episode_state(CliFlags(), ui, ms, {}, args, "sub", None) == "PLAY"
    assert (ms.current_ep, ms.current_ep_index, ui.ui_ttype_ctx) == ("dub-two", 1, "dub")


def test_missing_local_episode_still_starts_download(monkeypatch, episode_menu):
    ui, ms, args = episode_menu
    ms._is_downloads = True
    monkeypatch.setattr(app_core, "is_online", lambda: True)
    monkeypatch.setattr(playback_episodes, "run_details_menu",
                        lambda *a, **kw: DetailsMenuResult("selected", "second"))
    assert playback_episodes.handle_episode_state(CliFlags(), ui, ms, {}, args, "sub", None) == "PLAY"
    assert args.download is True
    assert ms.current_ep == "second"


def test_single_episode_keeps_direct_play(monkeypatch, episode_menu):
    ui, ms, args = episode_menu
    monkeypatch.setattr(app_core, "load_episode_ids_for_selection", lambda *a: ["first"])
    runner = Mock(side_effect=AssertionError("Single episode should not open a picker"))
    monkeypatch.setattr(playback_episodes, "run_details_menu", runner)
    assert playback_episodes.handle_episode_state(CliFlags(), ui, ms, {}, args, "sub", None) == "PLAY"
    assert ms.current_ep == "first"
    runner.assert_not_called()


def test_season_number_and_stale_alias_do_not_add_a_third_download(monkeypatch, episode_menu, tmp_path):
    ui, ms, args = episode_menu
    ui.ui_show_ctx["_episode_labels"] = {"first": "10 [442]", "second": "31 [463]", "third": "32 [464]"}
    paths = []
    for number, absolute in ((31, 463), (32, 464)):
        path = tmp_path / f"Example Season 10 - Episode {number} [{absolute}].mkv"
        path.write_bytes(b"video")
        paths.append(str(path))
    ms._download_files = {"10": paths[0], "31": paths[0], "32": paths[1],
                          "Example Season 10 - Episode 31 [463].mkv": paths[0]}
    monkeypatch.setattr("media_cli.core.storage.load_downloads_db", lambda: {"shows": {}})
    monkeypatch.setattr(app_core, "get_local_progress", lambda *a: 0)

    def pick(flags, ui, **hooks):
        items = hooks["read_view"]().items
        assert "ready to watch" not in items[0].hint_text
        assert all("ready to watch" in item.hint_text for item in items[1:])
        return DetailsMenuResult("back")

    monkeypatch.setattr(playback_episodes, "run_details_menu", pick)
    assert playback_episodes.handle_episode_state(CliFlags(), ui, ms, {}, args, "sub", None) == "DETAILS"


@pytest.mark.parametrize("global_cover,details_cover", [(True, True), (True, False), (False, True), (False, False)])
def test_episodes_honors_details_cover_setting(monkeypatch, episode_menu, global_cover, details_cover):
    ui, ms, args = episode_menu
    ui.ui_show_ctx["thumbnail"] = "https://images.test/cover.png"

    def pick(flags, ui, **hooks):
        assert (hooks["read_view"]().image is not None) is (global_cover and details_cover)
        return DetailsMenuResult("back")

    monkeypatch.setattr(playback_episodes, "run_details_menu", pick)
    playback_episodes.handle_episode_state(
        CliFlags(show_image=global_cover), ui, ms, {"details_cover": details_cover}, args, "sub", None,
    )


@pytest.mark.parametrize("confirmed", [False, True])
def test_delete_requires_confirmation_for_selected_identity(monkeypatch, episode_menu, tmp_path, confirmed):
    ui, ms, args = episode_menu
    path = tmp_path / "episode.mp4"
    path.write_bytes(b"video")
    ms._download_files = {"second": str(path)}
    delete = Mock(return_value=True)
    confirm = Mock(return_value=confirmed)
    monkeypatch.setattr(app_core, "delete_offline_episode", delete)
    monkeypatch.setattr(playback_episodes, "confirm_view_action", confirm, raising=False)
    monkeypatch.setattr(app_core, "build_info_panel", Mock(side_effect=AssertionError("Legacy confirmation header")))

    def pick(flags, ui, **hooks):
        assert any(action.id == "delete" for action in hooks["read_view"]().actions)
        hooks["action_handlers"]["flip"]("second")
        hooks["action_handlers"]["delete"]("second")
        return DetailsMenuResult("back")

    monkeypatch.setattr(playback_episodes, "run_details_menu", pick, raising=False)
    assert playback_episodes.handle_episode_state(CliFlags(), ui, ms, {}, args, "sub", None) == "DETAILS"
    assert confirm.call_args.kwargs["item_id"] == "episode:sub:second"
    assert confirm.call_args.args[2].items[1].reference == "second"
    assert confirm.call_args.kwargs["question"] == "Delete downloaded EP 2?"
    if confirmed:
        delete.assert_called_once_with("Example", "2", {})
    else:
        delete.assert_not_called()
