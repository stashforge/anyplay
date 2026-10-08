import io
import logging
import os
import signal
import sys
import tempfile
import pytest
from unittest.mock import patch, MagicMock

from media_cli.media.download_worker import (
    DownloadItem,
    DownloadStatus,
    DownloadWorker,
    get_global_worker_if_started,
    get_global_worker,
    has_active_downloads,
    has_tracked_downloads,
    stop_global_worker,
)
from media_cli.ui.picker_render import get_key
from media_cli.ui.download_manager import prompt_download_exit_cli


def test_cli_keyboard_interrupt_is_clean_interactive_exit(monkeypatch):
    from media_cli.cli import main as cli_main
    from media_cli.services import catalog

    monkeypatch.setenv("ALLMANGA_NO_OS_EXIT", "1")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli_main.app, "app_main", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    monkeypatch.setattr(cli_main.app, "restore_terminal", lambda: None)
    monkeypatch.setattr(cli_main.app, "flush_anilist_writes", lambda: None)
    monkeypatch.setattr(cli_main.app, "_ipc_player", MagicMock())
    monkeypatch.setattr(cli_main.app, "kill_active_subprocesses", lambda: None)
    monkeypatch.setattr(catalog._catalog_executor, "shutdown", lambda *args, **kwargs: None)
    monkeypatch.setattr("media_cli.media.download_worker.stop_global_worker", lambda: None)

    assert cli_main.run() == 0


def test_alt_d_key_decoding():
    # Alt+D sends ESC followed by 'd' or 'D'
    r, w = os.pipe()
    try:
        os.write(w, b"\x1bd")
        assert get_key(r) == "ALT_D"

        os.write(w, b"\x1bD")
        assert get_key(r) == "ALT_D"

        os.write(w, b"\x1b")
        assert get_key(r) == "ESC"
    finally:
        os.close(r)
        os.close(w)


def test_unknown_alt_key_is_ignored():
    r, w = os.pipe()
    try:
        os.write(w, b"\x1bx")
        assert get_key(r) == "UNKNOWN"
    finally:
        os.close(r)
        os.close(w)


def test_download_worker_lifecycle():
    with tempfile.TemporaryDirectory() as tmpdir:
        w = DownloadWorker(download_dir=tmpdir, max_concurrent=2)
        assert w.max_concurrent == 2
        assert w.wait_all(timeout=0.1) is True

        # Test item creation
        item = DownloadItem(
            id="test-1",
            title="Test Show",
            episode="Episode 1",
            destination_path=os.path.join(tmpdir, "test.mp4"),
        )
        w.add_item(item)
        assert len(w.items) == 1
        assert item.status == DownloadStatus.QUEUED
        w.stop()


def test_has_active_downloads():
    with tempfile.TemporaryDirectory() as tmpdir:
        stop_global_worker()
        active, queued = has_active_downloads()
        assert active == 0
        assert queued == 0

        gw = get_global_worker(download_dir=tmpdir)
        item = DownloadItem(
            id="test-2",
            title="Test Show 2",
            episode="Episode 2",
            destination_path=os.path.join(tmpdir, "test2.mp4"),
        )
        gw.add_item(item)
        active, queued = has_active_downloads()
        assert queued == 1
        gw.cancel_item(item.id)
        stop_global_worker()


def test_has_tracked_downloads_does_not_create_global_worker():
    stop_global_worker()

    assert get_global_worker_if_started() is None
    assert has_tracked_downloads() is False
    assert get_global_worker_if_started() is None


def test_has_active_downloads_counts_paused_as_unfinished():
    with tempfile.TemporaryDirectory() as tmpdir:
        stop_global_worker()
        gw = get_global_worker(download_dir=tmpdir)
        item = DownloadItem(
            id="paused-download",
            title="Paused Show",
            episode="Episode 3",
            destination_path=os.path.join(tmpdir, "paused.mp4"),
        )
        gw.add_item(item)
        with gw.lock:
            item.status = DownloadStatus.PAUSED

        active, queued = has_active_downloads()

        assert active == 1
        assert queued == 0
        gw.cancel_item(item.id)
        stop_global_worker()


def test_has_tracked_downloads_detects_existing_items():
    with tempfile.TemporaryDirectory() as tmpdir:
        stop_global_worker()
        gw = get_global_worker(download_dir=tmpdir)
        gw.add_item(DownloadItem(
            id="tracked-download",
            title="Tracked Show",
            episode="Episode 1",
            destination_path=os.path.join(tmpdir, "tracked.mp4"),
        ))

        assert has_tracked_downloads() is True
        stop_global_worker()


def test_has_tracked_downloads_hides_idle_dismissed_manager():
    with tempfile.TemporaryDirectory() as tmpdir:
        stop_global_worker()
        gw = get_global_worker(download_dir=tmpdir)
        item = DownloadItem(
            id="completed-only",
            title="Completed Show",
            episode="Episode 1",
            destination_path=os.path.join(tmpdir, "completed.mp4"),
            status=DownloadStatus.COMPLETED,
        )
        gw.add_item(item)

        assert has_tracked_downloads() is True
        gw.dismiss_when_idle()
        assert has_tracked_downloads() is False

        gw.add_item(DownloadItem(
            id="new-download",
            title="New Show",
            episode="Episode 2",
            destination_path=os.path.join(tmpdir, "new.mp4"),
        ))
        assert has_tracked_downloads() is True
        stop_global_worker()


def test_download_worker_can_resume_paused_items_for_finish_quit():
    with tempfile.TemporaryDirectory() as tmpdir:
        w = DownloadWorker(download_dir=tmpdir, max_concurrent=1)
        try:
            item = DownloadItem(
                id="paused-finish",
                title="Paused Finish",
                episode="Episode 4",
                destination_path=os.path.join(tmpdir, "finish.mp4"),
            )
            w.add_item(item)
            with w.lock:
                item.status = DownloadStatus.PAUSED

            assert w.resume_paused_items() == 1
            assert item.status == DownloadStatus.QUEUED
        finally:
            w.stop()


def test_download_manager_counts_paused_rows_as_unfinished():
    from media_cli.ui import download_manager

    active, queued = download_manager._count_unfinished_download_rows([
        {"status": "PAUSED"},
        {"status": "QUEUED"},
        {"status": "DONE"},
    ])

    assert active == 1
    assert queued == 1


def test_prompt_download_exit_cli_non_tty():
    # If not a tty, should return STOP_QUIT
    with patch("sys.stdin.isatty", return_value=False):
        res = prompt_download_exit_cli(1, 2)
        assert res == "STOP_QUIT"


def test_prompt_download_exit_cli_uses_alt_screen(monkeypatch, capsys):
    import termios
    import tty
    from media_cli.ui import download_manager

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdin, "fileno", lambda: 0)
    monkeypatch.setattr(termios, "tcgetattr", lambda fd: ["old"])
    monkeypatch.setattr(termios, "tcsetattr", lambda *args: None)
    monkeypatch.setattr(tty, "setraw", lambda fd: None)
    monkeypatch.setattr(download_manager.os, "read", lambda fd, size: b"c")

    result = prompt_download_exit_cli(1, 0)

    captured = capsys.readouterr().out
    assert result == "CANCEL"
    assert "\033[?1049h" in captured
    assert "\033[?1049l" in captured
    assert "\033[2;3H" in captured
    assert "\n" not in captured


def test_prompt_download_exit_cli_does_not_nest_alt_screen(monkeypatch, capsys):
    import termios
    import tty
    from media_cli.ui import display, download_manager

    display.set_alt_screen_active(True)
    try:
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(sys.stdin, "fileno", lambda: 0)
        monkeypatch.setattr(termios, "tcgetattr", lambda fd: ["old"])
        monkeypatch.setattr(termios, "tcsetattr", lambda *args: None)
        monkeypatch.setattr(tty, "setraw", lambda fd: None)
        monkeypatch.setattr(download_manager.os, "read", lambda fd, size: b"c")

        result = prompt_download_exit_cli(1, 0)

        captured = capsys.readouterr().out
        assert result == "CANCEL"
        assert "\033[?1049h" not in captured
        assert "\033[?1049l" not in captured
        assert "\033[H\033[2J" in captured
        assert captured.count("\033[H\033[2J") >= 2
        assert "\033[2;3H" in captured
        assert "\n" not in captured
    finally:
        display.set_alt_screen_active(False)


def test_download_worker_logs_resolution_failure(caplog):
    with tempfile.TemporaryDirectory() as tmpdir:
        w = DownloadWorker(download_dir=tmpdir, max_concurrent=1)
        try:
            item = DownloadItem(
                id="resolve-fail",
                title="Broken Resolver",
                episode="Episode 1",
                destination_path=os.path.join(tmpdir, "broken.mp4"),
                resolve_fn=lambda _item: (_ for _ in ()).throw(RuntimeError("provider token expired")),
            )

            with caplog.at_level(logging.WARNING, logger="media_cli.media.download_worker"):
                w._process_item(item)

            assert item.status == DownloadStatus.FAILED
            assert "Stream resolution failed" in item.error
            assert "provider token expired" in caplog.text
            assert "resolve-fail" in caplog.text
            assert "Traceback" not in caplog.text
        finally:
            w.stop()


def test_download_worker_logs_completion_callback_failure(caplog):
    with tempfile.TemporaryDirectory() as tmpdir:
        destination = os.path.join(tmpdir, "already-here.mp4")
        with open(destination, "wb") as fh:
            fh.write(b"video")
        w = DownloadWorker(download_dir=tmpdir, max_concurrent=1)
        try:
            item = DownloadItem(
                id="callback-fail",
                title="Callback Failure",
                episode="Episode 1",
                destination_path=destination,
                on_complete=lambda _item: (_ for _ in ()).throw(RuntimeError("library update failed")),
            )

            with caplog.at_level(logging.ERROR, logger="media_cli.media.download_worker"):
                w._process_item(item)

            assert item.status == DownloadStatus.SKIPPED
            assert "library update failed" in caplog.text
            assert "callback-fail" in caplog.text
        finally:
            w.stop()


def test_download_worker_logs_proxy_cleanup_failure(caplog):
    with tempfile.TemporaryDirectory() as tmpdir:
        w = DownloadWorker(download_dir=tmpdir, max_concurrent=1)
        try:
            item = DownloadItem(
                id="proxy-cleanup-fail",
                title="Proxy Cleanup Failure",
                episode="Episode 1",
                destination_path=os.path.join(tmpdir, "download.mp4"),
                url="https://example.invalid/video.mp4",
                engine="unknown-engine",
                proxy_server=object(),
                status=DownloadStatus.DOWNLOADING,
                max_retries=0,
            )

            with patch(
                "media_cli.media.local_proxy.stop_local_proxy",
                side_effect=RuntimeError("proxy socket stuck"),
            ):
                with caplog.at_level(logging.WARNING, logger="media_cli.media.download_worker"):
                    w._process_item(item)

            assert item.status == DownloadStatus.FAILED
            assert item.proxy_server is None
            assert "proxy socket stuck" in caplog.text
            assert "proxy-cleanup-fail" in caplog.text
        finally:
            w.stop()


def test_download_worker_ffmpeg_writes_hidden_partial_then_commits(monkeypatch):
    from media_cli.media import download_worker

    class FakeStdout:
        def __init__(self):
            self.lines = iter(["progress=end\n", ""])

        def readline(self):
            return next(self.lines)

    class FakeProc:
        def __init__(self, output_path):
            self.stdout = FakeStdout()
            self.returncode = 0
            self.output_path = output_path

        def wait(self):
            os.makedirs(os.path.dirname(self.output_path), exist_ok=True)
            with open(self.output_path, "wb") as fh:
                fh.write(b"complete-video")

        def poll(self):
            return 0

    with tempfile.TemporaryDirectory() as tmpdir:
        destination = os.path.join(tmpdir, "Show - Episode 1.mp4")
        captured_cmd = []

        def fake_popen(cmd, *args, **kwargs):
            nonlocal captured_cmd
            captured_cmd = cmd
            return FakeProc(cmd[-1])

        monkeypatch.setattr(download_worker.subprocess, "Popen", fake_popen)
        monkeypatch.setattr(download_worker, "probe_stream_duration", lambda *args, **kwargs: 1.0)

        w = DownloadWorker(download_dir=tmpdir, max_concurrent=1)
        try:
            item = DownloadItem(
                id="ffmpeg-atomic",
                title="Show",
                episode="Episode 1",
                destination_path=destination,
                url="https://example.invalid/master.m3u8",
                engine="ffmpeg",
            )
            w._download_ffmpeg(item)
        finally:
            w.stop()

        assert captured_cmd[-1] != destination
        assert ".anyplay-partials" in captured_cmd[-1]
        assert captured_cmd[-1].endswith(".mp4")
        assert os.path.exists(destination)
        assert not os.path.exists(captured_cmd[-1])
        assert item.status == DownloadStatus.COMPLETED


def test_download_worker_auto_prefers_ffmpeg_for_hls_when_available(monkeypatch):
    from media_cli.media import download_worker

    monkeypatch.setattr(
        download_worker.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in {"yt-dlp", "ffmpeg", "aria2c"} else None,
    )

    assert download_worker._select_download_engine("auto", "https://stream.example.com/master.m3u8") == "ffmpeg"


def test_download_worker_auto_uses_ytdlp_for_hls_without_ffmpeg(monkeypatch):
    from media_cli.media import download_worker

    monkeypatch.setattr(
        download_worker.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name == "yt-dlp" else None,
    )

    assert download_worker._select_download_engine("auto", "https://stream.example.com/master.m3u8") == "yt-dlp"


def test_download_worker_auto_prefers_ytdlp_before_aria2c(monkeypatch):
    from media_cli.media import download_worker

    monkeypatch.setattr(
        download_worker.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in {"yt-dlp", "aria2c"} else None,
    )

    assert download_worker._select_download_engine("auto", "https://stream.example.com/video.mp4") == "yt-dlp"


def test_download_worker_ytdlp_uses_aria2c_as_external_downloader(monkeypatch, tmp_path):
    from media_cli.media import download_worker

    captured_cmd = []

    class FakeStdout:
        def readline(self):
            return ""

    class FakeProc:
        def __init__(self, cmd):
            nonlocal captured_cmd
            captured_cmd = cmd
            self.stdout = FakeStdout()
            self.returncode = 0

        def wait(self):
            return 0

        def poll(self):
            return 0

        def kill(self):
            return None

    monkeypatch.setattr(
        download_worker.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in {"yt-dlp", "aria2c"} else None,
    )
    monkeypatch.setattr(download_worker.subprocess, "Popen", lambda cmd, *a, **k: FakeProc(cmd))

    worker = DownloadWorker(download_dir=str(tmp_path), max_concurrent=1)
    try:
        item = DownloadItem(
            id="ytdlp-aria2c",
            title="Speed Probe",
            episode="Episode 1",
            destination_path=str(tmp_path / "Speed Probe - Episode 1.mp4"),
            url="https://stream.example.com/video.mp4",
            engine="yt-dlp",
        )
        worker._download_ytdlp(item)
    finally:
        worker.stop()

    assert "--downloader" in captured_cmd
    assert captured_cmd[captured_cmd.index("--downloader") + 1] == "aria2c"
    assert "--downloader-args" in captured_cmd
    assert "aria2c:-x 16 -s 16 -k 1M" in captured_cmd


def test_download_worker_ytdlp_enables_audio_multistreams(monkeypatch, tmp_path):
    from media_cli.media import download_worker

    captured_cmd = []

    class FakeStdout:
        def readline(self):
            return ""

    class FakeProc:
        def __init__(self, cmd):
            nonlocal captured_cmd
            captured_cmd = cmd
            self.stdout = FakeStdout()
            self.returncode = 0

        def wait(self):
            return 0

        def poll(self):
            return 0

        def kill(self):
            return None

    monkeypatch.setattr(download_worker.shutil, "which", lambda name: None)
    monkeypatch.setattr(download_worker.subprocess, "Popen", lambda cmd, *a, **k: FakeProc(cmd))

    worker = DownloadWorker(download_dir=str(tmp_path), max_concurrent=1)
    try:
        item = DownloadItem(
            id="ytdlp-multiaudio",
            title="Multi Audio",
            episode="Episode 1",
            destination_path=str(tmp_path / "Multi Audio - Episode 1.mp4"),
            url="https://stream.example.com/video.mp4",
            engine="yt-dlp",
        )
        worker._download_ytdlp(item)
    finally:
        worker.stop()

    assert "--audio-multistreams" in captured_cmd


def test_download_worker_ffmpeg_maps_filtered_audio_and_embedded_subtitles(monkeypatch, tmp_path):
    from media_cli.media import download_worker

    captured_cmd = []
    sub_path = tmp_path / "Show - Episode 1.eng.vtt"
    sub_path.write_text("WEBVTT\n", encoding="utf-8")

    class FakeStdout:
        def __init__(self):
            self._lines = iter(["out_time_us=1000000\n", "total_size=2048\n", "progress=end\n", ""])

        def readline(self):
            return next(self._lines)

    class FakeProc:
        def __init__(self, cmd):
            nonlocal captured_cmd
            captured_cmd = cmd
            self.stdout = FakeStdout()
            self.returncode = 0

        def wait(self):
            os.makedirs(os.path.dirname(captured_cmd[-1]), exist_ok=True)
            with open(captured_cmd[-1], "wb") as fh:
                fh.write(b"complete-video")
            return 0

        def poll(self):
            return 0

        def kill(self):
            return None

    monkeypatch.setattr(download_worker.subprocess, "Popen", lambda cmd, *a, **k: FakeProc(cmd))
    monkeypatch.setattr(download_worker, "probe_stream_duration", lambda *a, **k: 1.0)
    monkeypatch.setattr(download_worker, "download_sidecar_subtitles", lambda *a, **k: [str(sub_path)])
    monkeypatch.setattr(download_worker, "is_valid_download_video", lambda path: True)

    worker = DownloadWorker(download_dir=str(tmp_path), max_concurrent=1)
    try:
        item = DownloadItem(
            id="ffmpeg-tracks",
            title="Show",
            episode="Episode 1",
            destination_path=str(tmp_path / "Show - Episode 1.mkv"),
            url="https://stream.example.com/video.m3u8",
            engine="ffmpeg",
            audio_tracks=[
                {"url": "https://stream.example.com/audio-ja.m3u8", "label": "Japanese", "language": "ja"},
                {"url": "https://stream.example.com/audio-en.m3u8", "label": "English", "language": "en"},
            ],
            subtitles=[
                {"url": "https://stream.example.com/en.vtt", "label": "English", "language": "en"},
                {"url": "https://stream.example.com/es.vtt", "label": "Spanish", "language": "es"},
            ],
            download_audio_langs="eng",
            download_sub_langs="eng",
            subtitle_mode="embed",
        )
        worker._download_ffmpeg(item)
    finally:
        worker.stop()

    assert "https://stream.example.com/audio-en.m3u8" in captured_cmd
    assert "https://stream.example.com/audio-ja.m3u8" not in captured_cmd
    assert str(sub_path) in captured_cmd
    assert "0:v:0" in captured_cmd
    assert "1:a:0" in captured_cmd
    assert "2:s:0" in captured_cmd
    assert "language=eng" in captured_cmd


def test_download_worker_ytdlp_does_not_use_aria2c_for_hls(monkeypatch, tmp_path):
    from media_cli.media import download_worker

    captured_cmd = []

    class FakeStdout:
        def readline(self):
            return ""

    class FakeProc:
        def __init__(self, cmd):
            nonlocal captured_cmd
            captured_cmd = cmd
            self.stdout = FakeStdout()
            self.returncode = 0

        def wait(self):
            return 0

        def poll(self):
            return 0

        def kill(self):
            return None

    monkeypatch.setattr(
        download_worker.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in {"yt-dlp", "aria2c"} else None,
    )
    monkeypatch.setattr(download_worker.subprocess, "Popen", lambda cmd, *a, **k: FakeProc(cmd))

    worker = DownloadWorker(download_dir=str(tmp_path), max_concurrent=1)
    try:
        item = DownloadItem(
            id="hls-no-aria2c",
            title="HLS Probe",
            episode="Episode 1",
            destination_path=str(tmp_path / "HLS Probe - Episode 1.mp4"),
            url="https://stream.example.com/master.m3u8",
            engine="yt-dlp",
        )
        worker._download_ytdlp(item)
    finally:
        worker.stop()

    assert "--downloader" not in captured_cmd
    assert "--downloader-args" not in captured_cmd


def test_download_worker_ytdlp_rejects_playlist_text_output(monkeypatch, tmp_path):
    from media_cli.media import download_worker

    class FakeStdout:
        def readline(self):
            return ""

    class FakeProc:
        def __init__(self, cmd):
            self.stdout = FakeStdout()
            self.returncode = 0
            output_path = cmd[cmd.index("-o") + 1]
            with open(output_path, "w", encoding="utf-8") as fh:
                fh.write("#EXTM3U\n#EXT-X-VERSION:6\n#EXTINF:3.0,\n0.m4s\n")

        def wait(self):
            return 0

        def poll(self):
            return 0

        def kill(self):
            return None

    monkeypatch.setattr(download_worker.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(download_worker.subprocess, "Popen", lambda cmd, *a, **k: FakeProc(cmd))

    worker = DownloadWorker(download_dir=str(tmp_path), max_concurrent=1)
    try:
        item = DownloadItem(
            id="playlist-output",
            title="Playlist Probe",
            episode="Episode 1",
            destination_path=str(tmp_path / "Playlist Probe - Episode 1.mp4"),
            url="https://stream.example.com/master.m3u8",
            engine="yt-dlp",
        )
        worker._download_ytdlp(item)
    finally:
        worker.stop()

    assert item.status == DownloadStatus.FAILED
    assert "playlist" in item.error.lower()


def test_download_worker_retries_failed_mirror_then_falls_back(monkeypatch, tmp_path, caplog):
    from media_cli.media import download_worker

    resolved_urls = []

    def resolve_item(item):
        failed = set(getattr(item, "failed_mirrors", set()))
        if "Mirror One" not in failed:
            item.current_mirror_name = "Mirror One"
            item.current_mirror_url = "https://stream.example.com/mirror-one.m3u8"
            resolved_urls.append(item.current_mirror_url)
            return item.current_mirror_url, {}, None
        item.current_mirror_name = "Mirror Two"
        item.current_mirror_url = "https://stream.example.com/mirror-two.m3u8"
        resolved_urls.append(item.current_mirror_url)
        return item.current_mirror_url, {}, None

    def fake_ffmpeg(self, item):
        if "mirror-one" in item.url:
            item.status = DownloadStatus.FAILED
            item.error = "ffmpeg exited with code 1"
            return
        os.makedirs(os.path.dirname(item.destination_path), exist_ok=True)
        with open(item.destination_path, "wb") as fh:
            fh.write(b"complete-video")
        item.status = DownloadStatus.COMPLETED
        item.progress = 100.0

    monkeypatch.setattr(download_worker.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(DownloadWorker, "_download_ffmpeg", fake_ffmpeg)
    caplog.set_level(logging.DEBUG, logger="media_cli.media.download_worker")

    worker = DownloadWorker(download_dir=str(tmp_path), max_concurrent=1)
    try:
        item = DownloadItem(
            id="fallback-download",
            title="Fallback Show",
            episode="Episode 1",
            destination_path=str(tmp_path / "Fallback Show - Episode 1.mp4"),
            engine="ffmpeg",
            resolve_fn=resolve_item,
            max_retries=1,
        )

        worker._process_item(item)
        assert item.status == DownloadStatus.QUEUED
        assert item.retry_count == 1
        assert resolved_urls == ["https://stream.example.com/mirror-one.m3u8"]

        worker._process_item(item)
        assert item.status == DownloadStatus.QUEUED
        assert item.retry_count == 0
        assert "Mirror One" in item.failed_mirrors
        assert resolved_urls == [
            "https://stream.example.com/mirror-one.m3u8",
            "https://stream.example.com/mirror-one.m3u8",
        ]

        worker._process_item(item)
        assert item.status == DownloadStatus.COMPLETED
        assert resolved_urls[-1] == "https://stream.example.com/mirror-two.m3u8"
        assert any("Retrying same mirror" in line for line in item.logs)
        assert any("Trying next mirror" in line for line in item.logs)
        assert not [
            record
            for record in caplog.records
            if record.name == "media_cli.media.download_worker"
            and record.levelno >= logging.ERROR
        ]
    finally:
        worker.stop()


def test_download_worker_stream_resolution_failure_uses_compact_logs(tmp_path, caplog):
    caplog.set_level(logging.DEBUG, logger="media_cli.media.download_worker")

    worker = DownloadWorker(download_dir=str(tmp_path), max_concurrent=1)
    try:
        item = DownloadItem(
            id="resolve-timeout",
            title="Resolve Timeout",
            episode="Episode 1",
            destination_path=str(tmp_path / "Resolve Timeout - Episode 1.mp4"),
            resolve_fn=lambda _item: (_ for _ in ()).throw(TimeoutError("read timed out")),
        )

        worker._process_item(item)

        assert item.status == DownloadStatus.FAILED
        assert any("Stream resolution failed: read timed out" in line for line in item.logs)
        assert not [
            record
            for record in caplog.records
            if record.name == "media_cli.media.download_worker"
            and record.levelno >= logging.ERROR
        ]
    finally:
        worker.stop()


def test_download_progress_log_trimming_preserves_resolution_history(tmp_path):
    from media_cli.media import download_worker

    item = DownloadItem(
        id="log-trim",
        title="Log Trim Show",
        episode="Episode 1",
        destination_path=str(tmp_path / "Log Trim Show - Episode 1.mp4"),
    )
    item.logs.extend([
        "⚡ Resolving stream URL on the fly…",
        "▶ Testing mirror: Mirror One",
        "✔ Stream resolved (https://example.test/master.m3u8)",
        "Using ffmpeg download engine.",
        "Starting ffmpeg stream downloader…",
    ])

    for index in range(50):
        download_worker._append_progress_log(item, f"ffmpeg: {index}.0% | 1.0MiB | 1.0MiB/s | ETA: 1s")

    assert "⚡ Resolving stream URL on the fly…" in item.logs
    assert "▶ Testing mirror: Mirror One" in item.logs
    assert "✔ Stream resolved (https://example.test/master.m3u8)" in item.logs
    assert len([line for line in item.logs if line.startswith("ffmpeg:")]) <= 20
    assert item.logs[-1].startswith("ffmpeg: 49.0%")


def test_download_log_display_lines_start_with_item_details(tmp_path, monkeypatch):
    from media_cli.ui.download_manager import UIItemWrapper, build_log_display_lines

    fake_home = tmp_path / "home" / "tiru"
    show_dir = fake_home / "Videos" / "anyplay" / "Display Lines Show"
    monkeypatch.setenv("HOME", str(fake_home))
    destination = show_dir / "Display Lines Show - Episode 7.mkv"
    item = DownloadItem(
        id="display-lines",
        title="Display Lines Show",
        episode="Episode 7",
        destination_path=str(destination),
        engine="ffmpeg",
        url="https://stream.example.test/master.m3u8",
    )
    item.status = DownloadStatus.DOWNLOADING
    item.current_mirror_name = "Mirror One"
    item.logs.append("⚡ Resolving stream URL on the fly…")

    lines = build_log_display_lines(UIItemWrapper(item))

    assert lines[:7] == [
        "Title:   Display Lines Show",
        "Episode: 7",
        "Status:  DOWNLOADING",
        "Engine:  ffmpeg",
        "Mirror:  Mirror One",
        "Filename: Display Lines Show - Episode 7.mkv",
        "Path:    ~/Videos/anyplay/Display Lines Show",
    ]
    assert "⚡ Resolving stream URL on the fly…" in lines


def test_download_table_layout_keeps_short_titles_compact():
    from media_cli.ui.download_manager import UIItemWrapper, build_download_table_layout

    items = [
        DownloadItem(
            id=f"ep-{idx}",
            title="Martial Master",
            episode=f"Episode {671 + idx}",
            destination_path=f"/tmp/martial-{idx}.mp4",
        )
        for idx in range(3)
    ]
    rows = [UIItemWrapper(item) for item in items]

    layout = build_download_table_layout(96, rows)

    assert layout["title_w"] <= 20
    assert layout["title_w"] >= len("Martial Master E671")
    assert layout["total_w"] == 96
    assert len(layout["title_gap"]) > 3
    assert len(layout["progress_gap"]) > 2
    assert layout["mode"] == "full6"


def test_download_table_layout_expands_and_truncates_long_titles():
    from media_cli.ui.download_manager import UIItemWrapper, build_download_table_layout, format_table_title

    item = DownloadItem(
        id="long-title",
        title="A Very Long Anime Title That Needs The Elastic Title Column",
        episode="Episode 12",
        destination_path="/tmp/long.mp4",
    )
    row = UIItemWrapper(item)

    layout = build_download_table_layout(72, [row])
    rendered = format_table_title(row.title, row.ep, layout["title_w"])

    assert layout["total_w"] <= 72
    assert layout["title_w"] > 20
    assert len(rendered) == layout["title_w"]
    assert "…" in rendered


def test_download_table_layout_expands_value_columns_before_title():
    from media_cli.ui.download_manager import UIItemWrapper, build_download_table_layout

    item = DownloadItem(
        id="active-large-values",
        title="A Long Enough Show Title",
        episode="Episode 3",
        destination_path="/tmp/active.mp4",
    )
    item.status = DownloadStatus.DOWNLOADING
    item.speed_str = "123.4MiB/s"
    item.eta_str = "01:23:45"
    row = UIItemWrapper(item)

    layout = build_download_table_layout(96, [row])

    assert layout["total_w"] == 96
    assert layout["spd_hdr_w"] >= len("123.4MiB/s")
    assert layout["eta_w"] >= len("01:23:45")
    assert layout["title_w"] >= layout["min_title_w"]


def test_download_content_rows_leave_blank_line_above_footer():
    from media_cli.ui.download_manager import download_content_row_count

    assert download_content_row_count(height=20, table_hdr_row=5) == 12
    assert download_content_row_count(height=8, table_hdr_row=6) == 1


def test_download_log_panel_starts_with_logs_without_duplicate_inspector_summary(tmp_path):
    from media_cli.ui.download_manager import UIItemWrapper, build_log_panel_rows

    item = DownloadItem(
        id="wide-logs",
        title="Wide Logs Show",
        episode="Episode 9",
        destination_path=str(tmp_path / "Wide Logs Show - Episode 9.mp4"),
        logs=["Resolving...", "Done."],
    )
    rows, range_text = build_log_panel_rows(UIItemWrapper(item), width=72, row_count=12)

    assert range_text == ""
    assert rows[0] == "• Title:   Wide Logs Show"
    assert not any(row.startswith("Show:") for row in rows)
    assert not any(row.startswith("Size:") for row in rows)


def test_download_manager_uses_htop_like_color_pairs(monkeypatch):
    from media_cli.ui import download_manager

    calls = {}

    class DummyCurses:
        COLORS = 256
        COLOR_BLACK = 0
        COLOR_GREEN = 2
        COLOR_BLUE = 4
        COLOR_CYAN = 6

        @staticmethod
        def init_pair(pair_id, fg, bg):
            calls[pair_id] = (fg, bg)

    download_manager._init_download_color_pairs(DummyCurses)

    assert calls[10] == (0, 2)  # ANSI green from the terminal theme
    assert calls[11] == (0, 2)  # active tab matches header
    assert calls[12] == (0, 4)  # ANSI blue from the terminal theme
    assert calls[13] == (0, 6)  # ANSI cyan from the terminal theme


def test_download_filter_tabs_keep_plain_separator_between_tab_blocks():
    from media_cli.ui.download_manager import build_filter_tab_segments

    segments = build_filter_tab_segments(
        ["all", "active", "completed"],
        "all",
        {"all": 28, "active": 0, "completed": 28},
        is_compact=False,
        start_x=1,
    )

    first, second, third = segments
    assert second["x"] == first["x"] + len(first["label"]) + 1
    assert third["x"] == second["x"] + len(second["label"]) + 1


def test_download_manager_owns_resize_signal_while_running(monkeypatch):
    from media_cli.ui import download_manager

    def previous_resize_handler(signum, frame):
        return None

    installed_handlers = []
    resize_calls = []

    class DummyWorker:
        max_concurrent = 1
        download_dir = "/tmp"
        items = []

        def __init__(self):
            self.lock = DummyLock()

        def start(self):
            return None

    class DummyLock:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class DummyStats:
        def read_network_ssid(self):
            return "Wi-Fi", "test", "wlan0"

        def read_cpu(self):
            return 1.0, 12.0

        def read_mem(self):
            return 1.0, 4.0, 25.0

        def read_disk(self):
            return 10.0, 20.0, 50.0

        def read_temp(self):
            return 40.0

        def read_loadavg(self):
            return "0.1 0.2 0.3"

        def read_uptime(self):
            return "00:01:00"

        def read_net_speed(self):
            return 0.0, "0.0M/s"

    class DummyScreen:
        def getmaxyx(self):
            return (24, 80)

        def erase(self):
            return None

        def clear(self):
            return None

        def clearok(self, value):
            return None

        def nodelay(self, value):
            return None

        def timeout(self, value):
            return None

        def leaveok(self, value):
            return None

        def refresh(self):
            return None

        def getch(self):
            return ord("q")

        def addstr(self, *args):
            return None

    def fake_signal(signum, handler):
        assert signum == signal.SIGWINCH
        installed_handlers.append(handler)
        return previous_resize_handler

    def fake_wrapper(runner):
        assert installed_handlers
        active_handler = installed_handlers[-1]
        assert active_handler is not previous_resize_handler

        active_handler(signal.SIGWINCH, None)
        active_handler(signal.SIGWINCH, None)
        active_handler(signal.SIGWINCH, None)
        return runner(DummyScreen())

    def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(signal, "getsignal", lambda signum: previous_resize_handler)
    monkeypatch.setattr(signal, "signal", fake_signal)
    monkeypatch.setattr(download_manager.curses, "wrapper", fake_wrapper)
    monkeypatch.setattr(download_manager, "_handle_resize_event", lambda stdscr: resize_calls.append(stdscr))
    monkeypatch.setattr(download_manager, "SystemStats", DummyStats)
    monkeypatch.setattr(download_manager.curses, "curs_set", noop)
    monkeypatch.setattr(download_manager.curses, "use_default_colors", noop)
    monkeypatch.setattr(download_manager.curses, "set_escdelay", noop)
    monkeypatch.setattr(download_manager.curses, "mousemask", lambda *args, **kwargs: (0, 0))
    monkeypatch.setattr(download_manager.curses, "mouseinterval", noop)
    monkeypatch.setattr(download_manager.curses, "COLORS", 256, raising=False)
    monkeypatch.setattr(download_manager.curses, "init_pair", noop)
    monkeypatch.setattr(download_manager.curses, "color_pair", lambda number: number)

    assert download_manager.run_download_manager(DummyWorker(), standalone=False) == "BACK"
    assert len(resize_calls) == 1
    assert installed_handlers[-1] is previous_resize_handler


def test_download_manager_reads_real_terminal_size_on_resize(monkeypatch):
    from media_cli.ui import download_manager

    class DummyScreen:
        def getmaxyx(self):
            return (24, 80)

    monkeypatch.setattr(
        download_manager.os,
        "get_terminal_size",
        lambda fd: os.terminal_size((132, 41)),
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdin, "fileno", lambda: 0)

    assert download_manager._current_terminal_size(DummyScreen()) == (41, 132)


def test_download_manager_alt_d_returns_to_cli(monkeypatch):
    from media_cli.ui import download_manager

    class DummyLock:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class DummyWorker:
        def __init__(self):
            self.lock = DummyLock()
            self.max_concurrent = 2
            self.download_dir = "/tmp"
            self.items = [
                DownloadItem(
                    id="toggle",
                    title="Toggle Probe",
                    episode="Episode 1",
                    destination_path="/tmp/toggle.mp4",
                    status=DownloadStatus.DOWNLOADING,
                )
            ]

        def start(self):
            return None

    class DummyStats:
        def read_network_ssid(self):
            return "Wi-Fi", "test", "wlan0"

        def read_cpu(self):
            return 1.0, 12.0

        def read_mem(self):
            return 1.0, 4.0, 25.0

        def read_disk(self):
            return 10.0, 20.0, 50.0

        def read_temp(self):
            return 40.0

        def read_loadavg(self):
            return "0.1 0.2 0.3"

        def read_uptime(self):
            return "00:01:00"

        def read_net_speed(self):
            return 0.0, "0.0M/s"

    class DummyScreen:
        def __init__(self):
            self.keys = [27, ord("d")]

        def getmaxyx(self):
            return (24, 80)

        def erase(self):
            return None

        def clearok(self, value):
            return None

        def nodelay(self, value):
            return None

        def timeout(self, value):
            return None

        def leaveok(self, value):
            return None

        def refresh(self):
            return None

        def getch(self):
            return self.keys.pop(0) if self.keys else -1

        def addstr(self, *args):
            return None

    monkeypatch.setattr(download_manager, "SystemStats", DummyStats)
    monkeypatch.setattr(download_manager.curses, "curs_set", lambda *args: None)
    monkeypatch.setattr(download_manager.curses, "use_default_colors", lambda *args: None)
    monkeypatch.setattr(download_manager.curses, "set_escdelay", lambda *args: None)
    monkeypatch.setattr(download_manager.curses, "mousemask", lambda *args, **kwargs: (0, 0))
    monkeypatch.setattr(download_manager.curses, "mouseinterval", lambda *args: None)
    monkeypatch.setattr(download_manager.curses, "COLORS", 256, raising=False)
    monkeypatch.setattr(download_manager.curses, "init_pair", lambda *args: None)
    monkeypatch.setattr(download_manager.curses, "color_pair", lambda number: number)

    assert download_manager._curses_download_manager(DummyScreen(), DummyWorker(), standalone=False) == "BACK"


def test_download_manager_repaints_after_exit_dialog_cancel(monkeypatch):
    from media_cli.ui import download_manager

    class DummyLock:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class DummyWorker:
        def __init__(self):
            self.lock = DummyLock()
            self.max_concurrent = 2
            self.download_dir = "/tmp"
            self.items = [
                DownloadItem(
                    id="cancel-dialog",
                    title="Cancel Dialog",
                    episode="Episode 1",
                    destination_path="/tmp/cancel-dialog.mp4",
                    status=DownloadStatus.DOWNLOADING,
                )
            ]

        def start(self):
            return None

    class DummyStats:
        def read_network_ssid(self):
            return "Wi-Fi", "test", "wlan0"

        def read_cpu(self):
            return 1.0, 12.0

        def read_mem(self):
            return 1.0, 4.0, 25.0

        def read_disk(self):
            return 10.0, 20.0, 50.0

        def read_temp(self):
            return 40.0

        def read_loadavg(self):
            return "0.1 0.2 0.3"

        def read_uptime(self):
            return "00:01:00"

        def read_net_speed(self):
            return 0.0, "0.0M/s"

    class DummyScreen:
        def __init__(self):
            self.keys = [3, ord("q")]
            self.erase_count = 0

        def getmaxyx(self):
            return (24, 80)

        def erase(self):
            self.erase_count += 1

        def clearok(self, value):
            return None

        def nodelay(self, value):
            return None

        def timeout(self, value):
            return None

        def leaveok(self, value):
            return None

        def refresh(self):
            return None

        def getch(self):
            return self.keys.pop(0) if self.keys else -1

        def addstr(self, *args):
            return None

    screen = DummyScreen()
    monkeypatch.setattr(download_manager, "SystemStats", DummyStats)
    monkeypatch.setattr(download_manager, "show_download_exit_dialog", lambda *args: "CANCEL")
    monkeypatch.setattr(download_manager.curses, "raw", lambda: None, raising=False)
    monkeypatch.setattr(download_manager.curses, "curs_set", lambda *args: None)
    monkeypatch.setattr(download_manager.curses, "use_default_colors", lambda *args: None)
    monkeypatch.setattr(download_manager.curses, "set_escdelay", lambda *args: None)
    monkeypatch.setattr(download_manager.curses, "mousemask", lambda *args, **kwargs: (0, 0))
    monkeypatch.setattr(download_manager.curses, "mouseinterval", lambda *args: None)
    monkeypatch.setattr(download_manager.curses, "COLORS", 256, raising=False)
    monkeypatch.setattr(download_manager.curses, "init_pair", lambda *args: None)
    monkeypatch.setattr(download_manager.curses, "color_pair", lambda number: number)

    assert download_manager._curses_download_manager(screen, DummyWorker(), standalone=False) == "BACK"
    assert screen.erase_count >= 2


def test_download_manager_finish_quit_stays_visible_until_downloads_finish(monkeypatch):
    from media_cli.ui import download_manager

    class DummyLock:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class DummyWorker:
        def __init__(self):
            self.lock = DummyLock()
            self.max_concurrent = 2
            self.download_dir = "/tmp"
            self.resume_calls = 0
            self.items_reads = 0
            self._items = [
                DownloadItem(
                    id="finish-quit",
                    title="Finish Quit",
                    episode="Episode 1",
                    destination_path="/tmp/finish-quit.mp4",
                    status=DownloadStatus.PAUSED,
                )
            ]

        @property
        def items(self):
            self.items_reads += 1
            if self.resume_calls and self.items_reads >= 3:
                self._items[0].status = DownloadStatus.COMPLETED
                self._items[0].progress = 100.0
            return self._items

        def start(self):
            return None

        def resume_paused_items(self):
            self.resume_calls += 1
            self._items[0].status = DownloadStatus.QUEUED
            return 1

    class DummyStats:
        def read_network_ssid(self):
            return "Wi-Fi", "test", "wlan0"

        def read_cpu(self):
            return 1.0, 12.0

        def read_mem(self):
            return 1.0, 4.0, 25.0

        def read_disk(self):
            return 10.0, 20.0, 50.0

        def read_temp(self):
            return 40.0

        def read_loadavg(self):
            return "0.1 0.2 0.3"

        def read_uptime(self):
            return "00:01:00"

        def read_net_speed(self):
            return 0.0, "0.0M/s"

    class DummyScreen:
        def __init__(self):
            self.keys = [3, -1, -1, -1]
            self.refresh_count = 0

        def getmaxyx(self):
            return (24, 80)

        def erase(self):
            return None

        def clearok(self, value):
            return None

        def nodelay(self, value):
            return None

        def timeout(self, value):
            return None

        def leaveok(self, value):
            return None

        def refresh(self):
            self.refresh_count += 1

        def getch(self):
            return self.keys.pop(0) if self.keys else -1

        def addstr(self, *args):
            return None

    screen = DummyScreen()
    worker = DummyWorker()
    monkeypatch.setattr(download_manager, "SystemStats", DummyStats)
    monkeypatch.setattr(download_manager, "show_download_exit_dialog", lambda *args: "FINISH_QUIT")
    monkeypatch.setattr(download_manager.curses, "raw", lambda: None, raising=False)
    monkeypatch.setattr(download_manager.curses, "curs_set", lambda *args: None)
    monkeypatch.setattr(download_manager.curses, "use_default_colors", lambda *args: None)
    monkeypatch.setattr(download_manager.curses, "set_escdelay", lambda *args: None)
    monkeypatch.setattr(download_manager.curses, "mousemask", lambda *args, **kwargs: (0, 0))
    monkeypatch.setattr(download_manager.curses, "mouseinterval", lambda *args: None)
    monkeypatch.setattr(download_manager.curses, "COLORS", 256, raising=False)
    monkeypatch.setattr(download_manager.curses, "init_pair", lambda *args: None)
    monkeypatch.setattr(download_manager.curses, "color_pair", lambda number: number)

    assert download_manager._curses_download_manager(screen, worker, standalone=False) == "QUIT"
    assert worker.resume_calls == 1
    assert screen.refresh_count >= 2


def test_download_manager_auto_quit_initial_mode_resumes_and_waits(monkeypatch):
    from media_cli.ui import download_manager

    class DummyLock:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class DummyWorker:
        def __init__(self):
            self.lock = DummyLock()
            self.max_concurrent = 2
            self.download_dir = "/tmp"
            self.resume_calls = 0
            self.items_reads = 0
            self._items = [
                DownloadItem(
                    id="initial-auto-quit",
                    title="Initial Auto Quit",
                    episode="Episode 1",
                    destination_path="/tmp/initial-auto-quit.mp4",
                    status=DownloadStatus.PAUSED,
                )
            ]

        @property
        def items(self):
            self.items_reads += 1
            if self.resume_calls and self.items_reads >= 3:
                self._items[0].status = DownloadStatus.COMPLETED
                self._items[0].progress = 100.0
            return self._items

        def start(self):
            return None

        def resume_paused_items(self):
            self.resume_calls += 1
            self._items[0].status = DownloadStatus.QUEUED
            return 1

    class DummyStats:
        def read_network_ssid(self):
            return "Wi-Fi", "test", "wlan0"

        def read_cpu(self):
            return 1.0, 12.0

        def read_mem(self):
            return 1.0, 4.0, 25.0

        def read_disk(self):
            return 10.0, 20.0, 50.0

        def read_temp(self):
            return 40.0

        def read_loadavg(self):
            return "0.1 0.2 0.3"

        def read_uptime(self):
            return "00:01:00"

        def read_net_speed(self):
            return 0.0, "0.0M/s"

    class DummyScreen:
        def __init__(self):
            self.refresh_count = 0

        def getmaxyx(self):
            return (24, 80)

        def erase(self):
            return None

        def clearok(self, value):
            return None

        def nodelay(self, value):
            return None

        def timeout(self, value):
            return None

        def leaveok(self, value):
            return None

        def refresh(self):
            self.refresh_count += 1

        def getch(self):
            return -1

        def addstr(self, *args):
            return None

    screen = DummyScreen()
    worker = DummyWorker()
    monkeypatch.setattr(download_manager, "SystemStats", DummyStats)
    monkeypatch.setattr(download_manager.curses, "raw", lambda: None, raising=False)
    monkeypatch.setattr(download_manager.curses, "curs_set", lambda *args: None)
    monkeypatch.setattr(download_manager.curses, "use_default_colors", lambda *args: None)
    monkeypatch.setattr(download_manager.curses, "set_escdelay", lambda *args: None)
    monkeypatch.setattr(download_manager.curses, "mousemask", lambda *args, **kwargs: (0, 0))
    monkeypatch.setattr(download_manager.curses, "mouseinterval", lambda *args: None)
    monkeypatch.setattr(download_manager.curses, "COLORS", 256, raising=False)
    monkeypatch.setattr(download_manager.curses, "init_pair", lambda *args: None)
    monkeypatch.setattr(download_manager.curses, "color_pair", lambda number: number)

    assert download_manager._curses_download_manager(screen, worker, standalone=False, auto_quit=True) == "QUIT"
    assert worker.resume_calls == 1
    assert screen.refresh_count >= 1


def test_app_quit_auto_quit_cancel_keeps_app_open(monkeypatch):
    from media_cli.app import orchestrator

    worker_stopped = False

    monkeypatch.setattr(
        "media_cli.media.download_worker.has_active_downloads",
        lambda: (1, 0),
    )
    monkeypatch.setattr(
        "media_cli.ui.download_manager.prompt_download_exit_cli",
        lambda active, queued: "FINISH_QUIT",
    )
    monkeypatch.setattr(
        "media_cli.media.download_worker.get_global_worker",
        lambda: object(),
    )
    monkeypatch.setattr(
        "media_cli.ui.download_manager.run_download_manager",
        lambda worker, standalone=False, auto_quit=False: "BACK",
    )

    def fake_stop_global_worker():
        nonlocal worker_stopped
        worker_stopped = True

    monkeypatch.setattr(
        "media_cli.media.download_worker.stop_global_worker",
        fake_stop_global_worker,
    )

    assert orchestrator._resolve_quit_with_active_downloads() == "SEARCH"
    assert worker_stopped is False


def test_download_manager_cleanup_resets_terminal_modes(monkeypatch):
    from media_cli.ui import download_manager

    writes = []
    mouse_masks = []
    tty_flushes = []

    class DummyStream:
        closed = False

        def write(self, value):
            writes.append(value)

        def flush(self):
            return None

    monkeypatch.setattr(download_manager.curses, "mousemask", lambda mask: mouse_masks.append(mask))
    monkeypatch.setattr(download_manager.curses, "flushinp", lambda: None)
    monkeypatch.setattr(download_manager.termios, "tcflush", lambda fd, queue: tty_flushes.append((fd, queue)))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdin, "fileno", lambda: 42)
    monkeypatch.setattr(sys, "__stdout__", DummyStream())
    monkeypatch.setattr(sys, "stdout", DummyStream())

    download_manager._restore_terminal_input_modes()

    combined = "".join(writes)
    assert mouse_masks == [0]
    assert "\033[?1000l" in combined
    assert "\033[?1006l" in combined
    assert "\033[?1l" in combined
    assert "\033>" in combined
    assert "\033[?2004l" in combined
    assert (42, download_manager.termios.TCIFLUSH) in tty_flushes


def test_download_manager_resize_keeps_cursor_hidden_without_full_clear(monkeypatch):
    from media_cli.ui import download_manager

    events = []

    class DummyScreen:
        def erase(self):
            events.append("erase")

        def clear(self):
            events.append("clear")

        def clearok(self, value):
            events.append(("clearok", value))

    monkeypatch.setattr(download_manager, "_current_terminal_size", lambda stdscr: (33, 111))
    monkeypatch.setattr(download_manager.curses, "resizeterm", lambda h, w: events.append(("resizeterm", h, w)))
    monkeypatch.setattr(download_manager.curses, "curs_set", lambda value: events.append(("curs_set", value)))

    download_manager._handle_resize_event(DummyScreen())

    assert ("resizeterm", 33, 111) in events
    assert ("curs_set", 0) in events
    assert "erase" in events
    assert "clear" not in events
    assert ("clearok", False) in events


def test_download_manager_does_not_erase_whole_screen_each_tick(monkeypatch):
    from media_cli.ui import download_manager

    class DummyLock:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class DummyWorker:
        def __init__(self):
            self.lock = DummyLock()
            self.max_concurrent = 2
            self.download_dir = "/tmp"
            self.items = [
                DownloadItem(
                    id="active",
                    title="Blink Probe",
                    episode="Episode 1",
                    destination_path="/tmp/blink.mp4",
                    status=DownloadStatus.DOWNLOADING,
                    progress=42.0,
                    speed_str="1.0MiB/s",
                    eta_str="12s",
                    logs=["Downloading..."],
                )
            ]

        def start(self):
            return None

    class DummyStats:
        def read_network_ssid(self):
            return "Wi-Fi", "test", "wlan0"

        def read_cpu(self):
            return 1.0, 12.0

        def read_mem(self):
            return 1.0, 4.0, 25.0

        def read_disk(self):
            return 10.0, 20.0, 50.0

        def read_temp(self):
            return 40.0

        def read_loadavg(self):
            return "0.1 0.2 0.3"

        def read_uptime(self):
            return "00:01:00"

        def read_net_speed(self):
            return 0.0, "0.0M/s"

    class DummyScreen:
        def __init__(self):
            self.erase_count = 0
            self.keys = [-1, -1, ord("q")]

        def getmaxyx(self):
            return (24, 80)

        def erase(self):
            self.erase_count += 1

        def clear(self):
            raise AssertionError("steady render tick should not full-clear")

        def clearok(self, value):
            return None

        def nodelay(self, value):
            return None

        def timeout(self, value):
            return None

        def leaveok(self, value):
            return None

        def refresh(self):
            return None

        def getch(self):
            return self.keys.pop(0)

        def addstr(self, *args):
            return None

    def noop(*args, **kwargs):
        return None

    screen = DummyScreen()
    monkeypatch.setattr(download_manager, "SystemStats", DummyStats)
    monkeypatch.setattr(download_manager.curses, "curs_set", noop)
    monkeypatch.setattr(download_manager.curses, "use_default_colors", noop)
    monkeypatch.setattr(download_manager.curses, "set_escdelay", noop)
    monkeypatch.setattr(download_manager.curses, "mousemask", lambda *args, **kwargs: (0, 0))
    monkeypatch.setattr(download_manager.curses, "mouseinterval", noop)
    monkeypatch.setattr(download_manager.curses, "COLORS", 256, raising=False)
    monkeypatch.setattr(download_manager.curses, "init_pair", noop)
    monkeypatch.setattr(download_manager.curses, "color_pair", lambda number: number)

    assert download_manager._curses_download_manager(screen, DummyWorker(), standalone=False) == "BACK"
    assert screen.erase_count <= 1


def test_download_manager_q_dismisses_idle_nonstandalone_manager(monkeypatch):
    from media_cli.ui import download_manager

    class DummyLock:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class DummyWorker:
        def __init__(self):
            self.lock = DummyLock()
            self.max_concurrent = 2
            self.download_dir = "/tmp"
            self.dismissed = False
            self.items = [
                DownloadItem(
                    id="done",
                    title="Done Probe",
                    episode="Episode 1",
                    destination_path="/tmp/done.mp4",
                    status=DownloadStatus.COMPLETED,
                    progress=100.0,
                )
            ]

        def start(self):
            return None

        def dismiss_when_idle(self):
            self.dismissed = True
            return True

    class DummyStats:
        def read_network_ssid(self):
            return "Wi-Fi", "test", "wlan0"

        def read_cpu(self):
            return 1.0, 12.0

        def read_mem(self):
            return 1.0, 4.0, 25.0

        def read_disk(self):
            return 10.0, 20.0, 50.0

        def read_temp(self):
            return 40.0

        def read_loadavg(self):
            return "0.1 0.2 0.3"

        def read_uptime(self):
            return "00:01:00"

        def read_net_speed(self):
            return 0.0, "0.0M/s"

    class DummyScreen:
        def __init__(self):
            self.keys = [ord("q")]

        def getmaxyx(self):
            return (24, 80)

        def erase(self):
            return None

        def clearok(self, value):
            return None

        def nodelay(self, value):
            return None

        def timeout(self, value):
            return None

        def leaveok(self, value):
            return None

        def refresh(self):
            return None

        def getch(self):
            return self.keys.pop(0) if self.keys else -1

        def addstr(self, *args):
            return None

    def noop(*args, **kwargs):
        return None

    worker = DummyWorker()
    monkeypatch.setattr(download_manager, "SystemStats", DummyStats)
    monkeypatch.setattr(download_manager.curses, "curs_set", noop)
    monkeypatch.setattr(download_manager.curses, "use_default_colors", noop)
    monkeypatch.setattr(download_manager.curses, "set_escdelay", noop)
    monkeypatch.setattr(download_manager.curses, "mousemask", lambda *args, **kwargs: (0, 0))
    monkeypatch.setattr(download_manager.curses, "mouseinterval", noop)
    monkeypatch.setattr(download_manager.curses, "COLORS", 256, raising=False)
    monkeypatch.setattr(download_manager.curses, "init_pair", noop)
    monkeypatch.setattr(download_manager.curses, "color_pair", lambda number: number)

    assert download_manager._curses_download_manager(DummyScreen(), worker, standalone=False) == "BACK"
    assert worker.dismissed is True


def test_download_manager_collapses_resize_burst_before_real_key(monkeypatch):
    from media_cli.ui import download_manager

    key_resize = download_manager.curses.KEY_RESIZE

    class DummyScreen:
        def __init__(self):
            self.keys = [key_resize, key_resize, key_resize, ord("q")]

        def getch(self):
            return self.keys.pop(0) if self.keys else -1

    ch, resized = download_manager._read_input_key(DummyScreen())

    assert ch == ord("q")
    assert resized is True


def test_download_manager_clears_content_area_when_opening_logs(monkeypatch):
    from media_cli.ui import download_manager

    class DummyLock:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class DummyWorker:
        def __init__(self):
            self.lock = DummyLock()
            self.max_concurrent = 2
            self.download_dir = "/tmp"
            self.items = [
                DownloadItem(
                    id="done",
                    title="Log Probe",
                    episode="Episode 1",
                    destination_path="/tmp/log-probe.mp4",
                    status=DownloadStatus.COMPLETED,
                    progress=100.0,
                    logs=["Already downloaded."],
                )
            ]

        def start(self):
            return None

    class DummyStats:
        def read_network_ssid(self):
            return "Wi-Fi", "test", "wlan0"

        def read_cpu(self):
            return 1.0, 12.0

        def read_mem(self):
            return 1.0, 4.0, 25.0

        def read_disk(self):
            return 10.0, 20.0, 50.0

        def read_temp(self):
            return 40.0

        def read_loadavg(self):
            return "0.1 0.2 0.3"

        def read_uptime(self):
            return "00:01:00"

        def read_net_speed(self):
            return 0.0, "0.0M/s"

    class DummyScreen:
        def __init__(self):
            self.keys = [10, ord("q"), ord("q")]
            self.writes = []

        def getmaxyx(self):
            return (30, 90)

        def erase(self):
            return None

        def clear(self):
            raise AssertionError("logs transition should not full-clear")

        def clearok(self, value):
            return None

        def nodelay(self, value):
            return None

        def timeout(self, value):
            return None

        def leaveok(self, value):
            return None

        def refresh(self):
            return None

        def getch(self):
            return self.keys.pop(0) if self.keys else -1

        def addstr(self, y, x, text, *args):
            self.writes.append((y, x, str(text)))

    def noop(*args, **kwargs):
        return None

    screen = DummyScreen()
    monkeypatch.setattr(download_manager, "SystemStats", DummyStats)
    monkeypatch.setattr(download_manager.curses, "curs_set", noop)
    monkeypatch.setattr(download_manager.curses, "use_default_colors", noop)
    monkeypatch.setattr(download_manager.curses, "set_escdelay", noop)
    monkeypatch.setattr(download_manager.curses, "mousemask", lambda *args, **kwargs: (0, 0))
    monkeypatch.setattr(download_manager.curses, "mouseinterval", noop)
    monkeypatch.setattr(download_manager.curses, "COLORS", 256, raising=False)
    monkeypatch.setattr(download_manager.curses, "init_pair", noop)
    monkeypatch.setattr(download_manager.curses, "color_pair", lambda number: number)

    assert download_manager._curses_download_manager(screen, DummyWorker(), standalone=False) == "BACK"

    table_hdr_row = 6
    blank_content_writes = [
        text for y, _x, text in screen.writes
        if y == table_hdr_row + 1 and text.strip() == "" and len(text) >= 80
    ]
    assert blank_content_writes


def test_download_manager_meter_value_width_is_stable_across_cpu_thresholds():
    from media_cli.ui.download_manager import _meter_value_width

    low_cpu_values = ["1.0%", "9.9%", "10.0%"]
    wide_cpu_values = ["1.0% [9%]", "9.9% [99%]", "10.0% [100%]"]

    assert len({_meter_value_width("medium", low_cpu_values) for _ in low_cpu_values}) == 1
    assert len({_meter_value_width("wide", wide_cpu_values) for _ in wide_cpu_values}) == 1
    assert _meter_value_width("medium", ["10.0%"]) >= len("10.0%")
    assert _meter_value_width("wide", ["10.0% [100%]"]) >= len("10.0% [100%]")


def test_download_manager_meter_value_width_accepts_scalar_values():
    from media_cli.ui.download_manager import _meter_value_width

    assert _meter_value_width("medium", 10) >= len("10")


def test_system_cpu_reading_is_throttled_and_smoothed(monkeypatch):
    from media_cli.ui import download_manager

    clock = iter([0.0, 0.0, 0.2, 0.8, 1.8])
    stats_lines = iter([
        "cpu  100 0 0 900 0 0 0\n",
        "cpu  100 0 0 900 0 0 0\n",
        "cpu  200 0 0 900 0 0 0\n",
        "cpu  300 0 0 900 0 0 0\n",
    ])
    open_count = 0

    class FakeTimes:
        user = 0.0
        system = 0.0
        children_user = 0.0
        children_system = 0.0

    class FakeFile:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def readline(self):
            return next(stats_lines)

    def fake_open(path, *args, **kwargs):
        nonlocal open_count
        if path == "/proc/stat":
            open_count += 1
            return FakeFile()
        raise AssertionError(path)

    monkeypatch.setattr(download_manager.time, "time", lambda: next(clock))
    monkeypatch.setattr(download_manager.os, "times", lambda: FakeTimes())
    monkeypatch.setattr(download_manager.os, "cpu_count", lambda: 1)
    monkeypatch.setattr("builtins.open", fake_open)

    stats = download_manager.SystemStats()
    first = stats.read_cpu()
    second = stats.read_cpu()
    third = stats.read_cpu()

    assert first == second
    assert open_count == 2
    assert 0.0 < third[1] < 100.0
