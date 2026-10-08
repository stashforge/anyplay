import os
import tempfile
from unittest.mock import MagicMock, patch
import pytest

from media_cli.media.download import download_episode


def test_download_episode_multi_audio_ffmpeg_command():
    stream = {
        "link": "https://stream.example.com/master.m3u8",
        "audio_tracks": [
            {"url": "https://stream.example.com/audio-ja.m3u8", "label": "Japanese", "language": "ja", "default": True},
            {"url": "https://stream.example.com/audio-en.m3u8", "label": "English", "language": "en", "default": False},
            {"url": "https://stream.example.com/audio-es.m3u8", "label": "Spanish", "language": "es", "default": False},
        ],
        "subtitles": [
            {"url": "https://stream.example.com/sub-en.vtt", "label": "English", "language": "en"},
        ],
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        captured_cmd = []

        def mock_popen(cmd, *args, **kwargs):
            nonlocal captured_cmd
            captured_cmd = cmd
            os.makedirs(os.path.dirname(cmd[-1]), exist_ok=True)
            with open(cmd[-1], "wb") as fh:
                fh.write(b"fake-video")
            mock_proc = MagicMock()
            mock_proc.wait.return_value = 0
            mock_proc.returncode = 0
            return mock_proc

        with patch("subprocess.Popen", side_effect=mock_popen), \
             patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("media_cli.media.download.download_sidecar_subtitles") as mock_sidecar:

            # Test 1: Download with all languages (multi)
            ok = download_episode(
                "Slime S04",
                1,
                stream,
                download_dir=tmpdir,
                downloader="ffmpeg",
                download_audio_langs="multi",
                download_sub_langs="multi",
                subtitle_mode="sidecar",
                quiet=True,
            )

            assert ok is True
            # Verify sidecar subtitles called
            assert mock_sidecar.called

            # Check FFmpeg inputs: 0 is video, 1 is ja, 2 is en, 3 is es
            assert "-i" in captured_cmd
            assert "https://stream.example.com/master.m3u8" in captured_cmd
            assert "https://stream.example.com/audio-ja.m3u8" in captured_cmd
            assert "https://stream.example.com/audio-en.m3u8" in captured_cmd
            assert "https://stream.example.com/audio-es.m3u8" in captured_cmd

            # Check maps and metadata
            assert "-map" in captured_cmd
            assert "0:v:0" in captured_cmd
            assert "1:a:0" in captured_cmd
            assert "2:a:0" in captured_cmd
            assert "3:a:0" in captured_cmd
            assert "language=jpn" in captured_cmd
            assert "language=eng" in captured_cmd
            assert "language=spa" in captured_cmd


def test_download_episode_filtered_audio_ffmpeg():
    stream = {
        "link": "https://stream.example.com/master.m3u8",
        "audio_tracks": [
            {"url": "https://stream.example.com/audio-ja.m3u8", "label": "Japanese", "language": "ja", "default": True},
            {"url": "https://stream.example.com/audio-en.m3u8", "label": "English", "language": "en", "default": False},
            {"url": "https://stream.example.com/audio-es.m3u8", "label": "Spanish", "language": "es", "default": False},
        ],
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        captured_cmd = []

        def mock_popen(cmd, *args, **kwargs):
            nonlocal captured_cmd
            captured_cmd = cmd
            os.makedirs(os.path.dirname(cmd[-1]), exist_ok=True)
            with open(cmd[-1], "wb") as fh:
                fh.write(b"fake-video")
            mock_proc = MagicMock()
            mock_proc.wait.return_value = 0
            mock_proc.returncode = 0
            return mock_proc

        with patch("subprocess.Popen", side_effect=mock_popen), \
             patch("shutil.which", return_value="/usr/bin/ffmpeg"):

            # User only wants English audio
            ok = download_episode(
                "Slime S04",
                1,
                stream,
                download_dir=tmpdir,
                downloader="ffmpeg",
                download_audio_langs="eng",
                quiet=True,
            )

            assert ok is True
            # Only English audio should be included
            assert "https://stream.example.com/audio-en.m3u8" in captured_cmd
            assert "https://stream.example.com/audio-ja.m3u8" not in captured_cmd
            assert "https://stream.example.com/audio-es.m3u8" not in captured_cmd
            assert "language=eng" in captured_cmd


def test_download_episode_embedded_subs_mkv():
    stream = {
        "link": "https://stream.example.com/video.mp4",
        "subtitles": [
            {"url": "https://stream.example.com/en.vtt", "label": "English", "language": "en"},
            {"url": "https://stream.example.com/es.vtt", "label": "Spanish", "language": "es"},
        ],
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        captured_cmd = []

        def mock_popen(cmd, *args, **kwargs):
            nonlocal captured_cmd
            captured_cmd = cmd
            os.makedirs(os.path.dirname(cmd[-1]), exist_ok=True)
            with open(cmd[-1], "wb") as fh:
                fh.write(b"fake-video")
            mock_proc = MagicMock()
            mock_proc.wait.return_value = 0
            mock_proc.returncode = 0
            return mock_proc

        dummy_sub = os.path.join(tmpdir, "sub.en.vtt")
        with open(dummy_sub, "w") as f:
            f.write("WEBVTT\n")

        with patch("subprocess.Popen", side_effect=mock_popen), \
             patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("media_cli.media.download.download_sidecar_subtitles", return_value=[dummy_sub]):

            ok = download_episode(
                "Slime S04",
                1,
                stream,
                download_dir=tmpdir,
                downloader="ffmpeg",
                subtitle_mode="embed",
                download_sub_langs="eng",
                quiet=True,
            )

            assert ok is True
            # Output should be .mkv when embedding subtitles
            output_file = captured_cmd[-1]
            assert output_file.endswith(".mkv")
            assert ".anyplay-partials" in output_file
            # Local subtitle input mapped (no remote URL to avoid 403 Forbidden)
            assert dummy_sub in captured_cmd
            assert "1:s:0" in captured_cmd
            assert "language=eng" in captured_cmd


def test_download_episode_ffmpeg_commits_only_after_partial_success():
    stream = {"link": "https://stream.example.com/master.m3u8"}

    with tempfile.TemporaryDirectory() as tmpdir:
        captured_cmd = []

        def mock_popen(cmd, *args, **kwargs):
            nonlocal captured_cmd
            captured_cmd = cmd
            os.makedirs(os.path.dirname(cmd[-1]), exist_ok=True)
            with open(cmd[-1], "wb") as fh:
                fh.write(b"complete-video")
            mock_proc = MagicMock()
            mock_proc.wait.return_value = 0
            mock_proc.returncode = 0
            return mock_proc

        with patch("subprocess.Popen", side_effect=mock_popen), \
             patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            ok = download_episode(
                "Atomic Show",
                7,
                stream,
                download_dir=tmpdir,
                downloader="ffmpeg",
                quiet=True,
            )

        final_path = os.path.join(tmpdir, "Atomic Show", "Atomic Show - Episode 7.mp4")
        assert ok is True
        assert captured_cmd[-1] != final_path
        assert ".anyplay-partials" in captured_cmd[-1]
        assert os.path.exists(final_path)
        assert not os.path.exists(captured_cmd[-1])


def test_download_episode_yt_dlp_with_embedded_subs_remux():
    stream = {
        "link": "https://stream.example.com/video.mp4",
        "subtitles": [
            {"url": "https://stream.example.com/en.vtt", "label": "English", "language": "en"},
        ],
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        captured_cmd = []

        def mock_popen(cmd, *args, **kwargs):
            nonlocal captured_cmd
            captured_cmd = cmd
            # Simulate yt-dlp creating the output file
            temp_path = cmd[cmd.index("-o") + 1]
            with open(temp_path, "wb") as f:
                f.write(b"fake-video-from-ytdlp")
            mock_proc = MagicMock()
            mock_proc.wait.return_value = 0
            mock_proc.returncode = 0
            return mock_proc

        dummy_sub_file = os.path.join(tmpdir, "sub.en.vtt")
        with open(dummy_sub_file, "w") as f:
            f.write("WEBVTT\n")

        with patch("subprocess.Popen", side_effect=mock_popen), \
             patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("media_cli.media.download.download_sidecar_subtitles", return_value=[dummy_sub_file]) as mock_sidecar, \
             patch("media_cli.media.download.remux_subtitles_into_mkv", return_value=True) as mock_remux:

            ok = download_episode(
                "Slime S04",
                1,
                stream,
                download_dir=tmpdir,
                downloader="yt-dlp",
                subtitle_mode="embed",
                download_sub_langs="eng",
                quiet=True,
            )

            assert ok is True
            # yt-dlp was called
            assert captured_cmd[0] == "yt-dlp"
            assert "-o" in captured_cmd
            temp_file = captured_cmd[captured_cmd.index("-o") + 1]
            assert temp_file.endswith(".temp.mp4")

            # Sidecar downloader was called to fetch subs for remuxing
            assert mock_sidecar.called

            # Remuxer was called with temp video and sidecar files
            assert mock_remux.called
            call_args = mock_remux.call_args[0]
            assert call_args[0] == temp_file
            assert call_args[1] == [dummy_sub_file]
            assert call_args[2].endswith(".mkv")
