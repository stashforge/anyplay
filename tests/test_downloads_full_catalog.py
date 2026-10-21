import unittest
from unittest.mock import MagicMock, patch
import os
import tempfile

from media_cli.context import MachineState, UiState, CliFlags
from media_cli.app import downloads, playback_episodes, playback_menu
from media_cli.domain.episodes import build_progress_entries


class TestDownloadsFullCatalog(unittest.TestCase):
    def test_downloads_backfills_missing_episodes_offline(self):
        """When opening a download folder with episodes 30-41 and watched progress,
        it should synthesize 1-29 so that _episode_ids has 1-41."""
        with tempfile.TemporaryDirectory() as tmpdir:
            show_dir = os.path.join(tmpdir, "Beyond Times Gaze")
            os.makedirs(show_dir)
            # Create files for episodes 30 to 41
            for ep in range(30, 42):
                fpath = os.path.join(show_dir, f"Beyond Times Gaze - Episode {ep}.mp4")
                with open(fpath, "w") as f:
                    f.write("test")

            db_data = {
                "shows": {
                    "Beyond Times Gaze": {
                        "metadata": {
                            "_id": "beyond-times-gaze",
                            "name": "Beyond Times Gaze",
                            "episodeCount": 12,
                            "_local_progress": 31,
                            "_local_episode_label": "31",
                        },
                        "episodes": [str(i) for i in range(30, 42)],
                        "watched_episodes": [str(i) for i in range(1, 32)],
                    }
                }
            }

            ui = UiState()
            ms = MachineState()
            cfg = {"download_dir": tmpdir}
            flags = CliFlags()

            with patch("media_cli.app.downloads.load_downloads_db", return_value=db_data), \
                 patch("media_cli.app.downloads.save_downloads_db"), \
                 patch("media_cli.app.downloads.tui_pick", return_value=0), \
                 patch("media_cli.app_core.is_online", return_value=False), \
                 patch("media_cli.app_core.get_history_entry", return_value=None), \
                 patch("media_cli.core.storage.load_history", return_value=[]), \
                 patch("media_cli.app.downloads.set_ui_context") as mock_set_ctx:

                res = downloads.handle_downloads_state(flags, ui, ms, cfg, MagicMock(), "sub", MagicMock(return_value=False))

                self.assertEqual(res, "DETAILS")

                show = mock_set_ctx.call_args[0][1]
                eids = show.get("_episode_ids")
                self.assertIsNotNone(eids)
                self.assertEqual(len(eids), 41)
                self.assertEqual(eids[0], "1")
                self.assertEqual(eids[-1], "41")
                self.assertEqual(show["episodeCount"], 41)

                # Verify ms._download_files only maps the files that exist (30-41)
                self.assertIn("30", ms._download_files)
                self.assertIn("41", ms._download_files)
                self.assertNotIn("1", ms._download_files)
                self.assertNotIn("29", ms._download_files)

    def test_downloads_mode_ignores_playlist_text_disguised_as_video(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            show_dir = os.path.join(tmpdir, "Fake Show")
            os.makedirs(show_dir)
            fake_file = os.path.join(show_dir, "Fake Show - Episode 1.mp4")
            with open(fake_file, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=123\nseg.m3u8\n")

            db_data = {
                "current_download_dir": tmpdir,
                "shows": {
                    "Fake Show": {
                        "metadata": {"name": "Fake Show", "episodeCount": 1},
                        "episodes": ["1"],
                    }
                },
            }

            ui = UiState()
            ms = MachineState()
            cfg = {"download_dir": tmpdir}
            flags = CliFlags()

            with patch("media_cli.app.downloads.load_downloads_db", return_value=db_data), \
                 patch("media_cli.app.downloads.save_downloads_db") as mock_save, \
                 patch("media_cli.app.downloads.warn") as mock_warn, \
                 patch("media_cli.app_core.is_online", return_value=False), \
                 patch("media_cli.app_core.get_history_entry", return_value=None):
                res = downloads.handle_downloads_state(flags, ui, ms, cfg, MagicMock(), "sub", MagicMock(return_value=False))

            self.assertEqual(res, "QUIT")
            mock_warn.assert_called_once()
            self.assertEqual(db_data["shows"], {})
            mock_save.assert_called()

    def test_progress_picker_shows_full_range_for_downloads(self):
        """build_progress_entries on backfilled show should return 0..41 entries."""
        eids = [str(i) for i in range(1, 42)]
        labels = {str(i): f"Episode {i}" for i in range(1, 42)}
        show = {
            "name": "Beyond Times Gaze",
            "_episode_ids": eids,
            "_episode_labels": labels,
            "_local_progress": 31,
            "_local_episode_label": "31",
        }
        entries = build_progress_entries(show, eids, labels=labels, ttype="sub")
        # 41 episodes + 1 entry for 0 / Unwatched = 42 entries
        self.assertEqual(len(entries), 42)
        self.assertEqual(entries[0]["id"], "0")
        self.assertEqual(entries[1]["id"], "1")
        self.assertEqual(entries[-1]["id"], "41")

    def test_downloads_episode_picker_styling_and_hints(self):
        """Verify the exact user styling requirements in downloads episode picker:
        - Watched + Downloaded: greyed out with green checkmark
        - Watched + Not Downloaded: greyed out
        - Unwatched + Downloaded: normal text with green checkmark
        - Unwatched + Not Downloaded: normal text
        """
        flags = CliFlags()
        ui = UiState()
        ms = MachineState()
        ms._is_downloads = True
        ms.show_title = "Beyond Times Gaze"
        ms.total_eps = 41
        ms.current_ep_index = 0

        eids = [str(i) for i in range(1, 42)]
        labels = {str(i): f"Episode {i}" for i in range(1, 42)}
        show = {
            "name": "Beyond Times Gaze",
            "_episode_ids": eids,
            "_episode_labels": labels,
            "watched_episodes": [str(i) for i in range(1, 32)], # 1-31 watched
            "_local_progress": 31,
            "_local_episode_label": "31",
            "episodeCount": 41,
        }
        ui.ui_show_ctx = show
        ui.ep_prev_state = "DETAILS"
        cfg = {"episode_order": "asc"}

        captured_opts = []
        captured_hints_fn = None

        def fake_tui_pick(*args, **kwargs):
            nonlocal captured_opts, captured_hints_fn
            from media_cli.app.details_menu_controller import DetailsMenuResult
            from media_cli.state.theme import APPLICATION_THEME
            from media_cli.ui.row_text import render_row_text
            view = kwargs["read_view"]()
            captured_opts = [render_row_text(item.label, columns=120, theme=APPLICATION_THEME) for item in view.items]
            def captured_hints_fn(i):
                return view.items[i].hint_text
            return DetailsMenuResult("back")

        with tempfile.TemporaryDirectory() as tmpdir:
            ms._download_files = {}
            for i in range(30, 42):
                path = os.path.join(tmpdir, f"ep{i}.mp4")
                with open(path, "wb") as fh:
                    fh.write(b"video")
                ms._download_files[str(i)] = path

            with patch("media_cli.app_core.ensure_episode_ids", return_value=eids), \
                 patch("media_cli.app.playback_episodes.run_details_menu", side_effect=fake_tui_pick):

                res = playback_episodes.handle_episode_state(flags, ui, ms, cfg, MagicMock(), "sub", MagicMock(return_value=False))
                self.assertEqual(res, "DETAILS")

                # 1. Ep 1: Watched + Not Downloaded -> greyed out, hint "watched • not downloaded"
                ep1_opt = captured_opts[0]
                self.assertIn("\033[38;5;244mEpisode 1\033[0m", ep1_opt)
                self.assertNotIn("✔", ep1_opt)
                self.assertEqual(captured_hints_fn(0), "watched • not downloaded")

                # 2. Ep 30: Watched + Downloaded -> greyed out with green checkmark
                ep30_opt = captured_opts[29]
                self.assertIn("\033[38;5;244mEpisode 30\033[0m", ep30_opt)
                self.assertIn("\033[38;2;166;227;161m  ✔\033[0m", ep30_opt)
                self.assertEqual(captured_hints_fn(29), "watched • ready to watch")

                # 3. Ep 35: Unwatched + Downloaded -> normal text with green checkmark
                ep35_opt = captured_opts[34]
                self.assertIn("Episode 35", ep35_opt)
                self.assertIn("\033[38;2;166;227;161m  ✔\033[0m", ep35_opt)
                self.assertNotIn("\033[38;5;244mEpisode 35", ep35_opt)
                self.assertEqual(captured_hints_fn(34), "ready to watch")

    def test_playback_submenu_toggles_are_session_only(self):
        """Playback submenu toggles auto-download and auto-delete without saving config."""
        flags = CliFlags()
        ui = UiState()
        ui.ui_show_ctx = {
            "_id": "test-show",
            "name": "Test Show",
            "_episode_ids": ["1", "2"],
            "_episode_ids_ttype": "sub",
            "_episode_labels": {"1": "Episode 1", "2": "Episode 2"},
        }
        ui.action_prev_state = "DETAILS"
        ms = MachineState()
        ms._is_downloads = True
        ms.current_ep = "1"
        ms.current_ep_index = 0
        ms.total_eps = 2
        ms.auto_download_next = False
        ms.auto_delete_watched = False
        cfg = {"auto_download_next": False, "auto_delete_watched": False}

        playback_picks = 0

        def pick_action(fl, u, title, opts, **kwargs):
            nonlocal playback_picks
            if title == "Select action":
                self.assertIn("Playback", opts)
                self.assertFalse(any("Auto-Download" in o for o in opts))
                return opts.index("Playback")
            if title == "Playback":
                playback_picks += 1
                if playback_picks > 1:
                    return -2
                for i, o in enumerate(opts):
                    if "Auto-Download" in o:
                        return i
            return -2

        def select_settings(flags, ui, *, read_view, switch_translation, information, activate=None):
            from media_cli.app.details_menu_controller import DetailsMenuController, DetailsMenuResult
            from media_cli.ui.view_picker import run_view_session

            view = read_view()
            if view.prompt == "Playback":
                controller = DetailsMenuController(
                    ui=ui, read_view=read_view, switch_translation=switch_translation,
                    information=information, activate=activate,
                )
                return run_view_session(
                    flags, ui, controller.session, picker=playback_menu.tui_pick, updates_fn=controller.updates,
                )
            item = next(item for item in view.items if item.reference == "PLAYBACK_SETTINGS")
            return DetailsMenuResult("selected", item.reference)

        with patch("media_cli.app.playback_menu.run_details_menu", side_effect=select_settings), \
             patch("media_cli.app.playback_menu.tui_pick", side_effect=pick_action), \
             patch("media_cli.app_core.save_config") as mock_save:

            playback_menu.handle_action_menu_state(flags, ui, ms, cfg, MagicMock(), "sub", MagicMock(return_value=False))
            self.assertEqual(ms.auto_download_next, "adaptive")
            self.assertFalse(cfg["auto_download_next"])
            mock_save.assert_not_called()

        delete_playback_picks = 0

        def pick_delete(fl, u, title, opts, **kwargs):
            nonlocal delete_playback_picks
            if title == "Select action":
                return opts.index("Playback")
            if title == "Playback":
                delete_playback_picks += 1
                if delete_playback_picks > 1:
                    return -2
                for i, o in enumerate(opts):
                    if "Auto-Delete" in o:
                        return i
            return -2

        with patch("media_cli.app.playback_menu.run_details_menu", side_effect=select_settings), \
             patch("media_cli.app.playback_menu.tui_pick", side_effect=pick_delete), \
             patch("media_cli.app_core.save_config") as mock_save:

            playback_menu.handle_action_menu_state(flags, ui, ms, cfg, MagicMock(), "sub", MagicMock(return_value=False))
            self.assertTrue(ms.auto_delete_watched)
            self.assertFalse(cfg["auto_delete_watched"])
            mock_save.assert_not_called()

    def test_online_mode_episode_picker_greys_out_watched_episodes(self):
        """In regular online mode (not downloads), watched episodes must still be greyed out."""
        flags = CliFlags()
        ui = UiState()
        ms = MachineState()
        ms._is_downloads = False  # Online mode
        ms.show_title = "Frieren"
        ms.total_eps = 28
        ms.current_ep_index = 0

        eids = [str(i) for i in range(1, 29)]
        labels = {str(i): f"Episode {i}" for i in range(1, 29)}
        show = {
            "name": "Frieren",
            "_episode_ids": eids,
            "_episode_labels": labels,
            "_local_progress": 10,  # Episodes 1-10 are watched
            "_local_episode_label": "10",
            "episodeCount": 28,
        }
        ui.ui_show_ctx = show
        ui.ep_prev_state = "DETAILS"
        cfg = {"episode_order": "asc"}

        captured_opts = []
        captured_hints_fn = None

        def fake_tui_pick(*args, **kwargs):
            nonlocal captured_opts, captured_hints_fn
            from media_cli.app.details_menu_controller import DetailsMenuResult
            from media_cli.state.theme import APPLICATION_THEME
            from media_cli.ui.row_text import render_row_text
            view = kwargs["read_view"]()
            captured_opts = [render_row_text(item.label, columns=120, theme=APPLICATION_THEME) for item in view.items]
            def captured_hints_fn(i):
                return view.items[i].hint_text
            return DetailsMenuResult("back")

        with patch("media_cli.app_core.ensure_episode_ids", return_value=eids), \
             patch("media_cli.app_core.find_offline_file_for_episode", return_value=None), \
             patch("media_cli.app.playback_episodes.run_details_menu", side_effect=fake_tui_pick):

            res = playback_episodes.handle_episode_state(flags, ui, ms, cfg, MagicMock(), "sub", MagicMock(return_value=False))
            self.assertEqual(res, "DETAILS")

            # Ep 1-10 are watched -> must be greyed out
            for i in range(10):
                self.assertIn(f"\033[38;5;244mEpisode {i+1}\033[0m", captured_opts[i])
                self.assertEqual(captured_hints_fn(i), "watched")

            # Ep 11-28 are unwatched -> must be normal text
            for i in range(10, 28):
                self.assertIn(f"Episode {i+1}", captured_opts[i])
                self.assertNotIn("\033[38;5;244m", captured_opts[i])
                self.assertEqual(captured_hints_fn(i), "")

    def test_setup_download_directory_counts_only_disk_shows_and_prunes_phantoms(self):
        from media_cli.app.orchestrator import setup_download_directory
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create Show 1 on disk
            show1_dir = os.path.join(tmpdir, "Show One")
            os.makedirs(show1_dir)
            with open(os.path.join(show1_dir, "Show One - Episode 1.mp4"), "w") as f:
                f.write("data")
            with open(os.path.join(show1_dir, "Show One - Episode 2.mp4"), "w") as f:
                f.write("data")

            # Create Show 2 on disk
            show2_dir = os.path.join(tmpdir, "Show Two")
            os.makedirs(show2_dir)
            with open(os.path.join(show2_dir, "Show Two - Episode 1.mkv"), "w") as f:
                f.write("data")

            # DB has Show 1 (2 eps), Phantom Show 3 (1 ep), but NOT Show 2
            db_data = {
                "current_download_dir": tmpdir,
                "shows": {
                    "Show One": {"episodes": ["1", "2"]},
                    "Phantom Show 3": {"episodes": ["1"]},
                }
            }
            cfg = {"download_dir": ""}

            with patch("sys.stdin.isatty", return_value=True), \
                 patch("builtins.input", return_value="1"), \
                 patch("media_cli.app.orchestrator.save_config"), \
                 patch("media_cli.app.orchestrator.save_downloads_db"):
                chosen = setup_download_directory(cfg, db_data)
                self.assertEqual(chosen, tmpdir)
                # Phantom Show 3 should be pruned
                self.assertNotIn("Phantom Show 3", db_data["shows"])
                # Show One and Show Two should be in db
                self.assertIn("Show One", db_data["shows"])
                self.assertIn("Show Two", db_data["shows"])
                self.assertEqual(len(db_data["shows"]), 2)


if __name__ == "__main__":
    unittest.main()
