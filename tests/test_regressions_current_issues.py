import argparse

from media_cli.context import CliFlags, MachineState, UiState


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
