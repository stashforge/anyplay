from types import SimpleNamespace

import pytest

from media_cli.ui.background_loading import BackgroundLoadingStatus


@pytest.mark.parametrize("outcome", ["complete", "search-error", "back", "picker-error"])
def test_animethemes_search_status_uses_shared_adapter_and_retires(monkeypatch, outcome):
    from media_cli.app import animethemes as flow

    reads = []
    workers = []

    class DeferredThread:
        def __init__(self, *, target, daemon):
            workers.append(target)

        def start(self):
            pass

    class ObservedStatus(BackgroundLoadingStatus):
        def read(self, active):
            reads.append(active)
            return super().read(active)

    def search(query):
        if outcome == "search-error":
            raise RuntimeError("Search unavailable")
        return {}

    def render(*args, **kwargs):
        status = args[4]()
        return status.message if status else kwargs["get_error_fn"]()

    monkeypatch.setattr(flow.threading, "Thread", DeferredThread)
    monkeypatch.setattr(flow.animethemes_service, "search", search)
    monkeypatch.setattr(flow.app_core, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(flow.app_core, "make_info_fn", lambda *args: None)
    monkeypatch.setattr(flow.app_core, "render_search_header", render)
    monkeypatch.setattr("media_cli.ui.background_loading.BackgroundLoadingStatus", ObservedStatus)

    def pick(*args, **kwargs):
        assert kwargs["header_fn"](0) == "Searching…"
        assert reads == [True]
        if outcome in ("complete", "search-error"):
            workers[0]()
            assert kwargs["header_fn"](0) == ("AnimeThemes search failed." if outcome == "search-error" else "")
            assert reads == [True, False]
            assert kwargs["live_fn"]()[2] is True
        if outcome == "picker-error":
            raise ValueError("Picker stopped")
        return -2

    monkeypatch.setattr(flow, "tui_pick", pick)

    def run():
        return flow.handle_animethemes_search_state(
            None, SimpleNamespace(), SimpleNamespace(query_str="oshi no ko"), {}, SimpleNamespace(), "sub",
        )

    if outcome == "picker-error":
        with pytest.raises(ValueError, match="Picker stopped"):
            run()
    else:
        assert run() == "ANIMETHEMES_SEARCH"
    assert reads[-1] is False
