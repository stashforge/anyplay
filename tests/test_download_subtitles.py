import os
import tempfile
from unittest.mock import patch
import pytest

from media_cli.media.subtitles import (
    detect_subtitle_format,
    download_sidecar_subtitles,
)


def test_detect_subtitle_format():
    # URL extension detection
    assert detect_subtitle_format("http://example.com/subs.vtt", b"") == ".vtt"
    assert detect_subtitle_format("http://example.com/subs.srt", b"") == ".srt"
    assert detect_subtitle_format("http://example.com/subs.ass", b"") == ".ass"

    # Header content detection
    assert detect_subtitle_format("http://example.com/api?file=123", b"WEBVTT\n1\n00:00:01.000 --> 00:00:04.000") == ".vtt"
    assert detect_subtitle_format("http://example.com/api?file=123", b"[Script Info]\nTitle: Default") == ".ass"
    assert detect_subtitle_format("http://example.com/api?file=123", b"1\n00:00:01,000 --> 00:00:04,000\nHello") == ".srt"


def test_download_sidecar_subtitles():
    subtitles = [
        {"url": "http://example.com/en.vtt", "label": "English", "language": "en"},
        {"url": "http://example.com/en-cc.vtt", "label": "English [CC]", "language": "en"},
        {"url": "http://example.com/ja.srt", "label": "Japanese", "language": "ja"},
        {"url": "http://example.com/es.vtt", "label": "Spanish", "language": "es"},
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        base_path = os.path.join(tmpdir, "Show - Episode 01")

        def mock_fetch(url, headers=None, referer=None):
            if "en" in url:
                return b"WEBVTT\n00:00:01.000 --> 00:00:02.000\nHi"
            if "ja" in url:
                return b"1\n00:00:01,000 --> 00:00:02,000\nKonnichiwa"
            return b"WEBVTT\n00:00:01.000 --> 00:00:02.000\nHola"

        with patch("media_cli.media.subtitles.fetch_subtitle_bytes", side_effect=mock_fetch):
            # Test 1: multi downloads all
            saved = download_sidecar_subtitles(subtitles, base_path, requested_langs="multi", quiet=True)
            assert len(saved) == 4
            assert os.path.exists(f"{base_path}.en.vtt")
            assert os.path.exists(f"{base_path}.en-2.vtt")
            assert os.path.exists(f"{base_path}.ja.srt")
            assert os.path.exists(f"{base_path}.es.vtt")

            # Clean up files
            for p in saved:
                os.remove(p)

            # Test 2: specific language filter
            saved = download_sidecar_subtitles(subtitles, base_path, requested_langs="eng", quiet=True)
            assert len(saved) == 2
            assert os.path.exists(f"{base_path}.en.vtt")
            assert os.path.exists(f"{base_path}.en-2.vtt")
            assert not os.path.exists(f"{base_path}.ja.srt")

            # Test 3: user opted out with 'none'
            saved_none = download_sidecar_subtitles(subtitles, base_path, requested_langs="none", quiet=True)
            assert saved_none == []


def test_offline_scanner_ignores_sidecar_subtitles():
    from media_cli.app.downloads import VIDEO_EXTS, extract_offline_episode_info

    # Ensure .vtt, .srt, .ass are not treated as video files
    assert ".vtt" not in VIDEO_EXTS
    assert ".srt" not in VIDEO_EXTS
    assert ".ass" not in VIDEO_EXTS

    # Ensure extract_offline_episode_info correctly extracts the video file
    video_file = "/path/Slime S04/Slime S04 - Episode 1.mp4"
    prim, sec, clean_id, label = extract_offline_episode_info(video_file)
    assert prim == 1.0
    assert clean_id == "1"


def test_remux_subtitles_into_mkv():
    from media_cli.media.subtitles import remux_subtitles_into_mkv

    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, "input.mp4")
        with open(video_path, "wb") as f:
            f.write(b"fake-video-content")

        sub_path = os.path.join(tmpdir, "input.en.vtt")
        with open(sub_path, "w") as f:
            f.write("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nHello")

        out_mkv = os.path.join(tmpdir, "output.mkv")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            # Create dummy output file so getsize > 0 passes
            with open(out_mkv, "wb") as f:
                f.write(b"fake-mkv")

            success = remux_subtitles_into_mkv(video_path, [sub_path], out_mkv, quiet=True)
            assert success is True

            cmd = mock_run.call_args[0][0]
            assert cmd[0] == "ffmpeg"
            assert "-i" in cmd
            assert video_path in cmd
            assert sub_path in cmd
            assert "-c" in cmd
            assert "copy" in cmd
            assert "-map" in cmd
            assert out_mkv in cmd


