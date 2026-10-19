import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from media_cli.app import playback_player
from media_cli.context import CliFlags, MachineState, UiState
from media_cli.ui import player_screen
from media_cli.ui.transition import PreparationCancelled


@pytest.mark.parametrize("installed,expected", [
    ((), "mpv"),
    (("is.xyz.mpv",), "mpv"),
    (("app.gyrolet.mpvrx",), "mpvrx"),
    (("xyz.mpv.rex",), "rex"),
    (("app.gyrolet.mpvrx", "xyz.mpv.rex"), "mpvrx"),
    (("app.gyrolet.mpvrx", "xyz.mpv.rex", "is.xyz.mpv"), "mpv"),
])
def test_android_default_detection_runs_on_worker_and_preserves_preference(monkeypatch, installed, expected):
    owner = threading.current_thread()

    def check(package):
        assert threading.current_thread() is not owner
        return package in installed

    def pump(work, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(work).result(timeout=1)

    monkeypatch.setattr(playback_player.app_core, "pkg_installed", check)
    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    assert playback_player._select_android_player(None) == expected


@pytest.mark.parametrize("chosen,expected", [("mpv", "mpv"), ("mpvrex", "rex"), ("vlc", "vlc")])
def test_explicit_android_player_bypasses_detection(monkeypatch, chosen, expected):
    monkeypatch.setattr(playback_player.app_core, "pkg_installed", lambda *a: pytest.fail("Explicit player triggered detection"))
    monkeypatch.setattr(player_screen, "run_loading_work", lambda *a, **kw: pytest.fail("Explicit player started loading"))
    assert playback_player._select_android_player(chosen) == expected


def test_cancelled_detection_does_not_continue_package_checks(monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls, pending = [], []

    def check(package):
        calls.append(package)
        started.set()
        assert release.wait(2)
        return True

    monkeypatch.setattr(playback_player.app_core, "pkg_installed", check)
    with ThreadPoolExecutor(max_workers=1) as pool:
        def pump(work, **kwargs):
            pending.append(pool.submit(work))
            assert started.wait(1)
            raise PreparationCancelled

        monkeypatch.setattr(player_screen, "run_loading_work", pump)
        try:
            with pytest.raises(PreparationCancelled):
                playback_player._select_android_player(None)
        finally:
            release.set()
        with pytest.raises(PreparationCancelled):
            pending[0].result(timeout=1)
    assert calls == ["app.gyrolet.mpvrx"]


@pytest.mark.parametrize("failure", [PreparationCancelled, KeyboardInterrupt])
def test_android_detection_cancel_returns_to_details_without_launch(monkeypatch, failure):
    core = playback_player.app_core
    show = {"_id": "one", "_provider": "torrent", "name": "Example"}
    ui = UiState(ui_show_ctx=show)
    ms = MachineState(current_ep="1", selected_stream={"link": "file", "source_name": "Local File"},
                      ep_cache_key=("one", "1", "sub", "torrent"), ep_cache_data={"episode": {"sourceUrls": []}})
    calls = []

    def cancel(*args, **kwargs):
        raise failure

    monkeypatch.setattr(player_screen, "_player_ui_state", {})
    monkeypatch.setattr(player_screen, "reset_loading_cancel", lambda: None)
    monkeypatch.setattr(player_screen, "loading_cancel_requested", lambda: False)
    monkeypatch.setattr(core, "with_loading", lambda msg, work: work())
    monkeypatch.setattr(core, "prepare_show_display_state", lambda *a: None)
    monkeypatch.setattr(core, "ensure_episode_ids", lambda *a: ["1"])
    monkeypatch.setattr(core, "get_show_anilist_id", lambda *a: None)
    monkeypatch.setattr(core, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(core, "_ipc_player", None)
    monkeypatch.setattr(core, "_exit_player_screen", lambda *a, **kw: calls.append("exit"))
    monkeypatch.setattr(core, "set_action_feedback", lambda *a: calls.append("feedback"))
    monkeypatch.setattr(core, "is_termux", lambda: True)
    monkeypatch.setattr(core, "play_android", lambda *a, **kw: pytest.fail("Cancelled detection launched playback"))
    monkeypatch.setattr(playback_player, "_select_android_player", cancel)
    args = SimpleNamespace(player=None, aniskip=False, download=False, print_url=False, sources=False, binge=False)
    def handle():
        return playback_player.handle_play_state(
            CliFlags(), ui, ms, {}, args, "sub", "best", lambda *a: False,
        )

    if failure is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            handle()
    else:
        assert handle() == "DETAILS"
        assert calls == ["exit", "feedback"]


def test_package_query_still_reuses_cache_and_keeps_timeout(monkeypatch):
    from media_cli.playback import android

    calls = []

    def query(command, **kwargs):
        calls.append(command)
        assert kwargs == {"capture_output": True, "text": True, "timeout": 5}
        return SimpleNamespace(stdout="package:is.xyz.mpv\n")

    monkeypatch.setattr(android, "_packages", None)
    monkeypatch.setattr(android.subprocess, "run", query)
    assert android.package_installed("is.xyz.mpv")
    assert not android.package_installed("app.gyrolet.mpvrx")
    assert calls == [["pm", "list", "packages"]]
