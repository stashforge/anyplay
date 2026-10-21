from types import SimpleNamespace

import pytest

from media_cli.context import CliFlags, MachineState, UiState
from media_cli.core.terminal import strip_ansi
from media_cli.ui.background_loading import BackgroundLoadingStatus
from media_cli.ui.footer import render_footer


@pytest.mark.parametrize("exit_state", ["completed", "active", "error"])
def test_history_updating_uses_shared_status_and_cleans_up_on_exit(monkeypatch, exit_state):
    from media_cli.app import history_legacy_picker, history_menu_controller, search

    reads = []
    history = [{"show": {"_id": "a", "name": "Example"}, "translation_type": "sub"}]
    busy = {"BATCH": "Refreshing 13 titles..."}
    controllers = []
    original = history_menu_controller.HistoryMenuController

    class ObservedController(original):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            controllers.append(self)

    monkeypatch.setattr(history_menu_controller, "HistoryMenuController", ObservedController)

    class ObservedStatus(BackgroundLoadingStatus):
        def read(self, active, **kwargs):
            reads.append(active)
            return super().read(active, **kwargs)

    monkeypatch.setattr(search, "BackgroundLoadingStatus", ObservedStatus)
    monkeypatch.setattr(search, "history_refresh_status", busy)
    monkeypatch.setattr(search.app_core, "load_history", lambda: history)
    monkeypatch.setattr(search.app_core, "batch_prepare_shows_display_state", lambda *args: None)
    monkeypatch.setattr(search.app_core, "_poster_manager", SimpleNamespace())
    monkeypatch.setattr(search.app_core, "filter_history_entries", lambda entries, mode: list(entries))
    monkeypatch.setattr(search.app_core, "format_history_entry", lambda entry: "Example")
    monkeypatch.setattr(search.app_core, "format_history_updated_time", lambda entry: "")
    monkeypatch.setattr(search.app_core, "build_info_panel", lambda *args, **kwargs: None)
    monkeypatch.setattr(search.app_core, "make_info_fn", lambda *args: None)
    monkeypatch.setattr(search.app_core, "_poster_footer_line", lambda show, footer, width: render_footer(footer, width))
    monkeypatch.setattr(history_legacy_picker, "context_prefix", lambda *args, **kwargs: "History")

    def pick(*args, **kwargs):
        control = controllers[0]
        assert control.session.current.footer_statuses[0].message == "Refreshing 13 titles..."
        assert not control.session.input_blocked
        before = len(reads)
        assert "Refreshing 13 titles..." in strip_ansi(kwargs["header_fn"](0))
        assert reads[before:] == [True]
        if exit_state == "completed":
            busy.clear()
            busy["SINGLE"] = "Refreshing this title..."
            assert "Refreshing this title..." in strip_ansi(kwargs["header_fn"](0))
            busy.clear()
            assert "Refreshing" not in strip_ansi(kwargs["header_fn"](0))
            assert reads[before:] == [True, True, False]
            for command in control.updates(force=True):
                control.session.apply(command)
            assert control.session.current.footer_statuses == ()
        if exit_state == "error":
            raise ValueError("Picker stopped")
        return -2

    monkeypatch.setattr(search, "tui_pick", pick)
    flags = CliFlags(incognito_mode=True)

    def run():
        return search.handle_history_state(
            flags, UiState(), MachineState(), {}, SimpleNamespace(), "sub", lambda *args: False,
        )

    if exit_state == "error":
        with pytest.raises(ValueError, match="Picker stopped"):
            run()
    else:
        assert run() == "QUIT"
    assert reads[-1] is False
