"""Select only installed engines without sacrificing external audio/subtitles."""

import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from media_cli.core.api import ProviderDependencyError


def tools(monkeypatch, installed):
    monkeypatch.setattr("shutil.which", lambda name: f"/test/{name}" if name in installed else None)


@pytest.mark.parametrize("installed,url,worker,expected,hint", [
    ({"ffmpeg", "yt-dlp"}, "https://cdn.example/video.mp4", False, "yt-dlp", False),
    ({"ffmpeg", "yt-dlp"}, "https://cdn.example/video.mp4", True, "ffmpeg", False),
    ({"ffmpeg", "yt-dlp"}, "https://cdn.example/video.m3u8", False, "ffmpeg", False),
    ({"ffmpeg"}, "https://cdn.example/video.mp4", False, "ffmpeg", False),
    ({"yt-dlp"}, "https://cdn.example/video.m3u8", True, "yt-dlp", True),
    ({"aria2c"}, "https://cdn.example/video.mp4", True, "aria2c", True),
])
def test_download_plan_preserves_capable_alternatives(monkeypatch, installed, url, worker, expected, hint):
    from media_cli.media.download_requirements import plan_download

    tools(monkeypatch, installed)
    plan = plan_download("auto", url, worker=worker)
    assert plan.engine == expected
    assert bool(plan.hints) is hint


@pytest.mark.parametrize("installed", [set(), {"aria2c"}])
def test_hls_requires_a_real_stream_downloader(monkeypatch, installed):
    from media_cli.media.download_requirements import plan_download

    tools(monkeypatch, installed)
    with pytest.raises(ProviderDependencyError, match="ffmpeg.*yt-dlp"):
        plan_download("auto", "https://cdn.example/video.m3u8", worker=True)


@pytest.mark.parametrize("engine", ["ffmpeg", "yt-dlp", "aria2c"])
def test_explicit_missing_engine_is_not_silently_replaced(monkeypatch, engine):
    from media_cli.media.download_requirements import plan_download

    tools(monkeypatch, {"ffmpeg", "yt-dlp", "aria2c"} - {engine})
    with pytest.raises(ProviderDependencyError, match=engine):
        plan_download(engine, "https://cdn.example/video.mp4", worker=True)


def test_worker_external_tracks_need_ffmpeg_even_with_ytdlp(monkeypatch):
    from media_cli.media.download_requirements import plan_download

    tools(monkeypatch, {"yt-dlp"})
    with pytest.raises(ProviderDependencyError, match="ffmpeg"):
        plan_download("auto", "https://cdn.example/video.mp4", embed_subtitles=True, worker=True)


def test_classic_can_remux_ytdlp_subtitles_with_ffmpeg(monkeypatch):
    from media_cli.media.download_requirements import plan_download

    tools(monkeypatch, {"yt-dlp", "ffmpeg"})
    assert plan_download("yt-dlp", "https://cdn.example/video.mp4", embed_subtitles=True).engine == "yt-dlp"


def test_classic_quiet_missing_tool_raises_before_creating_files(monkeypatch, tmp_path):
    from media_cli.media.download import download_episode

    tools(monkeypatch, set())
    with pytest.raises(ProviderDependencyError, match="[Ii]nstall"):
        download_episode("Title", "1", {"link": "https://cdn.example/video.mp4"},
                         download_dir=str(tmp_path / "output"), quiet=True)
    assert not (tmp_path / "output").exists()


def test_worker_setup_failure_is_visible_and_does_not_retry_or_create_proxy(monkeypatch, tmp_path):
    from media_cli.media import download_worker as module

    tools(monkeypatch, set())
    proxy = Mock(side_effect=AssertionError("Tool check must precede proxy setup"))
    monkeypatch.setattr(module, "_prepare_bound_network", proxy)
    worker = module.DownloadWorker(download_dir=str(tmp_path))
    item = module.DownloadItem(id="missing", title="Title", episode="1", url="https://cdn.example/video.m3u8",
                               destination_path=str(tmp_path / "output.mkv"), status=module.DownloadStatus.DOWNLOADING)
    try:
        worker.active_items[item.id] = item
        worker._process_item(item)
        assert item.status == module.DownloadStatus.FAILED
        assert item.retry_count == 0
        assert "install" in item.error.lower()
        assert any("install" in message.lower() for message in item.logs)
        assert item.id not in worker.active_items
        assert not item.failed_mirrors
        proxy.assert_not_called()
    finally:
        worker.stop()


def test_worker_does_not_require_ffmpeg_for_unselected_subtitles(monkeypatch, tmp_path):
    from media_cli.media import download_worker as module

    tools(monkeypatch, {"yt-dlp"})
    monkeypatch.setattr(module, "_prepare_bound_network", lambda *a: None)
    played = []
    monkeypatch.setattr(module.DownloadWorker, "_download_ytdlp", lambda self, item: played.append(item.id))
    worker = module.DownloadWorker(download_dir=str(tmp_path))
    item = module.DownloadItem(id="language", title="Title", episode="1", url="https://cdn.example/video.mp4",
                               destination_path=str(tmp_path / "output.mkv"), status=module.DownloadStatus.DOWNLOADING,
                               subtitles=[{"url": "https://cdn.example/en.vtt", "language": "en"}], download_sub_langs="ja")
    try:
        worker._process_item(item)
        assert played == ["language"]
        assert not item.error
    finally:
        worker.stop()


def test_source_requirement_failure_stops_without_mirror_retry(monkeypatch, tmp_path):
    from media_cli.media import download_worker as module
    from media_cli.services.requirements import MissingPythonDependency

    failure = MissingPythonDependency(("bs4",), "Native extraction")
    worker = module.DownloadWorker(download_dir=str(tmp_path))
    item = module.DownloadItem(id="resolve", title="Title", episode="1", destination_path=str(tmp_path / "output.mkv"),
                               status=module.DownloadStatus.DOWNLOADING, resolve_fn=Mock(side_effect=failure))
    try:
        worker._process_item(item)
        assert item.status == module.DownloadStatus.FAILED
        assert item.retry_count == 0
        assert "beautifulsoup4" in item.error
        assert any("beautifulsoup4" in line for line in item.logs)
    finally:
        worker.stop()


def test_aria2_hls_capability_error_precedes_tool_install_hint(monkeypatch):
    from media_cli.media.download_requirements import plan_download

    tools(monkeypatch, set())
    with pytest.raises(ProviderDependencyError, match="cannot assemble"):
        plan_download("aria2c", "https://cdn.example/video.m3u8", worker=True)


def test_direct_file_fallback_rejects_disguised_playlist_output(monkeypatch, tmp_path):
    from media_cli.media import download_worker as module

    path = tmp_path / "output.mkv"

    def start(*a, **kw):
        path.write_bytes(b"#EXTM3U\n#EXTINF:2,\nsegment.ts\n")
        return SimpleNamespace(stdout=io.StringIO(""), returncode=0, wait=lambda: None, poll=lambda: 0)

    monkeypatch.setattr(module.subprocess, "Popen", start)
    worker = module.DownloadWorker(download_dir=str(tmp_path))
    item = module.DownloadItem(id="playlist", title="Title", episode="1", url="https://cdn.example/stream",
                               destination_path=str(path), status=module.DownloadStatus.DOWNLOADING, engine="aria2c")
    try:
        worker._download_aria2c(item)
        assert item.status == module.DownloadStatus.FAILED
        assert "playlist" in item.error
        assert not path.exists()
        assert not item.output_files
    finally:
        worker.stop()


def test_unsupported_engine_gives_configuration_guidance_not_mirror_retry(monkeypatch):
    from media_cli.media.download_requirements import plan_download

    tools(monkeypatch, {"ffmpeg", "yt-dlp"})
    with pytest.raises(ProviderDependencyError, match="Choose"):
        plan_download("unknown-engine", "https://cdn.example/video.mp4")
