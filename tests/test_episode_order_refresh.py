"""Order flips reuse row status; actual catalogue refreshes renew it once."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from media_cli import app_core
from media_cli.app import playback_episodes
from media_cli.app.details_menu_controller import DetailsMenuResult
from media_cli.context import CliFlags, MachineState, UiState


@pytest.mark.parametrize("watched", [[], ["1"]])
def test_order_flip_reuses_rows_without_reloading_status(monkeypatch, watched):
    ids = [str(i) for i in range(1, 162)]
    show = dict(_id="show", name="Example", _provider="example", _episode_ids=ids)
    ui = UiState(ui_show_ctx=show, ep_prev_state="DETAILS")
    ms = MachineState(show_id="show", show_title="Example", current_ep="1")
    reads = Mock(return_value={"shows": {"Example": {"watched_episodes": watched}}})
    progress = Mock(return_value=0)
    order = {"value": "asc"}

    def toggle(*args):
        assert args == ("show", "asc", "example")
        order["value"] = "desc" if order["value"] == "asc" else "asc"
        return order["value"]

    monkeypatch.setattr("media_cli.core.storage.load_downloads_db", reads)
    monkeypatch.setattr(app_core, "get_local_progress", progress)
    monkeypatch.setattr(app_core, "load_episode_ids_for_selection", lambda *a: ids)
    monkeypatch.setattr(app_core, "ensure_episode_ids", lambda *a: ids)
    monkeypatch.setattr(app_core, "check_translation_switch_capability", lambda *a: (True, ""))
    monkeypatch.setattr(app_core, "with_loading", lambda msg, fn, *a: fn(*a))
    def read_order(show_id, default, provider_id):
        assert (show_id, default, provider_id) == ("show", "asc", "example")
        return order["value"]

    monkeypatch.setattr(app_core, "get_episode_order", read_order)
    monkeypatch.setattr(app_core, "toggle_episode_order", toggle)
    monkeypatch.setattr(app_core, "build_info_panel", lambda *a, **kw: None)
    monkeypatch.setattr(playback_episodes, "_build_offline_file_lookup", lambda *a: {})

    def pick(flags, ui, **hooks):
        assert reads.call_count == 1
        assert progress.call_count == 1
        original = hooks["read_view"]().items
        if watched:
            assert original[0].label.runs[0].role == "muted"
        for expected in (original[::-1], original):
            hooks["action_handlers"]["flip"]("1")
            assert hooks["read_view"]().items == expected
            assert reads.call_count == 1
            assert progress.call_count == 1
        reads.return_value = {"shows": {"Example": {"watched_episodes": ["2"]}}}
        hooks["switch_translation"]()
        refreshed = hooks["read_view"]()
        assert reads.call_count == 2
        assert progress.call_count == 2
        assert refreshed.items[1].label.runs[0].role == "muted"
        assert ui.ui_ttype_ctx == "dub"
        return DetailsMenuResult("back")

    monkeypatch.setattr(playback_episodes, "run_details_menu", pick, raising=False)
    assert playback_episodes.handle_episode_state(
        CliFlags(), ui, ms, {}, SimpleNamespace(), "sub", lambda *a: False,
    ) == "DETAILS"
