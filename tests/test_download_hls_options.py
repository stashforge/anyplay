from types import SimpleNamespace

import pytest

from media_cli.media import hls


@pytest.mark.parametrize("url", [
    "https://cdn.test/master.m3u8?token=private",
    "http://127.0.0.1:1234/secret.m3u8",
])
def test_hls_options_allow_extensionless_subtitle_segments(monkeypatch, url):
    monkeypatch.setattr(hls, "_ffmpeg_has_extension_picky", lambda: True)
    assert hls.ffmpeg_hls_input_args(url) == [
        "-allowed_extensions", "ALL", "-extension_picky", "0",
    ]


def test_non_hls_inputs_do_not_probe_or_receive_hls_options(monkeypatch):
    def unexpected():
        pytest.fail("Must not probe FFmpeg for non-HLS inputs")
    monkeypatch.setattr(hls, "_ffmpeg_has_extension_picky", unexpected)
    assert hls.ffmpeg_hls_input_args("https://cdn.test/video.mp4") == []
    assert hls.ffmpeg_hls_input_args("/tmp/subtitle.vtt") == []


def test_older_ffmpeg_does_not_receive_unknown_option(monkeypatch):
    monkeypatch.setattr(hls, "_ffmpeg_has_extension_picky", lambda: False)
    assert hls.ffmpeg_hls_input_args("https://cdn.test/master.m3u8") == []


def test_ffmpeg_capability_probe_is_cached_and_captured(monkeypatch):
    calls = []
    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return SimpleNamespace(returncode=0, stdout=" -extension_picky <boolean>", stderr="")
    monkeypatch.setattr(hls.subprocess, "run", run)
    hls._ffmpeg_has_extension_picky.cache_clear()
    try:
        assert hls._ffmpeg_has_extension_picky()
        assert hls._ffmpeg_has_extension_picky()
        assert len(calls) == 1
        assert calls[0][1]["capture_output"]
        assert calls[0][1]["timeout"] == 3
    finally:
        hls._ffmpeg_has_extension_picky.cache_clear()
