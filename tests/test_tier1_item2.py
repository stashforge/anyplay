import os
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest

from allmanga_cli.domain.history import (
    history_entry_category,
    refresh_history_entry_provider_catalog,
)
from allmanga_cli.core import storage
from allmanga_cli.state import paths
from allmanga_cli.domain.metadata import (
    format_next_airing,
    apply_provider_metadata_to_history_show,
)
from allmanga_cli.providers.shared.wordpress import (
    parse_series,
    WordPressAnimeProvider,
)
from allmanga_cli.core.terminal import display_width as _display_width


def test_history_caught_up_to_active_transition():
    """Show caught up in Up to date moves to Active with ◆ when new ep drops."""
    entry = {
        "show": {
            "_id": "show-1",
            "name": "Test Anime",
            "status": "RELEASING",
            "availableEpisodes": {"sub": 12},
        },
        "episode": "12",
        "translation_type": "sub",
    }

    # 1. User watched 12/12 -> Up to date, was_caught_up=True, has_new_release=False
    cat = history_entry_category(entry)
    assert cat == "Up to date"
    assert entry.get("was_caught_up") is True
    assert entry.get("has_new_release") is False

    # 2. Episode 13 drops -> availableEpisodes becomes 13
    entry["show"]["availableEpisodes"]["sub"] = 13
    cat = history_entry_category(entry)
    assert cat == "Active"
    assert entry.get("has_new_release") is True

    # 3. Check prefix formatting
    prefix = "\033[38;2;166;227;161m◆\033[0m " if entry.get("has_new_release") else "  "
    assert "◆" in prefix
    assert _display_width(prefix) == 2


def test_history_backlogged_show_does_not_get_indicator():
    """Show backlogged in Active (watched 5/12) does NOT get ◆ when ep 13 drops."""
    entry = {
        "show": {
            "_id": "show-2",
            "name": "Backlogged Anime",
            "status": "RELEASING",
            "availableEpisodes": {"sub": 12},
        },
        "episode": "5",
        "translation_type": "sub",
    }

    # Initial check -> was_caught_up is not True
    cat = history_entry_category(entry)
    assert cat == "Active"
    assert not entry.get("was_caught_up")
    assert not entry.get("has_new_release")

    # Episode 13 drops
    entry["show"]["availableEpisodes"]["sub"] = 13
    cat = history_entry_category(entry)
    assert cat == "Active"
    assert not entry.get("has_new_release")

    prefix = "\033[38;2;166;227;161m◆\033[0m " if entry.get("has_new_release") else ""
    assert prefix == ""
    assert _display_width(prefix) == 0


def test_history_watched_clears_indicator(monkeypatch):
    """Watching the latest episode clears has_new_release and moves back to Up to date."""
    with tempfile.TemporaryDirectory() as tmpdir:
        hist_path = os.path.join(tmpdir, "history.json")
        monkeypatch.setattr(paths, "HISTORY_PATH", hist_path)
        monkeypatch.setattr(paths, "STATE_DIR", tmpdir)
        monkeypatch.setattr(storage, "_history_cache", None)

        show = {
            "_id": "show-3",
            "name": "Catchup Anime",
            "status": "RELEASING",
            "availableEpisodes": {"sub": 12},
        }

        # Progress at 12
        entry1 = storage.write_history_progress(show, "12", "sub")
        assert entry1.get("was_caught_up") is True
        assert entry1.get("has_new_release") is False

        # New episode 13 releases
        show["availableEpisodes"]["sub"] = 13
        cat = history_entry_category(entry1)
        assert cat == "Active"
        assert entry1.get("has_new_release") is True

        # User watches episode 13
        entry2 = storage.write_history_progress(show, "13", "sub")
        assert entry2.get("was_caught_up") is True
        assert entry2.get("has_new_release") is False
        assert history_entry_category(entry2) == "Up to date"


def test_format_next_airing_clears_when_episode_is_available():
    """format_next_airing returns empty string when availableEpisodes >= _next_airing_ep."""
    now = time.time()
    anime = {
        "_next_airing_ep": 25,
        "_next_airing_at": int(now + 86400),  # 1 day in the future
        "availableEpisodes": {"sub": 25},     # Released early!
    }
    # Even though remaining > 0, ep 25 is available -> return ""
    result = format_next_airing(anime, now=now)
    assert result == ""

    # If only ep 24 is available, countdown should be displayed
    anime["availableEpisodes"]["sub"] = 24
    result = format_next_airing(anime, now=now)
    assert "Next EP 25 in" in result


def test_apply_provider_metadata_clears_stale_airing_fields():
    """apply_provider_metadata_to_history_show clears _next_airing_* when show is up to date or provider cleared it."""
    show = {
        "_id": "show-4",
        "_next_airing_ep": 25,
        "_next_airing_at": 1780000000,
        "_next_airing_time": 1780000000,
        "availableEpisodes": {"sub": 24},
    }

    provider_show = {
        "_next_airing_ep": None,
        "_next_airing_at": None,
        "_next_airing_time": None,
        "availableEpisodes": {"sub": 25},
    }

    changed = apply_provider_metadata_to_history_show(show, provider_show)
    assert changed is True
    assert show.get("_next_airing_ep") is None
    assert show.get("_next_airing_at") is None
    assert show.get("_next_airing_time") is None
    assert show.get("availableEpisodes", {}).get("sub") == 25


def test_wordpress_parse_series_includes_early_released_episode():
    """parse_series does not drop an episode > latest_released_num if it has video mirrors."""
    series_html = """
    <div class="lastend">
        <span class="epcurlast">Episode 24</span>
    </div>
    <div class="eplister">
        <ul>
            <li>
                <a href="https://example.com/ep-25/">
                    <div class="epl-num">Episode 25</div>
                    <div class="epl-title">Episode 25</div>
                </a>
            </li>
            <li>
                <a href="https://example.com/ep-24/">
                    <div class="epl-num">Episode 24</div>
                    <div class="epl-title">Episode 24</div>
                </a>
            </li>
        </ul>
    </div>
    """

    def mock_fetch(url):
        if "ep-25" in url:
            # Episode 25 has player mirrors (early release)
            return """
            <html>
                <select class="mirror">
                    <option value="https://stream.example.com/video.m3u8">Server 1</option>
                </select>
            </html>
            """
        return ""

    entries = parse_series("https://example.com", series_html, fetch_fn=mock_fetch)
    # Episode 25 must be included!
    assert len(entries) == 2
    urls = [e.url for e in entries]
    assert "https://example.com/ep-25/" in urls


def test_wordpress_parse_series_drops_unreleased_countdown():
    """parse_series drops upcoming episode if it only has a tickcounter countdown."""
    series_html = """
    <div class="lastend">
        <span class="epcurlast">Episode 24</span>
    </div>
    <div class="eplister">
        <ul>
            <li>
                <a href="https://example.com/ep-25/">
                    <div class="epl-num">Episode 25</div>
                    <div class="epl-title">Episode 25</div>
                </a>
            </li>
            <li>
                <a href="https://example.com/ep-24/">
                    <div class="epl-num">Episode 24</div>
                    <div class="epl-title">Episode 24</div>
                </a>
            </li>
        </ul>
    </div>
    """

    def mock_fetch(url):
        if "ep-25" in url:
            # Episode 25 is unreleased countdown (no mirrors, only tickcounter)
            return """
            <html>
                <a class="tickcounter" data-id="12345">Countdown</a>
            </html>
            """
        return ""

    entries = parse_series("https://example.com", series_html, fetch_fn=mock_fetch)
    assert len(entries) == 1
    assert entries[0].url == "https://example.com/ep-24/"


def test_poster_footer_line_when_cover_ready():
    import hashlib
    from allmanga_cli.ui.poster import PosterManager

    pm = PosterManager(
        enabled=lambda: True,
        loading_frame=lambda: "\u28b9",
        cache_dir=lambda: "/tmp",
        read_cache_dirs=lambda: [],
        hovered_show_id=lambda: "1",
        request_redraw=lambda: None,
    )
    show = {
        "_id": "1",
        "name": "Slime Boukenki",
        "image": "https://example.com/cover.jpg",
        "_poster_status": "ready",
        "_poster_raw": "\033[38;2;255;0;0m#\033[0m",
    }
    default_text = "Miruro • \"slime\" • INCOGNITO • Enter=select"
    line = pm.footer_line(show, default_text, width=80)
    assert "Loading cover" not in line
    assert "Miruro" in line


def test_poster_footer_line_preserves_navigation_during_loading():
    import time
    from allmanga_cli.ui.poster import PosterManager

    pm = PosterManager(
        enabled=lambda: True,
        loading_frame=lambda: "\u28b9",
        cache_dir=lambda: "/tmp",
        read_cache_dirs=lambda: [],
        hovered_show_id=lambda: "1",
        request_redraw=lambda: None,
    )
    show = {
        "_id": "1",
        "name": "Slime Boukenki",
        "image": "https://example.com/cover.jpg",
        "_poster_status": "loading",
        "_poster_status_time": time.time(),
    }
    default_text = "Miruro • \"slime\" • INCOGNITO • Enter=select"
    line = pm.footer_line(show, default_text, width=80)
    assert "Loading cover" in line
    assert "│" in line
    assert "Miruro" in line


def test_poster_footer_line_clears_loading_if_raw_exists_even_if_status_lagged():
    from allmanga_cli.ui.poster import PosterManager

    pm = PosterManager(
        enabled=lambda: True,
        loading_frame=lambda: "\u28b9",
        cache_dir=lambda: "/tmp",
        read_cache_dirs=lambda: [],
        hovered_show_id=lambda: "1",
        request_redraw=lambda: None,
    )
    show = {
        "_id": "1",
        "name": "Slime Boukenki",
        "image": "https://example.com/cover.jpg",
        "_poster_status": "loading",
        "_poster_raw": "CHAFARAWCONTENT",
    }
    default_text = "Miruro • \"slime\" • INCOGNITO • Enter=select"
    line = pm.footer_line(show, default_text, width=80)
    assert "Loading cover" not in line
    assert "Miruro" in line


def test_poster_footer_line_clears_loading_if_cached_in_raw_cache():
    import hashlib
    from allmanga_cli.ui.poster import PosterManager

    pm = PosterManager(
        enabled=lambda: True,
        loading_frame=lambda: "\u28b9",
        cache_dir=lambda: "/tmp",
        read_cache_dirs=lambda: [],
        hovered_show_id=lambda: "1",
        request_redraw=lambda: None,
    )
    url = "https://example.com/cover.jpg"
    url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    pm._raw_cache[url_hash] = "RENDERED_ANSI"

    show = {
        "_id": "1",
        "name": "Slime Boukenki",
        "image": url,
        "_poster_status": "loading",
    }
    default_text = "Miruro • \"slime\" • INCOGNITO • Enter=select"
    line = pm.footer_line(show, default_text, width=80)
    assert "Loading cover" not in line
    assert "Miruro" in line


def test_cached_header_fn_clears_enriching_metadata():
    from allmanga_cli.ui.panels import render_search_header

    shows = [{
        "_id": "1",
        "name": "Genjitsu no Rimuru",
        "title": "Genjitsu no Rimuru: SUNSHINE in the SLIME",
        "format": "ONA",
        "status": "FINISHED",
    }]
    loading_state = ["Enriching metadata…"]

    # 1. Header with active loading message
    hdr_loading = render_search_header(
        "Miruro",
        "slime",
        "sub",
        lambda: shows,
        lambda: loading_state[0],
        selected_idx=0,
        badges=["INCOGNITO"],
    )
    assert "Enriching metadata…" in hdr_loading

    # 2. When enrichment finishes, get_loading returns ""
    loading_state[0] = ""
    hdr_done = render_search_header(
        "Miruro",
        "slime",
        "sub",
        lambda: shows,
        lambda: loading_state[0],
        selected_idx=0,
        badges=["INCOGNITO"],
    )
    assert "Enriching metadata…" not in hdr_done
    assert "Miruro" in hdr_done


def test_search_header_shows_loading_screen_when_results_empty():
    from allmanga_cli.ui.panels import render_search_header

    # When search is initiated, shows list is empty ([]), selected_idx is -1
    shows = []
    loading_msg = "⣯ Searching…"
    hdr = render_search_header(
        "Miruro",
        "slime",
        "sub",
        lambda: shows,
        lambda: loading_msg,
        selected_idx=-1,
    )
    assert "Use Up/Down to browse previous searches." in hdr
    assert "Provider:" in hdr
    assert "Miruro" in hdr
    assert "⣯ Searching…" in hdr


def test_write_history_progress_url_episode_clears_has_new_release(monkeypatch):
    from allmanga_cli.core.storage import write_history_progress, load_history

    show = {
        "_id": "test-wp-show",
        "name": "WordPress Anime",
        "availableEpisodes": {"sub": 12},
    }
    # Episode ID is a URL
    ep_url = "https://example.com/anime-episode-12/"
    entry = write_history_progress(show, ep_url, "sub")
    assert entry is not None
    assert entry.get("was_caught_up") is True
    assert entry.get("has_new_release") is False


def test_history_provider_is_completed_checks_anilist_status():
    from allmanga_cli.domain.history import history_provider_is_completed, history_full_episode_count

    show_al_finished = {
        "_id": "test-show",
        "name": "Finished Anime",
        "_anilist_status": "FINISHED",
        "_anilist_episode_count": 24,
    }
    assert history_provider_is_completed(show_al_finished) is True
    entry = {"show": show_al_finished, "translation_type": "sub"}
    assert history_full_episode_count(entry) == 24

def test_app_core_download_episode_and_process_exports():
    import allmanga_cli.app_core as app_core
    from unittest.mock import patch, MagicMock

    assert hasattr(app_core, "download_episode")
    assert hasattr(app_core, "register_subprocess")
    assert hasattr(app_core, "unregister_subprocess")
    assert hasattr(app_core, "kill_active_subprocesses")

    mock_stream = {"link": "https://example.com/video.mp4", "headers": {}}
    with patch("shutil.which", return_value="/usr/bin/yt-dlp"), \
         patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.wait.return_value = None
        mock_popen.return_value = mock_proc

        ok = app_core.download_episode(
            "Test Show", "1", mock_stream, download_dir="/tmp/test_dl"
        )
        assert ok is True


def test_cursor_solid_during_movement():
    # Simulate the cursor blink logic:
    # 1. While moving (idle < 0.7s), cursor must always be (True, True) (solid visible)
    # 2. Idle > 0.7s transitions into natural 1Hz blink cycle
    def simulate_is_cursor_blink_on(now, last_activity, picker_start, query="slime"):
        active = bool(query) or (now - picker_start < 3.0) or (now - last_activity < 3.0)
        if not active:
            return False, False
        idle = now - last_activity
        if idle < 0.7:
            return True, True
        blink_on = (int((idle - 0.7) * 2.0) % 2 == 1)
        return True, blink_on

    t0 = 1000.0
    # User is moving cursor every 100ms
    for step in range(10):
        t = t0 + step * 0.1
        last_move = t  # moving right now
        active, blink = simulate_is_cursor_blink_on(t, last_move, t0)
        assert active is True
        assert blink is True, "Cursor must remain visible while moving"

    # User pauses for 0.4s after movement
    t_pause = t0 + 1.0 + 0.4
    active, blink = simulate_is_cursor_blink_on(t_pause, t0 + 1.0, t0)
    assert blink is True, "Cursor must stay solid for 0.7s after stopping"

    # At 0.8s idle, cursor enters blink OFF phase
    t_off = t0 + 1.0 + 0.8
    active, blink = simulate_is_cursor_blink_on(t_off, t0 + 1.0, t0)
    assert blink is False, "Cursor blinks off after initial 0.7s solid duration"

    # At 1.3s idle, cursor enters blink ON phase
    t_on = t0 + 1.0 + 1.3
    active, blink = simulate_is_cursor_blink_on(t_on, t0 + 1.0, t0)
    assert blink is True, "Cursor blinks back on during periodic cycle"


def test_download_episode_uses_local_proxy_for_cloudflare_stream():
    from unittest.mock import patch, MagicMock
    from allmanga_cli.media.download import download_episode

    mock_stream = {
        "link": "https://vault-16.owocdn.top/stream/123/uwu.m3u8",
        "referer": "https://kwik.cx/",
        "headers": {"User-Agent": "test-ua"},
        "requires_proxy": True,
    }

    mock_server = MagicMock()
    with patch("shutil.which", return_value="/usr/bin/yt-dlp"), \
         patch("allmanga_cli.media.local_proxy.start_local_proxy", return_value=("http://127.0.0.1:8080/stream.m3u8", mock_server)) as mock_start_proxy, \
         patch("allmanga_cli.media.local_proxy.stop_local_proxy") as mock_stop_proxy, \
         patch("subprocess.Popen") as mock_popen:

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.wait.return_value = None
        mock_popen.return_value = mock_proc

        ok = download_episode("Slime", "3", mock_stream, download_dir="/tmp/test_dl")
        assert ok is True

        # Verify proxy was started with upstream link and referer
        assert mock_start_proxy.called
        args, kwargs = mock_start_proxy.call_args
        assert args[0] == "https://vault-16.owocdn.top/stream/123/uwu.m3u8"
        assert args[1] == "https://kwik.cx/"

        # Verify yt-dlp was invoked with the 127.0.0.1 localhost URL
        popen_cmd = mock_popen.call_args[0][0]
        assert "http://127.0.0.1:8080/stream.m3u8" in popen_cmd

        # Verify proxy was stopped upon completion
        assert mock_stop_proxy.called
        assert mock_stop_proxy.call_args[0][0] == mock_server


def test_download_mode_restores_terminal_and_returns_to_details(monkeypatch):
    import argparse
    from unittest.mock import patch, MagicMock
    from allmanga_cli.context import CliFlags, UiState, MachineState
    from allmanga_cli.app.playback_player import handle_play_state

    flags = CliFlags()
    ui = UiState()
    ui.ui_show_ctx = {
        "_id": "slime-id",
        "name": "Slime S4",
        "availableEpisodes": {"sub": 24},
        "_provider": "miruro",
    }
    ms = MachineState()
    ms.show_id = "slime-id"
    ms.show_title = "Slime S4"
    ms.current_ep = "3"
    ms.current_ep_index = 2
    ms.total_eps = 24

    args = argparse.Namespace(
        download=True,
        print_url=False,
        downloader="auto",
        extra_args=[],
    )

    with patch("allmanga_cli.app_core.restore_terminal") as mock_restore_term, \
         patch("allmanga_cli.app_core.enter_alt_screen") as mock_enter_alt, \
         patch("allmanga_cli.app_core.ensure_episode_ids", return_value=["1", "2", "3", "4"]), \
         patch("allmanga_cli.app_core.get_episode_data", return_value={"episode": {"sourceUrls": []}}), \
         patch("allmanga_cli.core.streams.start_bg_resolve"), \
         patch("allmanga_cli.app_core._stream_snapshot", return_value=[{"source_name": "Test", "link": "http://test.m3u8"}]), \
         patch("allmanga_cli.app_core.download_episode", return_value=True), \
         patch("sys.stdin.isatty", return_value=True), \
         patch("time.sleep"):

        next_state = handle_play_state(
            flags, ui, ms, {}, args, "sub", "best", lambda *a: False
        )

        # In download mode, restore_terminal MUST be called to exit UI before resolution/download
        assert mock_restore_term.called
        # enter_alt_screen MUST NOT be called
        assert not mock_enter_alt.called
        # args.download should be reset to False
        assert args.download is False
        # And next state should return to DETAILS (the action menu)
        assert next_state == "DETAILS"


def test_is_online_detects_status_and_caches():
    from unittest.mock import patch
    import allmanga_cli.core.network as net

    net._online_cache = None
    net._cache_time = 0.0

    # 1. Successful connection -> True
    with patch("socket.create_connection", return_value=MagicMock()):
        status = net.force_check_online()
        assert status is True

    # 2. Within cache TTL, socket is NOT called again
    with patch("socket.create_connection", side_effect=OSError("Offline")):
        status = net.is_online(cache_ttl=10.0)
        assert status is True  # Cached result

    # 3. Force check offline -> False
    with patch("socket.create_connection", side_effect=OSError("Offline")):
        status = net.force_check_online()
        assert status is False


def test_delete_offline_episode_disk_and_db():
    import json
    from pathlib import Path
    from allmanga_cli.core.storage import delete_offline_episode, load_downloads_db

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_file = tmp_path / "downloads.db"
        shows_dir = tmp_path / "downloads"
        show_dir = shows_dir / "Test Show"
        show_dir.mkdir(parents=True, exist_ok=True)

        ep1_file = show_dir / "Test Show - Episode 1.mp4"
        ep2_file = show_dir / "Test Show - Episode 2.mp4"
        ep1_file.write_text("ep1 content")
        ep2_file.write_text("ep2 content")

        initial_db = {
            "shows": {
                "Test Show": {
                    "episodes": ["1", "2"],
                    "watched_episodes": ["1"],
                    "metadata": {"name": "Test Show", "downloadedCount": 2},
                }
            }
        }
        db_file.write_text(json.dumps(initial_db))

        with patch("allmanga_cli.state.paths.DOWNLOADS_DB_PATH", str(db_file)):
            cfg = {"download_dir": str(shows_dir)}
            # Delete EP 1
            ok = delete_offline_episode("Test Show", "1", cfg=cfg)
            assert ok is True
            assert not ep1_file.exists()
            assert ep2_file.exists()

            # Check DB updated
            db = load_downloads_db()
            show_data = db["shows"]["Test Show"]
            assert "1" not in show_data["episodes"]
            assert "2" in show_data["episodes"]
            assert "1" not in show_data.get("watched_episodes", [])
            assert show_data["metadata"]["downloadedCount"] == 1


def test_downloads_mode_defaults_to_first_downloaded_episode():
    from allmanga_cli.context import CliFlags, UiState, MachineState
    from allmanga_cli.app.details import handle_details_state

    flags = CliFlags()
    ui = UiState()
    ms = MachineState()

    # Show has 24 episodes in catalog, but only EP 3 is downloaded
    catalog_eids = [str(i) for i in range(1, 25)]
    labels = {str(i): str(i) for i in range(1, 25)}
    show = {
        "_id": "offline-slime",
        "name": "Slime S4",
        "_folder_name": "Slime S4",
        "_episode_ids": catalog_eids,
        "_episode_labels": labels,
        "availableEpisodes": {"sub": 24},
        "_downloaded_episodes": ["3"],
        "watched_episodes": [],
    }
    ui.ui_show_ctx = show
    ms.show_id = "offline-slime"
    ms.show_title = "Slime S4"
    ms._is_downloads = True
    ms._download_files = {"3": "/tmp/Slime S4/Slime S4 - Episode 3.mp4"}

    with patch("allmanga_cli.app_core.build_info_panel"), \
         patch("allmanga_cli.app.playback_menu.handle_action_menu_state", return_value="QUIT"):
        next_state = handle_details_state(
            flags, ui, ms, {}, MagicMock(), "sub", lambda *a: False
        )
        assert next_state == "QUIT"
        # Must have selected EP 3 (the downloaded episode), NOT EP 1!
        assert ms.current_ep == "3"
        assert ms.current_ep_index == 2


def test_playback_menu_session_toggles():
    from allmanga_cli.context import CliFlags, UiState, MachineState
    from allmanga_cli.app.playback_menu import handle_action_menu_state

    flags = CliFlags()
    ui = UiState()
    ms = MachineState()
    ms._is_downloads = True
    ms.auto_download_next = False
    ms.auto_delete_watched = False
    ms.auto_delete_buffer = 1

    show = {
        "_id": "offline-show",
        "name": "Test Anime",
        "_folder_name": "Test Anime",
        "_episode_ids": ["1", "2"],
        "_episode_labels": {"1": "1", "2": "2"},
        "availableEpisodes": {"sub": 2},
        "_downloaded_episodes": ["1"],
    }
    ui.ui_show_ctx = show

    # Simulate selecting TOGGLE_AUTO_DOWNLOAD in action menu
    captured_opts = []

    def mock_pick(flags, ui, title, opts, acts=None, **kwargs):
        nonlocal captured_opts
        captured_opts = list(opts)
        for i, o in enumerate(opts):
            if "Auto-Download" in o:
                return i
        return 0

    with patch("allmanga_cli.app.playback_menu.tui_pick", side_effect=mock_pick), \
         patch("allmanga_cli.app_core.build_info_panel"):
        next_state = handle_action_menu_state(flags, ui, ms, {}, MagicMock(), "sub", lambda *a: False)
        assert next_state == "DETAILS"
        assert ms.auto_download_next is True
        assert any("Auto-Download" in opt for opt in captured_opts)
        assert any("Auto-Delete" in opt for opt in captured_opts)


def test_playback_episodes_downloads_mode_options_and_hints():
    from allmanga_cli.context import CliFlags, UiState, MachineState
    from allmanga_cli.app.playback_episodes import handle_episode_state

    flags = CliFlags()
    ui = UiState()
    ui.ep_prev_state = "DETAILS"
    ms = MachineState()
    ms._is_downloads = True
    ms.show_title = "Slime S4"
    ms.show_id = "slime-s4"
    ms._download_files = {"3": "/path/to/ep3.mp4"}

    show = {
        "_id": "slime-s4",
        "name": "Slime S4",
        "_folder_name": "Slime S4",
        "_episode_ids": ["1", "2", "3"],
        "_episode_ids_ttype": "sub",
        "_episode_labels": {"1": "1", "2": "2", "3": "3"},
        "availableEpisodes": {"sub": 3},
        "_downloaded_episodes": ["3"],
    }
    ui.ui_show_ctx = show

    captured_opts = []
    captured_disabled = None
    captured_hints = None
    captured_header_fn = None
    captured_can_delete_fn = None
    captured_delete_prompt = None

    def mock_pick(flags, ui, title, opts, header_fn=None, disabled_indices=None, hints=None, can_delete_fn=None, delete_prompt=None, **kwargs):
        nonlocal captured_opts, captured_disabled, captured_hints, captured_header_fn, captured_can_delete_fn, captured_delete_prompt
        captured_opts = list(opts)
        captured_disabled = disabled_indices
        captured_hints = hints
        captured_header_fn = header_fn
        captured_can_delete_fn = can_delete_fn
        captured_delete_prompt = delete_prompt
        return -2  # Esc / back

    with patch("allmanga_cli.app.playback_episodes.tui_pick", side_effect=mock_pick), \
         patch("allmanga_cli.app_core.is_online", return_value=True), \
         patch("allmanga_cli.app_core.get_local_progress", return_value=None), \
         patch("allmanga_cli.app_core.build_info_panel"):
        next_state = handle_episode_state(flags, ui, ms, {}, MagicMock(), "sub", lambda *a: False)
        assert next_state == "DETAILS"

        # Check options formatting: EP 1 has no ✘ (clean dimmed), EP 3 has ✔
        assert "✘" not in captured_opts[0]
        assert "✔" in captured_opts[2]

        # Check disabled indices: must be EMPTY so user can select/hover un-downloaded episodes
        assert captured_disabled == set()

        # Check dynamic hints on highlighted items: concise & human-friendly
        assert callable(captured_hints)
        assert "✘" in captured_hints(0)
        assert "not downloaded" in captured_hints(0).lower()
        assert "Enter=Download" not in captured_hints(0)
        assert captured_hints(2) == "ready to watch"

        # Check footer in header_fn: shows Enter=Download for EP 0, Enter=Play • Del=Delete for EP 2
        assert callable(captured_header_fn)
        assert "Enter=Download" in captured_header_fn(0)
        assert "Enter=Play" in captured_header_fn(2)
        assert "Del=Delete" in captured_header_fn(2)

        # Check delete guards and prompt
        assert callable(captured_can_delete_fn)
        assert captured_can_delete_fn(0) is False  # EP 1 is not downloaded -> cannot delete
        assert captured_can_delete_fn(2) is True   # EP 3 is downloaded -> can delete
        assert callable(captured_delete_prompt)
        assert captured_delete_prompt(2) == "Delete downloaded EP 3? y/N"


def test_playback_player_rolling_deletion():
    from allmanga_cli.context import CliFlags, UiState, MachineState
    from allmanga_cli.app.playback_player import handle_play_state
    import argparse

    flags = CliFlags()
    ui = UiState()
    ms = MachineState()
    ms._is_downloads = True
    ms.show_title = "Slime S4"
    ms.show_id = "slime-s4"
    ms.current_ep = "2"
    ms.current_ep_index = 1
    ms.total_eps = 3
    ms.auto_delete_watched = True
    ms.auto_delete_buffer = 1
    ms._download_files = {
        "1": "/path/to/ep1.mp4",
        "2": "/path/to/ep2.mp4",
        "3": "/path/to/ep3.mp4",
    }

    show = {
        "_id": "slime-s4",
        "name": "Slime S4",
        "_folder_name": "Slime S4",
        "_episode_ids": ["1", "2", "3"],
        "_episode_ids_ttype": "sub",
        "_episode_labels": {"1": "1", "2": "2", "3": "3"},
        "availableEpisodes": {"sub": 3},
        "_downloaded_episodes": ["1", "2", "3"],
        "watched_episodes": ["1"],  # EP 1 was previously watched
    }
    ui.ui_show_ctx = show
    args = argparse.Namespace(
        download=False,
        print_url=False,
        downloader="auto",
        extra_args=[],
        binge=False,
    )

    # Mock MPV player playing EP 2 to EOF (100% watched)
    mock_player = MagicMock()
    mock_player.play.return_value = ("EOF", 100.0, 1400.0, 1400.0)

    deleted_eps = []
    def mock_delete(folder, ep, cfg=None):
        deleted_eps.append(str(ep))
        return True

    with patch("allmanga_cli.app_core.enter_alt_screen"), \
         patch("allmanga_cli.app_core._exit_player_screen"), \
         patch("allmanga_cli.app_core.play_desktop", return_value=("EOF", 100.0, 1400.0, 1400.0, 1400.0)), \
         patch("allmanga_cli.app.playback_player.playback_updates_history", return_value=True), \
         patch("allmanga_cli.app_core.delete_offline_episode", side_effect=mock_delete), \
         patch("allmanga_cli.core.storage.update_offline_watch_status", return_value=True):
        next_state = handle_play_state(
            flags, ui, ms, {}, args, "sub", "best", lambda *a: False
        )
        assert next_state == "DETAILS"
        # Since EP 1 and EP 2 are now watched, and buffer is 1:
        # Latest watched is EP 2 (retained in buffer).
        # EP 1 is older than buffer -> MUST be auto-deleted!
        assert "1" in deleted_eps
        assert "2" not in deleted_eps


def test_handle_downloads_state_runs_without_error():
    from allmanga_cli.context import CliFlags, UiState, MachineState
    from allmanga_cli.app.downloads import handle_downloads_state
    from pathlib import Path
    import json

    flags = CliFlags()
    ui = UiState()
    ms = MachineState()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        db_file = tmp_path / "downloads.db"
        shows_dir = tmp_path / "downloads"
        show_dir = shows_dir / "Slime S4"
        show_dir.mkdir(parents=True, exist_ok=True)
        (show_dir / "Slime S4 - Episode 3.mp4").write_text("dummy")

        db_content = {
            "shows": {
                "Slime S4": {
                    "episodes": ["3"],
                    "metadata": {"name": "Slime S4"},
                }
            }
        }
        db_file.write_text(json.dumps(db_content))

        with patch("allmanga_cli.state.paths.DOWNLOADS_DB_PATH", str(db_file)), \
             patch("allmanga_cli.app_core.is_online", return_value=False), \
             patch("allmanga_cli.app.downloads.tui_pick", return_value=-2):
            cfg = {"download_dir": str(shows_dir)}
            state = handle_downloads_state(flags, ui, ms, cfg, MagicMock(), "sub", lambda *a: False)
            assert state == "QUIT"


def test_plain_picker_delete_guard():
    from allmanga_cli.ui.plain_picker import plain_pick
    from allmanga_cli.context import CliFlags, UiState

    flags = CliFlags(plain_mode=True)
    ui = UiState()
    deleted = []

    def mock_delete(idx):
        deleted.append(idx)
        return ["EP 1", "EP 2"], "header"

    inputs = iter(["del 1", "b"])
    output = []

    plain_pick(
        flags,
        ui,
        "Episodes",
        ["EP 1", "EP 2"],
        delete_fn=mock_delete,
        can_delete_fn=lambda idx: idx != 0,
        input_fn=lambda _: next(inputs),
        output_fn=output.append,
    )
    # Deletion was rejected by can_delete_fn
    assert len(deleted) == 0







