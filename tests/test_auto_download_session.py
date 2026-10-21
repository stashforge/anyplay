import threading
from types import SimpleNamespace

import pytest

from media_cli.app import playback_player
from media_cli.context import MachineState
from media_cli.media.download_worker import DownloadItem, DownloadStatus, DownloadWorker


@pytest.mark.parametrize(
    "incognito,requested,expected",
    [
        (True, None, "off"),
        (True, True, "adaptive"),
        (True, False, "off"),
        (False, None, "adaptive"),
        (False, False, "off"),
    ],
)
def test_initial_mode_prefers_explicit_request_then_incognito_default(incognito, requested, expected):
    cfg = {"auto_download_next": "adaptive"}
    assert playback_player.initial_auto_download_mode(cfg, incognito=incognito, requested=requested) == expected
    assert cfg == {"auto_download_next": "adaptive"}


def test_off_mode_never_creates_a_worker_or_queued_item(monkeypatch):
    monkeypatch.setattr("media_cli.media.download_worker.get_global_worker", lambda *a: pytest.fail("Started worker"))
    monkeypatch.setattr(playback_player.app_core, "is_online", lambda: pytest.fail("Off mode reached network checks"))
    playback_player.trigger_background_auto_download(
        {"name": "Example"},
        0,
        ("1", "2"),
        "sub",
        {"auto_download_next": "adaptive"},
        MachineState(auto_download_next="off"),
        current_stream={"link": "https://example.test/video"},
    )


def test_switching_off_cancels_only_automatic_unfinished_downloads(monkeypatch):
    automatic = DownloadItem("auto", "Example", "2", "/tmp/auto.mkv", automatic=True, status=DownloadStatus.PAUSED)
    manual = DownloadItem("manual", "Example", "3", "/tmp/manual.mkv", status=DownloadStatus.PAUSED)
    completed = DownloadItem("done", "Example", "4", "/tmp/done.mkv", automatic=True, status=DownloadStatus.COMPLETED)
    cancelled = []
    worker = SimpleNamespace(
        get_items=lambda: [automatic, manual, completed],
        cancel_item=cancelled.append,
        discard_finished_item=lambda item_id: None,
    )
    monkeypatch.setattr("media_cli.media.download_worker.get_global_worker_if_started", lambda: worker)
    ms = MachineState(auto_download_next="adaptive")
    playback_player.set_auto_download_enabled(ms, False)
    assert ms.auto_download_next == "off"
    assert cancelled == ["auto"]
    playback_player.set_auto_download_enabled(ms, True)
    assert ms.auto_download_next == "adaptive"
    assert cancelled == ["auto"]


def test_disabling_with_no_worker_does_not_start_one(monkeypatch):
    monkeypatch.setattr("media_cli.media.download_worker.get_global_worker_if_started", lambda: None)
    monkeypatch.setattr("media_cli.media.download_worker.get_global_worker", lambda *a: pytest.fail("Started worker"))
    ms = MachineState()
    playback_player.set_auto_download_enabled(ms, False)
    assert ms.auto_download_next == "off"


def test_disabling_removes_waiting_auto_row_so_reenable_can_queue_again(monkeypatch):
    worker = object.__new__(DownloadWorker)
    worker.lock = threading.RLock()
    worker._wake_event = threading.Event()
    automatic = DownloadItem(
        "auto", "Example", "2", "/tmp/auto.mkv", automatic=True, ep_id="2", status=DownloadStatus.PAUSED
    )
    manual = DownloadItem("manual", "Example", "3", "/tmp/manual.mkv", status=DownloadStatus.PAUSED)
    worker.items = [automatic, manual]
    monkeypatch.setattr("media_cli.media.download_worker.get_global_worker_if_started", lambda: worker)
    ms = MachineState(auto_download_next="adaptive")
    ms._bg_download_tasks = {("Example", "2")}
    playback_player.set_auto_download_enabled(ms, False)
    assert automatic.status is DownloadStatus.CANCELLED
    assert worker.get_items() == [manual]
    assert ms._bg_download_tasks == set()
    playback_player.set_auto_download_enabled(ms, True)
    replacement = DownloadItem("auto", "Example", "2", "/tmp/auto.mkv", automatic=True)
    assert worker.add_item(replacement) is replacement


@pytest.mark.parametrize("mode", ["off", "adaptive"])
def test_android_completion_receives_session_mode_not_saved_config(monkeypatch, mode):
    from media_cli.context import CliFlags, UiState
    from media_cli.ui import player_screen

    core = playback_player.app_core
    data = {"episode": {"sourceUrls": []}}
    show = {"_id": "one", "_provider": "example", "name": "Example"}
    ms = MachineState(
        current_ep="1",
        auto_download_next=mode,
        selected_stream={"link": "file", "source_name": "Local File"},
        ep_cache_key=("one", "1", "sub", "example"),
        ep_cache_data=data,
    )
    monkeypatch.setattr(player_screen, "_player_ui_state", {})
    monkeypatch.setattr(player_screen, "reset_loading_cancel", lambda: None)
    monkeypatch.setattr(player_screen, "loading_cancel_requested", lambda: False)
    monkeypatch.setattr(core, "with_loading", lambda msg, fn: fn())
    monkeypatch.setattr(core, "prepare_show_display_state", lambda *a: None)
    monkeypatch.setattr(core, "ensure_episode_ids", lambda *a: ["1"])
    monkeypatch.setattr(core, "get_show_anilist_id", lambda *a: None)
    monkeypatch.setattr(core, "get_show_mal_id", lambda *a, **kw: None)
    monkeypatch.setattr(core, "get_custom_skips", lambda *a: {})
    monkeypatch.setattr(core, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(core, "_ipc_player", None)
    monkeypatch.setattr(core, "_exit_player_screen", lambda *a, **kw: None)
    monkeypatch.setattr(core, "is_termux", lambda: True)
    monkeypatch.setattr(playback_player, "_select_android_player", lambda *a, **kw: "mpv")
    monkeypatch.setattr(playback_player, "trigger_background_auto_download", lambda *a: None)
    received = []
    monkeypatch.setattr(core, "play_android", lambda *a, **kw: received.append(kw["cfg"]) or False)
    cfg = {"auto_download_next": "adaptive"}
    args = SimpleNamespace(player=None, aniskip=False, download=False, print_url=False, sources=False, binge=False)
    assert (
        playback_player.handle_play_state(
            CliFlags(incognito_mode=True), UiState(ui_show_ctx=show), ms, cfg, args, "sub", "best", lambda *a: False
        )
        == "DETAILS"
    )
    assert received[0]["auto_download_next"] == mode
    assert cfg == {"auto_download_next": "adaptive"}
