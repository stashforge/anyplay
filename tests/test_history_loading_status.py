from types import SimpleNamespace

import pytest

from media_cli.context import CliFlags, MachineState, UiState
from media_cli.core.terminal import strip_ansi
from media_cli.ui.background_loading import BackgroundLoadingStatus
from media_cli.ui.footer import render_footer


@pytest.mark.parametrize("exit_state", ["completed", "active", "error"])
def test_history_updating_uses_shared_status_and_cleans_up_on_exit(monkeypatch, exit_state):
    from media_cli.app import search

    reads = []
    history = [{"show": {"_id": "a", "name": "Example"}, "translation_type": "sub"}]
    busy = {"BATCH": "Checking stale titles..."}

    class ObservedStatus(BackgroundLoadingStatus):
        def read(self, active):
            reads.append(active)
            return super().read(active)

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
    monkeypatch.setattr(search, "context_prefix", lambda *args, **kwargs: "History")

    def pick(*args, **kwargs):
        assert "Updating" in strip_ansi(kwargs["header_fn"](0))
        assert reads == [True]
        if exit_state == "completed":
            busy.clear()
            assert "Updating" not in strip_ansi(kwargs["header_fn"](0))
            assert reads == [True, False]
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
