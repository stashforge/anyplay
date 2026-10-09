import argparse

from media_cli.context import CliFlags, MachineState, UiState


def test_history_sanitizer_strips_runtime_episode_catalog_maps():
    from media_cli.core.storage import sanitize_show_for_history

    show = {
        "_id": "show-1",
        "_provider": "lucifer",
        "name": "Demo",
        "_episode_ids": ["https://provider/ep-1"],
        "_episode_ids_ttype": "sub",
        "_episode_ids_by_ttype": {"sub": ["https://provider/ep-1"]},
        "_episode_labels": {"https://provider/ep-1": "1 [101] [4K]"},
        "_episodes_map": {
            "https://provider/ep-1": {
                "id": "https://provider/ep-1",
                "label": "1 [101] [4K]",
            }
        },
        "_episodes_map_by_ttype": {
            "sub": {
                "https://provider/ep-1": {
                    "id": "https://provider/ep-1",
                    "label": "1 [101] [4K]",
                }
            }
        },
        "_episodes_by_number": {"1": "https://provider/ep-1", "101": "https://provider/ep-1"},
        "_episodes_by_number_by_ttype": {
            "sub": {"1": "https://provider/ep-1", "101": "https://provider/ep-1"}
        },
        "availableEpisodes": {"sub": 1},
    }

    stored = sanitize_show_for_history(show)

    assert stored["_provider"] == "lucifer"
    assert stored["availableEpisodes"] == {"sub": 1}
    for key in (
        "_episode_ids",
        "_episode_ids_ttype",
        "_episode_ids_by_ttype",
        "_episode_labels",
        "_episodes_map",
        "_episodes_map_by_ttype",
        "_episodes_by_number",
        "_episodes_by_number_by_ttype",
    ):
        assert key not in stored


def test_available_metadata_uses_latest_provider_episode_label():
    from media_cli.domain.metadata import format_available_episodes

    show = {
        "status": "RELEASING",
        "_episode_ids": [
            "https://lucifer/show-season-5-episode-18-194/",
            "https://lucifer/show-season-5-episode-19-195/",
        ],
        "_episode_labels": {
            "https://lucifer/show-season-5-episode-18-194/": "18 [194] [4K]",
            "https://lucifer/show-season-5-episode-19-195/": "19 [195] [4K]",
        },
        "availableEpisodes": {"sub": 2},
    }

    assert format_available_episodes(show, "sub") == "Avail 19 [195]"


def test_background_auto_download_uses_provider_key(monkeypatch, tmp_path):
    from media_cli.app import playback_player

    show = {
        "_id": "provider-title-456",
        "_provider": "anikoto",
        "_provider_id": "provider-title-456",
        "name": "Demo Show",
        "_episode_labels": {"1": "Episode 1", "2": "Episode 2"},
    }
    ms = MachineState(show_title="Demo Show", auto_download_next=True)
    cfg = {
        "auto_download_next": True,
        "download_dir": str(tmp_path),
        "quality": "best",
    }
    captured = {}

    class SyncThread:
        def __init__(self, target, daemon=False):
            self.target = target
            self.daemon = daemon

        def start(self):
            self.target()

    def fake_fetch(show_id, ep, ttype, quality="best", provider_id=None, ep_data=None):
        captured["show_id"] = show_id
        captured["provider_id"] = provider_id
        return ({"link": "https://example.test/video.m3u8"}, "Mirror", {}, [])

    monkeypatch.setattr(playback_player.app_core, "is_online", lambda: True)
    monkeypatch.setattr(playback_player.app_core, "find_offline_file_for_episode", lambda *a, **k: None)
    monkeypatch.setattr(playback_player.app_core, "fetch_episode_stream", fake_fetch)
    monkeypatch.setattr(playback_player.app_core, "download_episode", lambda *a, **k: True)
    monkeypatch.setattr(playback_player.app_core, "set_action_feedback", lambda *a, **k: None)
    monkeypatch.setattr("threading.Thread", SyncThread)

    playback_player.trigger_background_auto_download(show, 0, ["1", "2"], "sub", cfg, ms)

    assert captured["show_id"] == "provider-title-456"
    assert captured["provider_id"] == "anikoto"


def test_background_auto_download_prefers_provider_title_id(monkeypatch, tmp_path):
    from media_cli.app import playback_player

    show = {
        "_id": "anilist-123",
        "_provider": "anikoto",
        "_provider_id": "provider-title-456",
        "name": "Demo Show",
        "_episode_labels": {"1": "Episode 1", "2": "Episode 2"},
    }
    ms = MachineState(show_title="Demo Show", auto_download_next=True)
    cfg = {
        "auto_download_next": True,
        "download_dir": str(tmp_path),
        "quality": "best",
    }
    captured = {}

    class SyncThread:
        def __init__(self, target, daemon=False):
            self.target = target
            self.daemon = daemon

        def start(self):
            self.target()

    def fake_fetch(show_id, ep, ttype, quality="best", provider_id=None, ep_data=None):
        captured["show_id"] = show_id
        captured["provider_id"] = provider_id
        return ({"link": "https://example.test/video.m3u8"}, "Mirror", {}, [])

    monkeypatch.setattr(playback_player.app_core, "is_online", lambda: True)
    monkeypatch.setattr(playback_player.app_core, "find_offline_file_for_episode", lambda *a, **k: None)
    monkeypatch.setattr(playback_player.app_core, "fetch_episode_stream", fake_fetch)
    monkeypatch.setattr(playback_player.app_core, "download_episode", lambda *a, **k: True)
    monkeypatch.setattr(playback_player.app_core, "set_action_feedback", lambda *a, **k: None)
    monkeypatch.setattr("threading.Thread", SyncThread)

    playback_player.trigger_background_auto_download(show, 0, ["1", "2"], "sub", cfg, ms)

    assert captured["show_id"] == "provider-title-456"
    assert captured["provider_id"] == "anikoto"


def test_auto_delete_prunes_downloaded_watched_episode_after_normal_playback(monkeypatch):
    import argparse

    from media_cli.app import playback_player

    flags = CliFlags()
    ui = UiState()
    show = {
        "_id": "demo-show",
        "_provider": "anikoto",
        "name": "Demo Show",
        "_folder_name": "Demo Show",
        "_episode_ids": ["1", "2", "3"],
        "_episode_labels": {"1": "1", "2": "2", "3": "3"},
        "availableEpisodes": {"sub": 3},
        "_downloaded_episodes": ["1", "2", "3"],
        "watched_episodes": ["1"],
    }
    ui.ui_show_ctx = show
    ms = MachineState(show_title="Demo Show", auto_delete_watched=True, auto_delete_buffer=1)
    ms.show_id = "demo-show"
    ms.current_ep = "2"
    ms.current_ep_index = 1
    ms.total_eps = 3
    ms.selected_stream = {"link": "https://example.test/ep2.m3u8", "source_name": "Provider"}
    ms._download_files = {
        "1": "/tmp/demo/ep1.mp4",
        "2": "/tmp/demo/ep2.mp4",
        "3": "/tmp/demo/ep3.mp4",
    }
    args = argparse.Namespace(
        download=False,
        print_url=False,
        sources=False,
        binge=False,
        aniskip=False,
        auto_skip=False,
        quality=None,
        extra_args=[],
    )
    cfg = {"auto_delete_watched": True, "auto_delete_buffer": 1, "auto_download_next": False}
    deleted = []

    monkeypatch.setattr(playback_player.app_core, "prepare_show_display_state", lambda *a, **k: None)
    monkeypatch.setattr(playback_player.app_core, "ensure_episode_ids", lambda *a, **k: ["1", "2", "3"])
    monkeypatch.setattr(playback_player.app_core, "get_show_anilist_id", lambda *a, **k: None)
    monkeypatch.setattr(playback_player.app_core, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(playback_player.app_core, "_exit_player_screen", lambda: None)
    monkeypatch.setattr(playback_player.app_core, "is_termux", lambda: False)
    monkeypatch.setattr(playback_player.app_core, "play_desktop", lambda *a, **k: ("EOF", 100.0, 1400.0, 1400.0, 1400.0))
    monkeypatch.setattr(playback_player.app_core, "save_history", lambda *a, **k: None)
    monkeypatch.setattr(playback_player.app_core, "touch_history", lambda *a, **k: None)
    monkeypatch.setattr(playback_player.app_core, "save_resume_time", lambda *a, **k: None)
    monkeypatch.setattr(playback_player.app_core, "clear_pending_completion", lambda *a, **k: None)
    monkeypatch.setattr(playback_player.app_core, "set_action_feedback", lambda *a, **k: None)
    monkeypatch.setattr(playback_player.app_core, "delete_offline_episode", lambda folder, ep, cfg=None: deleted.append(str(ep)) or True)

    next_state = playback_player.handle_play_state(
        flags,
        ui,
        ms,
        cfg,
        args,
        "sub",
        "best",
        lambda *a, **k: False,
    )

    assert next_state == "DETAILS"
    assert "1" in deleted
    assert "2" not in deleted
    assert "1" not in ms._download_files


def test_mpv_next_prefetch_prefers_downloaded_episode_outside_downloads_mode(monkeypatch, tmp_path):
    from media_cli.app import playback_player

    flags = CliFlags()
    ui = UiState()
    show = {
        "_id": "provider-show",
        "_provider": "anikoto",
        "name": "Provider Show",
        "_episode_ids": ["1", "2"],
        "_episode_labels": {"1": "1", "2": "2"},
        "availableEpisodes": {"sub": 2},
    }
    ui.ui_show_ctx = show
    ms = MachineState(show_title="Provider Show")
    ms.show_id = "provider-show"
    ms.current_ep = "1"
    ms.current_ep_index = 0
    ms.total_eps = 2
    ms.selected_stream = {"link": "https://example.test/ep1.m3u8", "source_name": "Provider"}

    local_ep2 = tmp_path / "Provider Show - Episode 2.mp4"
    local_ep2.write_bytes(b"\0" * 2048)
    captured = {}

    def fake_play_desktop(title, episode, stream, fetch_cb, *args, **kwargs):
        captured["prefetched"] = fetch_cb("2")
        return ("QUIT", 0.0, 0.0, 0.0, 0.0)

    def fake_find_offline(show_title, episode, cfg=None):
        if show_title == "Provider Show" and str(episode) in {"2", "Episode 2"}:
            return str(local_ep2)
        return None

    monkeypatch.setattr(playback_player.app_core, "prepare_show_display_state", lambda *a, **k: None)
    monkeypatch.setattr(playback_player.app_core, "ensure_episode_ids", lambda *a, **k: ["1", "2"])
    monkeypatch.setattr(playback_player.app_core, "get_show_anilist_id", lambda *a, **k: None)
    monkeypatch.setattr(playback_player.app_core, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(playback_player.app_core, "_exit_player_screen", lambda: None)
    monkeypatch.setattr(playback_player.app_core, "is_termux", lambda: False)
    monkeypatch.setattr(playback_player.app_core, "play_desktop", fake_play_desktop)
    monkeypatch.setattr(playback_player.app_core, "find_offline_file_for_episode", fake_find_offline)
    monkeypatch.setattr(
        playback_player.app_core,
        "fetch_episode_stream",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("downloaded next episode should be used before online fetch")
        ),
    )
    monkeypatch.setattr(playback_player.app_core, "touch_history", lambda *a, **k: None)
    monkeypatch.setattr(playback_player.app_core, "save_resume_time", lambda *a, **k: None)
    monkeypatch.setattr(playback_player.app_core, "set_action_feedback", lambda *a, **k: None)

    args = argparse.Namespace(
        download=False,
        print_url=False,
        sources=False,
        binge=False,
        aniskip=False,
        auto_skip=False,
        quality=None,
        extra_args=[],
    )

    state = playback_player.handle_play_state(
        flags,
        ui,
        ms,
        {"download_dir": str(tmp_path)},
        args,
        "sub",
        "best",
        lambda *a, **k: False,
    )

    assert state == "DETAILS"
    assert captured["prefetched"][0]["link"] == str(local_ep2)
    assert captured["prefetched"][0]["is_local"] is True
    assert captured["prefetched"][1] == "Local File"


def test_mpv_next_prefetch_falls_back_online_when_download_is_invalid(monkeypatch, tmp_path):
    from media_cli.app import playback_player

    flags = CliFlags()
    ui = UiState()
    show = {
        "_id": "provider-show",
        "_provider": "anikoto",
        "name": "Provider Show",
        "_episode_ids": ["1", "2"],
        "_episode_labels": {"1": "1", "2": "2"},
        "availableEpisodes": {"sub": 2},
    }
    ui.ui_show_ctx = show
    ms = MachineState(show_title="Provider Show")
    ms.show_id = "provider-show"
    ms.current_ep = "1"
    ms.current_ep_index = 0
    ms.total_eps = 2
    ms.selected_stream = {"link": "https://example.test/ep1.m3u8", "source_name": "Provider"}
    bad_local_ep2 = tmp_path / "Provider Show - Episode 2.mp4"
    bad_local_ep2.write_text("#EXTM3U\nnot a real downloaded video\n", encoding="utf-8")
    ms._download_files = {"2": str(bad_local_ep2)}
    captured = {}

    def fake_play_desktop(title, episode, stream, fetch_cb, *args, **kwargs):
        captured["prefetched"] = fetch_cb("2")
        return ("QUIT", 0.0, 0.0, 0.0, 0.0)

    def fake_fetch(*args, **kwargs):
        captured["online_fetch_called"] = True
        return (
            {"link": "https://example.test/ep2.m3u8", "source_name": "Provider"},
            "Provider",
            {"episode": {"sourceUrls": []}},
            [],
        )

    monkeypatch.setattr(playback_player.app_core, "prepare_show_display_state", lambda *a, **k: None)
    monkeypatch.setattr(playback_player.app_core, "ensure_episode_ids", lambda *a, **k: ["1", "2"])
    monkeypatch.setattr(playback_player.app_core, "get_show_anilist_id", lambda *a, **k: None)
    monkeypatch.setattr(playback_player.app_core, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(playback_player.app_core, "_exit_player_screen", lambda: None)
    monkeypatch.setattr(playback_player.app_core, "is_termux", lambda: False)
    monkeypatch.setattr(playback_player.app_core, "play_desktop", fake_play_desktop)
    monkeypatch.setattr(playback_player.app_core, "find_offline_file_for_episode", lambda *a, **k: str(bad_local_ep2))
    monkeypatch.setattr(playback_player.app_core, "fetch_episode_stream", fake_fetch)
    monkeypatch.setattr(playback_player.app_core, "touch_history", lambda *a, **k: None)
    monkeypatch.setattr(playback_player.app_core, "save_resume_time", lambda *a, **k: None)
    monkeypatch.setattr(playback_player.app_core, "set_action_feedback", lambda *a, **k: None)

    args = argparse.Namespace(
        download=False,
        print_url=False,
        sources=False,
        binge=False,
        aniskip=False,
        auto_skip=False,
        quality=None,
        extra_args=[],
    )

    state = playback_player.handle_play_state(
        flags,
        ui,
        ms,
        {"download_dir": str(tmp_path)},
        args,
        "sub",
        "best",
        lambda *a, **k: False,
    )

    assert state == "DETAILS"
    assert captured["online_fetch_called"] is True
    assert captured["prefetched"][0]["link"] == "https://example.test/ep2.m3u8"


def test_provider_details_menu_keeps_playback_settings_last(monkeypatch):
    from media_cli.app import playback_menu

    flags = CliFlags()
    ui = UiState()
    ui.action_prev_state = "DETAILS"
    show = {
        "_id": "provider-show",
        "_provider": "anikoto",
        "name": "Provider Show",
        "_episode_ids": ["1", "2"],
        "_episode_labels": {"1": "1", "2": "2"},
        "availableEpisodes": {"sub": 2},
    }
    ui.ui_show_ctx = show
    ms = MachineState(show_title="Provider Show")
    ms.current_ep = "1"
    ms.current_ep_index = 0
    ms.total_eps = 2
    captured_opts = []

    def fake_pick(flags, ui, title, opts, **kwargs):
        nonlocal captured_opts
        captured_opts = list(opts)
        return -2

    monkeypatch.setattr(playback_menu, "tui_pick", fake_pick)
    monkeypatch.setattr(playback_menu.app_core, "ensure_episode_ids", lambda *a, **k: ["1", "2"])
    monkeypatch.setattr(playback_menu.app_core, "get_show_anilist_id", lambda *a, **k: None)
    monkeypatch.setattr(playback_menu.app_core, "get_resume_time", lambda *a, **k: 0)
    monkeypatch.setattr(playback_menu.app_core, "get_local_progress", lambda *a, **k: None)
    monkeypatch.setattr(playback_menu.app_core, "build_info_panel", lambda *a, **k: None)

    state = playback_menu.handle_action_menu_state(
        flags,
        ui,
        ms,
        {},
        argparse.Namespace(),
        "sub",
        lambda *a, **k: False,
    )

    assert state == "DETAILS"
    assert captured_opts[-1] == "Playback"
    assert "Download" in captured_opts
    assert not any("Auto-Download" in opt for opt in captured_opts)
    assert not any("Auto-Delete" in opt for opt in captured_opts)


def test_playback_settings_keeps_header_and_stays_open_after_toggle(monkeypatch):
    from media_cli.app import playback_menu

    flags = CliFlags()
    ui = UiState()
    show = {
        "_id": "download-show",
        "name": "Download Show",
        "_episode_ids": ["1", "2"],
        "_episode_ids_ttype": "sub",
        "_episode_labels": {"1": "Episode 1", "2": "Episode 2"},
        "availableEpisodes": {"sub": 2},
    }
    ui.ui_show_ctx = show
    ui.action_prev_state = "DETAILS"
    ms = MachineState(show_title="Download Show")
    ms._is_downloads = True
    ms.current_ep = "1"
    ms.current_ep_index = 0
    ms.total_eps = 2
    ms.auto_download_next = False
    ms.auto_delete_watched = False

    playback_calls = []
    playback_headers = []
    playback_initial_selected = []

    def fake_pick(flags, ui, title, opts, **kwargs):
        if title == "Select action":
            return opts.index("Playback")
        if title == "Playback":
            playback_calls.append(list(opts))
            playback_headers.append(kwargs.get("header_fn"))
            playback_initial_selected.append(kwargs.get("initial_selected"))
            if len(playback_calls) == 1:
                return next(i for i, opt in enumerate(opts) if "Auto-Download" in opt)
            return -2
        return -2

    monkeypatch.setattr(playback_menu, "tui_pick", fake_pick)
    monkeypatch.setattr(playback_menu.app_core, "ensure_episode_ids", lambda *a, **k: ["1", "2"])
    monkeypatch.setattr(playback_menu.app_core, "get_show_anilist_id", lambda *a, **k: None)
    monkeypatch.setattr(playback_menu.app_core, "get_resume_time", lambda *a, **k: 0)
    monkeypatch.setattr(playback_menu.app_core, "get_local_progress", lambda *a, **k: None)
    monkeypatch.setattr(playback_menu.app_core, "build_info_panel", lambda *a, **k: None)

    state = playback_menu.handle_action_menu_state(
        flags,
        ui,
        ms,
        {},
        argparse.Namespace(),
        "sub",
        lambda *a, **k: False,
    )

    assert state == "DETAILS"
    assert ms.auto_download_next is True
    assert len(playback_calls) == 2
    assert callable(playback_headers[0])
    assert any("Auto-Download Next: OFF" in opt for opt in playback_calls[0])
    assert any("Auto-Download Next: ON" in opt for opt in playback_calls[1])
    assert playback_initial_selected == [0, 0]


def test_playback_settings_preserves_selected_row_after_toggle(monkeypatch):
    from media_cli.app import playback_menu

    flags = CliFlags()
    ui = UiState()
    show = {
        "_id": "download-show",
        "name": "Download Show",
        "_episode_ids": ["1", "2"],
        "_episode_ids_ttype": "sub",
        "_episode_labels": {"1": "Episode 1", "2": "Episode 2"},
        "availableEpisodes": {"sub": 2},
    }
    ui.ui_show_ctx = show
    ms = MachineState(show_title="Download Show")
    ms._is_downloads = True
    ms.current_ep = "1"
    ms.current_ep_index = 0
    ms.total_eps = 2

    playback_initial_selected = []

    def fake_pick(flags, ui, title, opts, **kwargs):
        if title == "Select action":
            return opts.index("Playback")
        if title == "Playback":
            playback_initial_selected.append(kwargs.get("initial_selected"))
            if len(playback_initial_selected) == 1:
                return next(i for i, opt in enumerate(opts) if "Auto-Delete Buffer" in opt)
            return -2
        return -2

    monkeypatch.setattr(playback_menu, "tui_pick", fake_pick)
    monkeypatch.setattr(playback_menu.app_core, "ensure_episode_ids", lambda *a, **k: ["1", "2"])
    monkeypatch.setattr(playback_menu.app_core, "get_show_anilist_id", lambda *a, **k: None)
    monkeypatch.setattr(playback_menu.app_core, "get_resume_time", lambda *a, **k: 0)
    monkeypatch.setattr(playback_menu.app_core, "get_local_progress", lambda *a, **k: None)
    monkeypatch.setattr(playback_menu.app_core, "build_info_panel", lambda *a, **k: None)

    state = playback_menu.handle_action_menu_state(
        flags,
        ui,
        ms,
        {},
        argparse.Namespace(),
        "sub",
        lambda *a, **k: False,
    )

    assert state == "DETAILS"
    assert playback_initial_selected == [0, 2]


def test_skip_settings_status_rows_are_actionable(monkeypatch):
    from media_cli.app import playback_menu

    flags = CliFlags()
    ui = UiState()
    show = {
        "_id": "show-1",
        "name": "Demo",
        "_episode_ids": ["1", "2"],
        "_episode_ids_ttype": "sub",
        "_episode_labels": {"1": "Episode 1", "2": "Episode 2"},
        "availableEpisodes": {"sub": 2},
    }
    ui.ui_show_ctx = show
    ms = MachineState(show_title="Demo")
    ms.current_ep = "1"
    ms.current_ep_index = 0
    ms.total_eps = 2
    ms.custom_skip_sessions = {
        "show-1": [
            {
                "providers": ["all"],
                "exclude_providers": [],
                "intervals": [{"type": "op", "label": "Opening", "start": 0.0, "end": 90.0}],
            }
        ]
    }
    args = argparse.Namespace(aniskip=True, auto_skip=True)
    playback_calls = []
    skip_done = False

    def fake_pick(flags, ui, title, opts, **kwargs):
        nonlocal skip_done
        if title == "Select action":
            return opts.index("Playback")
        if title == "Playback":
            if skip_done:
                return -2
            return opts.index("Skip Settings")
        if title == "Skip Settings":
            playback_calls.append(list(opts))
            if len(playback_calls) == 1:
                return next(i for i, opt in enumerate(opts) if opt.startswith("AniSkip:"))
            if len(playback_calls) == 2:
                return next(i for i, opt in enumerate(opts) if opt.startswith("Auto-Skip:"))
            if len(playback_calls) == 3:
                return next(i for i, opt in enumerate(opts) if opt.startswith("Custom Skips:"))
            skip_done = True
            return -2
        return -2

    monkeypatch.setattr(playback_menu, "tui_pick", fake_pick)
    monkeypatch.setattr(playback_menu.app_core, "ensure_episode_ids", lambda *a, **k: ["1", "2"])
    monkeypatch.setattr(playback_menu.app_core, "get_show_anilist_id", lambda *a, **k: None)
    monkeypatch.setattr(playback_menu.app_core, "get_resume_time", lambda *a, **k: 0)
    monkeypatch.setattr(playback_menu.app_core, "get_local_progress", lambda *a, **k: None)
    monkeypatch.setattr(playback_menu.app_core, "build_info_panel", lambda *a, **k: None)
    monkeypatch.setattr(playback_menu.app_core, "get_custom_skips", lambda *a, **k: [])

    state = playback_menu.handle_action_menu_state(
        flags,
        ui,
        ms,
        {},
        args,
        "sub",
        lambda *a, **k: False,
    )

    assert state == "DETAILS"
    assert args.aniskip is False
    assert args.auto_skip is False
    assert "show-1" in ms.custom_skip_disabled_titles
    assert any("AniSkip: OFF" in opt for opt in playback_calls[-1])
    assert any("Auto-Skip: OFF" in opt for opt in playback_calls[-1])
    assert any("Custom Skips: OFF" in opt for opt in playback_calls[-1])


def test_episode_menu_uses_loading_catalog_entrypoint(monkeypatch):
    from media_cli.app import playback_episodes

    flags = CliFlags()
    ui = UiState()
    show = {
        "_id": "show-1",
        "_provider": "animexin",
        "name": "Demo",
        "availableEpisodes": {"sub": 2},
    }
    ui.ui_show_ctx = show
    ui.ep_prev_state = "DETAILS"
    ms = MachineState(show_title="Demo")
    ms.current_ep = "1"
    ms.current_ep_index = 0
    ms.total_eps = 2
    called = {}

    def fake_load_episode_ids(show_arg, ttype_arg):
        called["show"] = show_arg
        called["ttype"] = ttype_arg
        show_arg["_episode_ids_ttype"] = ttype_arg
        show_arg["_episode_ids"] = ["1", "2"]
        show_arg["_episode_labels"] = {"1": "1", "2": "2"}
        return ["1", "2"]

    def fail_direct_ensure(*args, **kwargs):
        raise AssertionError("episode menu should use the loading-aware catalog entrypoint")

    loading_messages = []

    def fake_with_loading(message, fn, *args, **kwargs):
        loading_messages.append(message)
        return fn(*args, **kwargs)

    monkeypatch.setattr(playback_episodes.app_core, "load_episode_ids_for_selection", fake_load_episode_ids)
    monkeypatch.setattr(playback_episodes.app_core, "with_loading", fake_with_loading)
    monkeypatch.setattr(playback_episodes.app_core, "ensure_episode_ids", fail_direct_ensure)
    monkeypatch.setattr(playback_episodes.app_core, "build_info_panel", lambda *a, **k: None)
    monkeypatch.setattr(playback_episodes, "tui_pick", lambda *a, **k: -2)

    state = playback_episodes.handle_episode_state(
        flags,
        ui,
        ms,
        {},
        argparse.Namespace(download=False),
        "sub",
        lambda *a, **k: False,
    )

    assert state == "DETAILS"
    assert called == {"show": show, "ttype": "sub"}
    assert loading_messages == ["Indexing episodes…"]


def test_details_entry_wraps_episode_catalog_loading(monkeypatch):
    from media_cli.app import details
    from media_cli.app import playback_menu

    flags = CliFlags()
    ui = UiState()
    show = {
        "_id": "show-1",
        "_provider": "animexin",
        "_has_provider_link": True,
        "name": "Demo",
        "description": "Known",
        "genres": ["Action"],
        "availableEpisodes": {"sub": 2},
    }
    ui.ui_show_ctx = show
    ui.ui_ttype_ctx = "sub"
    ui.search_prev_state = "SEARCH"
    ms = MachineState(show_title="Demo")
    ms.just_picked_anime = True
    args = argparse.Namespace(sync=False, no_sync=True)
    loading_messages = []

    def fake_with_loading(message, fn, *args, **kwargs):
        loading_messages.append(message)
        return fn(*args, **kwargs)

    monkeypatch.setattr(details.app_core, "with_loading", fake_with_loading)
    monkeypatch.setattr(details.app_core, "refresh_history_entry_provider_catalog", lambda entry: None)
    monkeypatch.setattr(details.app_core, "ensure_episode_ids", lambda *a, **k: ["1", "2"])
    monkeypatch.setattr(details.app_core, "patch_history_entry_show", lambda *a, **k: None)
    monkeypatch.setattr(details.app_core, "get_show_anilist_id", lambda *a, **k: None)
    monkeypatch.setattr(playback_menu, "tui_pick", lambda *a, **k: -2)

    state = details.handle_details_state(
        flags,
        ui,
        ms,
        {},
        args,
        "sub",
        lambda *a, **k: False,
    )

    assert state == "SEARCH"
    assert "Loading episodes…" in loading_messages


def test_history_selection_loads_episode_catalog_inside_picker(monkeypatch):
    from media_cli.app import search

    flags = CliFlags()
    flags.plain_mode = True
    ui = UiState()
    ms = MachineState()
    args = argparse.Namespace()
    show = {
        "_id": "show-1",
        "_provider": "animexin",
        "name": "Demo",
        "availableEpisodes": {"sub": 2},
    }
    hist = [{"show": show, "translation_type": "sub", "episode": "1"}]
    select_messages = []
    selected = []

    def fake_tui_pick(*args, **kwargs):
        select_messages.append(kwargs["select_loading_message_fn"](0))
        assert kwargs["select_fn"](0) is True
        return 0

    monkeypatch.setattr(search, "tui_pick", fake_tui_pick)
    monkeypatch.setattr(search.app_core, "load_history", lambda: hist)
    monkeypatch.setattr(search.app_core, "batch_prepare_shows_display_state", lambda *a, **k: None)
    monkeypatch.setattr(search.app_core, "filter_history_entries", lambda entries, mode: list(entries))
    monkeypatch.setattr(search.app_core, "format_history_entry", lambda entry: "Demo")
    monkeypatch.setattr(search.app_core, "prepare_show_display_state", lambda *a, **k: None)
    monkeypatch.setattr(search.app_core, "playback_ep_from_history_entry", lambda *a, **k: "1")

    def fake_ensure(show_arg, ttype_arg):
        selected.append((show_arg, ttype_arg))
        return ["1", "2"]

    monkeypatch.setattr(search.app_core, "ensure_episode_ids", fake_ensure)
    monkeypatch.setattr(search.app_core, "with_loading", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("history selection loading should be owned by the picker")
    ))

    state = search.handle_history_state(
        flags,
        ui,
        ms,
        {},
        args,
        "sub",
        lambda *a, **k: False,
    )

    assert state == "DETAILS"
    assert select_messages == ["Checking episode list…"]
    assert selected == [(show, "sub")]


def test_large_episode_menu_wraps_preparation_in_loading(monkeypatch):
    from media_cli.app import playback_episodes

    flags = CliFlags()
    ui = UiState()
    episode_ids = [str(i) for i in range(1, 121)]
    show = {
        "_id": "show-1",
        "_provider": "animexin",
        "name": "Demo",
        "_episode_ids": episode_ids,
        "_episode_ids_ttype": "sub",
        "_episode_labels": {ep: f"Episode {ep}" for ep in episode_ids},
        "availableEpisodes": {"sub": len(episode_ids)},
    }
    ui.ui_show_ctx = show
    ui.ep_prev_state = "DETAILS"
    ms = MachineState(show_title="Demo")
    ms.current_ep = "1"
    ms.current_ep_index = 0
    ms.total_eps = len(episode_ids)
    loading_messages = []

    def fake_with_loading(message, fn, *args, **kwargs):
        loading_messages.append(message)
        return fn(*args, **kwargs)

    monkeypatch.setattr(playback_episodes.app_core, "load_episode_ids_for_selection", lambda *a, **k: episode_ids)
    monkeypatch.setattr(playback_episodes.app_core, "with_loading", fake_with_loading)
    monkeypatch.setattr(playback_episodes.app_core, "build_info_panel", lambda *a, **k: None)
    monkeypatch.setattr(
        playback_episodes.app_core,
        "find_offline_file_for_episode",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("episode menu should not scan the download folder once per row")
        ),
    )
    monkeypatch.setattr(playback_episodes, "tui_pick", lambda *a, **k: -2)

    state = playback_episodes.handle_episode_state(
        flags,
        ui,
        ms,
        {},
        argparse.Namespace(download=False),
        "sub",
        lambda *a, **k: False,
    )

    assert state == "DETAILS"
    assert loading_messages[0] == "Indexing episodes…"
    assert loading_messages[1] == "Indexing 120 episodes…"
    assert loading_messages[2] == "Checking local files…"
    assert "Preparing episodes…" not in loading_messages
    assert "Checking downloaded episodes…" not in loading_messages


def test_episode_picker_prefixes_plain_numeric_labels_only():
    from media_cli.app.playback_episodes import _episode_picker_label

    assert _episode_picker_label("1") == "Episode 1"
    assert _episode_picker_label("18 [194] [4K]") == "Episode 18 [194] [4K]"
    assert _episode_picker_label("Episode 7") == "Episode 7"
    assert _episode_picker_label("1. Deep Blue") == "1. Deep Blue"
    assert _episode_picker_label("OVA") == "OVA"


def test_manual_provider_match_header_uses_live_results(monkeypatch):
    from media_cli.domain import matching

    flags = CliFlags()
    ui = UiState()
    results = []
    rendered_headers = []

    def fake_live_fn(query=""):
        if not results:
            results.append(
                {
                    "_id": "show-1",
                    "_provider": "anikoto",
                    "name": "Live Provider Result",
                    "availableEpisodes": {"sub": 1},
                }
            )
        return ["Live Provider Result"], "", True

    def fake_make_search(query, ttype, provider_id):
        return fake_live_fn, lambda: results, lambda: "", lambda: ""

    def fake_tui_pick(flags, ui, title, options, **kwargs):
        if kwargs.get("return_query_on_enter"):
            return "live query"
        live_fn = kwargs["live_fn"]
        header_fn = kwargs["header_fn"]
        live_fn("")
        rendered_headers.append(header_fn(0))
        return -2

    monkeypatch.setattr("media_cli.app.search_coordinator.make_provider_oneshot_search", fake_make_search)
    monkeypatch.setattr("media_cli.ui.picker.tui_pick", fake_tui_pick)
    monkeypatch.setattr(matching, "extract_franchise_query", lambda title: title)

    matching._run_manual_match_search(
        flags,
        ui,
        {"_id": "al-1", "name": "AniList Show"},
        "sub",
        provider_id="anikoto",
        allow_provider_change=False,
    )

    assert rendered_headers
    assert "Live Provider Result" in rendered_headers[0]
