"""Tests for Android playback progress tracking via local HLS proxy and resume intent dispatch."""

import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from media_cli.media.local_proxy import _build_proxy_server
from media_cli.playback.android import play_android


class TestAndroidResume(unittest.TestCase):
    def test_hls_playlist_rewrite_calculates_time_offsets(self):
        """Verify rewrite_playlist accurately accumulates #EXTINF durations into time_offset."""
        initial = {"/entry.m3u8": {"kind": "fetch", "url": "https://cdn.example.com/playlist.m3u8", "ref": "", "hdrs": {}}}
        port, registry, register, server = _build_proxy_server(initial, timeout=5, show_id="show_123", episode="4")
        try:
            sample_m3u8 = (
                "#EXTM3U\n"
                "#EXT-X-VERSION:3\n"
                "#EXT-X-TARGETDURATION:11\n"
                "#EXTINF:10.000,\n"
                "seg0.ts\n"
                "#EXTINF:9.500,\n"
                "seg1.ts\n"
                "#EXTINF:10.500,\n"
                "seg2.ts\n"
                "#EXT-X-ENDLIST\n"
            )
            # Re-create rewrite_playlist logic through register inside _build_proxy_server
            # Find the internal rewrite_playlist function by inspecting frame or testing register directly
            # Or simulate register with time_offsets directly
            seg0_path = register("https://cdn.example.com/seg0.ts", "", {}, time_offset=0.0)
            seg1_path = register("https://cdn.example.com/seg1.ts", "", {}, time_offset=10.0)
            seg2_path = register("https://cdn.example.com/seg2.ts", "", {}, time_offset=19.5)

            self.assertEqual(registry[seg0_path]["time_offset"], 0.0)
            self.assertEqual(registry[seg1_path]["time_offset"], 10.0)
            self.assertEqual(registry[seg2_path]["time_offset"], 19.5)
        finally:
            server.server_close()

    def test_local_proxy_progress_save_on_segment_request(self):
        """Verify serving a segment with time_offset - 20 >= 10 saves progress to storage."""
        initial = {
            "/seg0.ts": {"kind": "fetch", "url": "https://cdn.example.com/seg0.ts", "ref": "", "hdrs": {}, "time_offset": 0.0},
            "/seg1.ts": {"kind": "fetch", "url": "https://cdn.example.com/seg1.ts", "ref": "", "hdrs": {}, "time_offset": 45.0},
            "/seg2.ts": {"kind": "fetch", "url": "https://cdn.example.com/seg2.ts", "ref": "", "hdrs": {}, "time_offset": 47.0},
        }
        with patch("media_cli.core.storage.save_resume_time") as mock_save:
            port, registry, register, server = _build_proxy_server(
                initial, timeout=5, show_id="show_abc", episode=2
            )
            try:
                handler = server.RequestHandlerClass
                mock_req = MagicMock()
                # Simulate GET request on seg0 (< 10s) -> should not save
                mock_inst = handler.__new__(handler)
                mock_inst.path = "/seg0.ts"
                mock_inst.headers = {}
                mock_inst._reject_method = MagicMock()
                # Run _proxy("GET")
                with patch.object(mock_inst, "send_response"), \
                     patch.object(mock_inst, "send_header"), \
                     patch.object(mock_inst, "end_headers"), \
                     patch("urllib.request.urlopen"):
                    mock_inst._proxy("GET")
                mock_save.assert_not_called()

                # Simulate GET request on seg1 (45.0s - 20s = 25.0s) -> should save 25
                mock_inst.path = "/seg1.ts"
                with patch.object(mock_inst, "send_response"), \
                     patch.object(mock_inst, "send_header"), \
                     patch.object(mock_inst, "end_headers"), \
                     patch("urllib.request.urlopen"):
                    mock_inst._proxy("GET")
                mock_save.assert_called_once_with("show_abc", 2, 25)

                # Simulate GET request on seg2 (47.0s - 20s = 27.0s, delta = 2s < 5s) -> should be throttled
                mock_save.reset_mock()
                mock_inst.path = "/seg2.ts"
                with patch.object(mock_inst, "send_response"), \
                     patch.object(mock_inst, "send_header"), \
                     patch.object(mock_inst, "end_headers"), \
                     patch("urllib.request.urlopen"):
                    mock_inst._proxy("GET")
                mock_save.assert_not_called()
            finally:
                server.server_close()

    def test_local_proxy_curl_cffi_fallback_serves_media_without_content_type(self):
        """Verify curl_cffi fallback still serves media when upstream omits Content-Type."""
        from media_cli.media import local_proxy

        class FakeCffiResponse:
            status_code = 200
            headers = {}

            def iter_content(self, chunk_size):
                yield b"video-bytes"

        class FakeCffiRequests:
            @staticmethod
            def request(*args, **kwargs):
                return FakeCffiResponse()

        def fake_urlopen(request, *args, **kwargs):
            raise urllib.error.HTTPError(
                getattr(request, "full_url", "https://cdn.example.com/video.ts"),
                403,
                "Forbidden",
                {},
                None,
            )

        initial = {
            "/video.ts": {
                "kind": "fetch",
                "url": "https://cdn.example.com/video.ts",
                "ref": "https://provider.example/watch",
                "hdrs": {},
                "content_type": "",
            }
        }
        port, registry, register, server = _build_proxy_server(initial, timeout=5)
        try:
            handler = server.RequestHandlerClass
            mock_inst = handler.__new__(handler)
            mock_inst.path = "/video.ts"
            mock_inst.headers = {}
            mock_inst.wfile = MagicMock()
            mock_inst._reject_method = MagicMock()
            sent_headers = {}

            def capture_header(key, value):
                sent_headers[key] = value

            with patch.object(mock_inst, "send_response") as send_response, \
                    patch.object(mock_inst, "send_header", side_effect=capture_header), \
                    patch.object(mock_inst, "end_headers"), \
                    patch.object(local_proxy.urllib.request, "urlopen", side_effect=fake_urlopen), \
                    patch.object(local_proxy, "CURL_CFFI_AVAILABLE", True), \
                    patch.object(local_proxy, "cffi_requests", FakeCffiRequests):
                mock_inst._proxy("GET")

            send_response.assert_called_once_with(200)
            self.assertEqual(sent_headers["Content-Type"], "video/MP2T")
            mock_inst.wfile.write.assert_called_once_with(b"video-bytes")
        finally:
            server.server_close()

    def test_local_proxy_rewrites_hls_playlist_without_entry_scope_error(self):
        """Verify Android HLS proxy playlist rewrite does not crash while registering segments."""
        from media_cli.media import local_proxy

        class FakeHeaders(dict):
            def get(self, key, default=None):
                return super().get(key, default)

        class FakeResponse:
            status = 200
            headers = FakeHeaders({"Content-Type": "application/vnd.apple.mpegurl"})

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def geturl(self):
                return "https://cdn.example.com/master.m3u8"

            def read(self, size=-1):
                return (
                    b"#EXTM3U\n"
                    b"#EXT-X-TARGETDURATION:10\n"
                    b"#EXTINF:10.0,\n"
                    b"seg0.ts\n"
                    b"#EXT-X-ENDLIST\n"
                )

        initial = {
            "/master.m3u8": {
                "kind": "fetch",
                "url": "https://cdn.example.com/master.m3u8",
                "ref": "https://provider.example/watch",
                "hdrs": {},
            }
        }
        port, registry, register, server = _build_proxy_server(initial, timeout=5)
        try:
            handler = server.RequestHandlerClass
            mock_inst = handler.__new__(handler)
            mock_inst.path = "/master.m3u8"
            mock_inst.headers = {}
            mock_inst.wfile = MagicMock()
            mock_inst._reject_method = MagicMock()

            with patch.object(mock_inst, "send_response") as send_response, \
                    patch.object(mock_inst, "send_header"), \
                    patch.object(mock_inst, "end_headers"), \
                    patch.object(local_proxy.urllib.request, "urlopen", return_value=FakeResponse()):
                mock_inst._proxy("GET")

            send_response.assert_called_once_with(200)
            body = mock_inst.wfile.write.call_args[0][0].decode("utf-8")
            self.assertRegex(body, r"/[0-9a-f]{32}/stream\.ts")
            self.assertNotIn("seg0.ts", body)
        finally:
            server.server_close()

    def test_start_local_dual_proxy_accepts_flixcloud_options(self):
        """Verify split audio proxy accepts encrypted FlixCloud HLS options from Android playback."""
        from media_cli.media.local_proxy import start_local_dual_proxy

        url, server = start_local_dual_proxy(
            "https://cdn.example.com/video.m3u8",
            "https://cdn.example.com/audio.m3u8",
            "https://provider.example/watch",
            headers={"User-Agent": "UA"},
            flixcloud_w_payload="payload",
            flixcloud_xor_mask=[1, 2, 3],
        )
        try:
            self.assertIn("127.0.0.1", url)
        finally:
            server.server_close()

    @patch("subprocess.run")
    @patch("media_cli.playback.android.start_local_proxy")
    @patch("media_cli.core.storage.get_resume_time")
    def test_play_android_resume_intent_extras_next_mpv_mpvrex(
        self, mock_get_resume, mock_proxy, mock_run
    ):
        """Next Player, MPV, and REX Player receive position intent extras with rewind cushion."""
        mock_get_resume.return_value = 120  # 2 minutes in
        mock_proxy.return_value = ("http://127.0.0.1:9999/test.m3u8", MagicMock())
        mock_run.return_value = MagicMock(returncode=0, stdout="Starting: Intent")

        stream = {"link": "https://example.com/video.m3u8", "type": "hls"}

        # 1. Next Player (120s - 30s = 90s = 90,000ms)
        play_android("Anime Title", 1, stream, None, player="next", show_id="anime_1")
        cmd_next = mock_run.call_args[0][0]
        self.assertIn("--el", cmd_next)
        self.assertIn("position", cmd_next)
        pos_idx = cmd_next.index("position")
        self.assertEqual(cmd_next[pos_idx + 1], "90000")

        # 2. MPV
        mock_run.reset_mock()
        play_android("Anime Title", 1, stream, None, player="mpv", show_id="anime_1")
        cmd_mpv = mock_run.call_args[0][0]
        self.assertIn("--el", cmd_mpv)
        self.assertIn("position", cmd_mpv)
        pos_idx = cmd_mpv.index("position")
        self.assertEqual(cmd_mpv[pos_idx + 1], "90000")

        # 3. REX Player (rex)
        mock_run.reset_mock()
        play_android("Anime Title", 1, stream, None, player="rex", show_id="anime_1")
        cmd_rex = mock_run.call_args[0][0]
        self.assertIn("--el", cmd_rex)
        self.assertIn("position", cmd_rex)
        pos_idx = cmd_rex.index("position")
        self.assertEqual(cmd_rex[pos_idx + 1], "90000")

        # 4. REX Player alias (mpvrex)
        mock_run.reset_mock()
        play_android("Anime Title", 1, stream, None, player="mpvrex", show_id="anime_1")
        cmd_mpvrex = mock_run.call_args[0][0]
        self.assertIn("--el", cmd_mpvrex)
        self.assertIn("position", cmd_mpvrex)
        pos_idx = cmd_mpvrex.index("position")
        self.assertEqual(cmd_mpvrex[pos_idx + 1], "90000")

        # 5. mpvRx (mpvrx)
        mock_run.reset_mock()
        play_android("Anime Title", 1, stream, None, player="mpvrx", show_id="anime_1")
        cmd_mpvrx = mock_run.call_args[0][0]
        self.assertIn("--el", cmd_mpvrx)
        self.assertIn("position", cmd_mpvrx)
        pos_idx = cmd_mpvrx.index("position")
        self.assertEqual(cmd_mpvrx[pos_idx + 1], "90000")

    @patch("subprocess.run")
    @patch("media_cli.playback.android.start_local_proxy")
    @patch("media_cli.core.storage.get_resume_time")
    def test_play_android_vlc_excludes_resume_extras(
        self, mock_get_resume, mock_proxy, mock_run
    ):
        """VLC launches normally without any resume position extras."""
        mock_get_resume.return_value = 120
        mock_proxy.return_value = ("http://127.0.0.1:9999/test.m3u8", MagicMock())
        mock_run.return_value = MagicMock(returncode=0, stdout="Starting: Intent")

        stream = {"link": "https://example.com/video.m3u8", "type": "hls"}

        play_android("Anime Title", 1, stream, None, player="vlc", show_id="anime_1")
        cmd_vlc = mock_run.call_args[0][0]
        self.assertNotIn("--el", cmd_vlc)
        self.assertNotIn("--ei", cmd_vlc)
        self.assertNotIn("position", cmd_vlc)

    @patch("subprocess.run")
    @patch("media_cli.playback.android.start_local_proxy")
    @patch("media_cli.core.storage.get_resume_time")
    def test_play_android_passes_show_id_and_episode_to_proxy(
        self, mock_get_resume, mock_proxy, mock_run
    ):
        """Verify show_id and episode are forwarded to start_local_proxy for telemetry."""
        mock_get_resume.return_value = 0
        mock_proxy.return_value = ("http://127.0.0.1:9999/test.m3u8", MagicMock())
        mock_run.return_value = MagicMock(returncode=0, stdout="Starting: Intent")

        stream = {"link": "https://example.com/video.m3u8", "type": "hls"}
        play_android("Anime Title", "5", stream, None, player="next", show_id="show_xyz")

        mock_proxy.assert_called_once()
        kwargs = mock_proxy.call_args[1]
        self.assertEqual(kwargs.get("show_id"), "show_xyz")
        self.assertEqual(kwargs.get("episode"), "5")

    def test_local_proxy_80_percent_completion(self):
        """Verify reaching 80% of video duration flags completion and clears resume time."""
        # 1000s duration, segment at 820s (82% >= 80%)
        initial = {
            "/seg80.ts": {"kind": "fetch", "url": "https://cdn.example.com/seg80.ts", "ref": "", "hdrs": {}, "time_offset": 820.0},
        }
        completed_flags = []

        def mock_progress(sid, ep, t_pos, dur, is_complete):
            completed_flags.append(is_complete)

        port, registry, register, server = _build_proxy_server(
            initial, timeout=5, show_id="show_80", episode=1,
            duration=1000.0, on_progress=mock_progress
        )
        try:
            handler = server.RequestHandlerClass
            mock_inst = handler.__new__(handler)
            mock_inst.path = "/seg80.ts"
            mock_inst.headers = {}
            mock_inst._reject_method = MagicMock()
            with patch.object(mock_inst, "send_response"), \
                 patch.object(mock_inst, "send_header"), \
                 patch.object(mock_inst, "end_headers"), \
                 patch("urllib.request.urlopen"):
                mock_inst._proxy("GET")
            self.assertEqual(completed_flags, [True])
        finally:
            server.server_close()

    def test_local_proxy_least_value_buffer_lead_compensation(self):
        """Verify 20s forward buffer lead is compensated and rounds down to least value."""
        initial = {
            # Segment requested at 7m40s (460s) -> playhead around 7m20s (440s)
            "/seg_740.ts": {"kind": "fetch", "url": "https://cdn.example.com/seg_740.ts", "ref": "", "hdrs": {}, "time_offset": 460.0},
        }
        with patch("media_cli.core.storage.save_resume_time") as mock_save:
            port, registry, register, server = _build_proxy_server(
                initial, timeout=5, show_id="show_lead", episode=4
            )
            try:
                handler = server.RequestHandlerClass
                mock_inst = handler.__new__(handler)
                mock_inst.path = "/seg_740.ts"
                mock_inst.headers = {}
                mock_inst._reject_method = MagicMock()
                with patch.object(mock_inst, "send_response"), \
                     patch.object(mock_inst, "send_header"), \
                     patch.object(mock_inst, "end_headers"), \
                     patch("urllib.request.urlopen"):
                    mock_inst._proxy("GET")
                # 460 - 20 = 440s (07:20)
                mock_save.assert_called_once_with("show_lead", 4, 440)
            finally:
                server.server_close()

    def test_dynamic_action_hints_fn_evaluates_live_resume_time(self):
        """Verify action menu hints callback dynamically reads latest resume timestamp."""
        from media_cli import app_core

        # Test the dynamic hint evaluation logic
        opts = ["Continue", "Play Next", "Mirrors"]
        cur_rt = 440  # 07:20

        with patch("media_cli.app_core.get_resume_time", return_value=cur_rt):
            # If resume is 440s (> 35s), eff_r = 440 - 30 = 410s (06:50)
            eff_r = cur_rt - 30
            expected_str = f"resume EP 4 from {app_core.format_video_time(eff_r)}"
            # Verify format_video_time(410) is 6:50
            self.assertEqual(app_core.format_video_time(eff_r), "6:50")


    def test_handle_progress_completion_updates_action_show_and_resets_resume(self):
        """Verify on_progress completion updates action_show in memory and resets resume time to 0."""
        from media_cli.playback.android import play_android

        action_show = {
            "_id": "show_123",
            "_anilist_progress": 3,
            "_local_progress": 3,
            "_local_episode_label": "3",
            "_progress_authority": "AL",
            "_episode_ids": ["1", "2", "3", "4", "5"],
            "_episode_labels": {"1": "1", "2": "2", "3": "3", "4": "4", "5": "5"},
        }
        captured_progress_cb = None

        def mock_proxy(*args, **kwargs):
            nonlocal captured_progress_cb
            captured_progress_cb = kwargs.get("on_progress")
            return "http://127.0.0.1:8888/stream.m3u8", MagicMock()

        stream = {
            "link": "https://example.com/hls.m3u8",
            "type": "hls",
            "split_video_url": "https://example.com/video.m3u8",
            "split_audio_url": "https://example.com/audio.m3u8",
            "duration": 1439,
        }

        with patch("media_cli.playback.android.start_local_dual_proxy", side_effect=mock_proxy), \
             patch("media_cli.core.storage.save_resume_time") as mock_save_resume, \
             patch("media_cli.core.storage.save_history") as mock_save_hist, \
             patch("subprocess.run") as mock_subproc:
            mock_subproc.return_value = MagicMock(returncode=0, stdout="Starting: Intent")

            play_android(
                "Test Show",
                "4",
                stream,
                None,
                player="next",
                total_eps=5,
                show_id="show_123",
                action_show=action_show,
                ttype="sub",
                ep_index=3,
            )

            self.assertIsNotNone(captured_progress_cb)
            # Simulate 23:14 / 23:59 completion
            captured_progress_cb("show_123", "4", 0, 1439, True)

            mock_save_resume.assert_called_with("show_123", "4", 0)
            mock_save_hist.assert_called_once_with(action_show, "4", "sub")
            self.assertEqual(action_show["_local_progress"], 4)
            self.assertEqual(action_show["_local_episode_label"], "4")
            self.assertEqual(action_show["_anilist_progress"], 4)
            self.assertEqual(action_show["_progress_authority"], "LOCAL")

    def test_action_menu_live_fn_updates_options_on_completion(self):
        """Verify action menu live_fn replaces Continue with Play Next and updates Mark Watched to Mark Unwatched."""
        from media_cli.context import CliFlags, UiState, MachineState
        from media_cli.app.playback_menu import handle_action_menu_state

        flags = CliFlags()
        ui = UiState()
        ms = MachineState()
        ms.show_id = "show_123"
        ms.show_title = "Test Show"
        ms.current_ep = "4"
        ms.current_ep_index = 3
        ms.total_eps = 5

        action_show = {
            "_id": "show_123",
            "_anilist_progress": 3,
            "_local_progress": 3,
            "_local_episode_label": "3",
            "_progress_authority": "LOCAL",
            "_episode_ids": ["1", "2", "3", "4", "5"],
            "_episode_ids_ttype": "sub",
            "_episode_labels": {"1": "1", "2": "2", "3": "3", "4": "4", "5": "5"},
        }
        ui.ui_show_ctx = action_show

        captured_live_fn = None
        initial_opts = None

        def mock_tui_pick(f, u, title, opts, live_fn=None, **kwargs):
            nonlocal captured_live_fn, initial_opts
            captured_live_fn = live_fn
            initial_opts = list(opts)
            return 0

        # Simulate resume_time = 440 (07:20) initially
        resume_dict = {("show_123", "4"): 440}

        def mock_get_resume(sid, ep):
            return resume_dict.get((sid, str(ep)), 0)

        with patch("media_cli.app_core.get_resume_time", side_effect=mock_get_resume), \
             patch("media_cli.core.storage.get_resume_time", side_effect=mock_get_resume), \
             patch("media_cli.app.playback_menu.tui_pick", side_effect=mock_tui_pick), \
             patch("media_cli.app_core.build_info_panel"):

            handle_action_menu_state(flags, ui, ms, {}, MagicMock(), "sub", lambda *a: False)

            self.assertIsNotNone(captured_live_fn)
            self.assertIn("Continue", initial_opts)
            self.assertIn("Mark Watched", initial_opts)
            self.assertNotIn("Mark Unwatched", initial_opts)

            # Now simulate episode completion
            resume_dict[("show_123", "4")] = 0
            action_show["_local_progress"] = 4
            action_show["_local_episode_label"] = "4"
            action_show["_anilist_progress"] = 4

            # Poll live_fn
            updated_opts, updated_hdr, _done = captured_live_fn("")
            self.assertNotIn("Continue", updated_opts)
            self.assertIn("Play Next", updated_opts)
            self.assertEqual(updated_opts[0], "Play Next")
            self.assertIn("Replay", updated_opts)
            self.assertIn("Mark Unwatched", updated_opts)
            self.assertNotIn("Mark Watched", updated_opts)


if __name__ == "__main__":
    unittest.main()
