from media_cli.media import download_worker


def test_ffmpeg_diagnostic_removes_credentials_urls_and_controls():
    value = download_worker._safe_ffmpeg_error(
        "\x1b[31mHTTP error 403 for https://cdn.test/private?token=secret\nAuthorization: Bearer secret"
    )
    assert "403" in value
    assert "secret" not in value
    assert "cdn.test" not in value
    assert "\x1b" not in value
    assert "\n" not in value


def test_ffmpeg_diagnostic_bounds_line_length():
    assert len(download_worker._safe_ffmpeg_error("x" * 10000)) <= 300


def test_real_ffmpeg_failure_is_retained_in_item_logs(tmp_path):
    import shutil
    import pytest

    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg unavailable")
    source = tmp_path / "invalid.mkv"
    source.write_bytes(b"invalid video")
    item = download_worker.DownloadItem(
        id="demo:1", title="Demo", episode="1", url=str(source),
        destination_path=str(tmp_path / "output.mkv"),
    )
    worker = download_worker.DownloadWorker(download_dir=str(tmp_path))
    worker._download_ffmpeg(item)
    assert item.status == download_worker.DownloadStatus.FAILED
    assert any(line.startswith("FFmpeg: ") for line in item.logs)
    assert "ffmpeg exited with code" in item.error
    assert len(item.error) > len("ffmpeg exited with code 183")
