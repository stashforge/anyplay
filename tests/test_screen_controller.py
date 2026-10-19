from types import SimpleNamespace

import pytest


def test_screen_controller_commits_only_an_accepted_prepared_result(monkeypatch):
    from media_cli.app import terminal_controller
    from media_cli.ui.transition import PreparationCancelled

    flags = SimpleNamespace(plain_mode=False)
    ui = SimpleNamespace(ui_show_ctx={"name": "Source"}, search_prev_state="SEARCH")
    ms = SimpleNamespace(just_searched=False, just_picked_anime=False)
    cfg, args = {}, SimpleNamespace(episode=None)
    monkeypatch.setattr(terminal_controller.sys, "stdin", SimpleNamespace(isatty=lambda: True))

    def handler(flags, ui, ms, cfg, args):
        ui.ui_show_ctx["name"] = "Destination"
        args.episode = "2"
        return "DETAILS"

    def cancel(message, function):
        function()
        raise PreparationCancelled

    monkeypatch.setattr(terminal_controller, "with_loading", cancel)
    assert terminal_controller.run_screen_controller(handler, flags, ui, ms, cfg, args, fallback="SEARCH") == "SEARCH"
    assert ui.ui_show_ctx == {"name": "Source"}
    assert args.episode is None
    monkeypatch.setattr(terminal_controller, "with_loading", lambda msg, fn: fn())
    assert terminal_controller.run_screen_controller(handler, flags, ui, ms, cfg, args, fallback="SEARCH") == "DETAILS"
    assert ui.ui_show_ctx == {"name": "Destination"}
    assert args.episode == "2"


def test_plain_controller_does_not_start_background_ui(monkeypatch):
    from media_cli.app import terminal_controller

    flags = SimpleNamespace(plain_mode=True)
    ui, ms, args, cfg = SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), {}
    monkeypatch.setattr(terminal_controller, "with_loading", lambda *a: pytest.fail("plain spinner"))
    assert (
        terminal_controller.run_screen_controller(lambda *a: "QUIT", flags, ui, ms, cfg, args, fallback="SEARCH")
        == "QUIT"
    )


