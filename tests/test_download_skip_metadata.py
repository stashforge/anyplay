import shutil
import subprocess

import pytest

from media_cli.media import aniskip, download, episode_identity

ENDING = {"type": "ed", "start": 1, "end": 2}
OPENING = {"type": "op", "start": 0, "end": 1, "episode_length": 3}


def test_provider_ending_is_saved_without_aniskip(monkeypatch):
    monkeypatch.setattr(aniskip, "fetch_skip_times", lambda *a: pytest.fail("Unexpected AniSkip request"))
    assert download.resolve_download_skips({"skip_intervals": [ENDING]}, 42, "2")[0]["source"] == "provider"


def test_aniskip_fallback_uses_episode_number(monkeypatch):
    def fetch(mal_id, episode):
        assert (mal_id, episode) == (42, 2)
        return [OPENING]
    monkeypatch.setattr(aniskip, "fetch_skip_times", fetch)
    assert download.resolve_download_skips({}, 42, "Episode 2") == [OPENING]


def test_aniskip_failure_leaves_download_without_timings(monkeypatch):
    def fetch(*args):
        raise TimeoutError

    monkeypatch.setattr(aniskip, "fetch_skip_times", fetch)
    assert download.resolve_download_skips({}, 42, "2") == []


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="FFmpeg tools unavailable")
def test_saved_skips_survive_rename_and_validate_runtime(tmp_path):
    path = tmp_path / "episode.mkv"
    tags = download.anyplay_metadata_args(episode="2", skip_intervals=[OPENING])
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=s=32x32:d=3",
         "-c:v", "ffv1", *tags, str(path)], check=True,
    )
    renamed = path.rename(tmp_path / "renamed.mkv")
    assert episode_identity.read_episode_identity(renamed).episode == "2"
    assert episode_identity.read_episode_skips(renamed)[0]["type"] == "op"


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="FFmpeg tools unavailable")
def test_metadata_remux_preserves_original_when_cancelled(tmp_path):
    path = tmp_path / "episode.mkv"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=s=32x32:d=3",
         "-c:v", "ffv1", str(path)], check=True,
    )
    original = path.read_bytes()
    tags = download.anyplay_metadata_args(episode="2", skip_intervals=[OPENING])
    assert not download.embed_download_metadata(path, tags, cancel_check=lambda: True)
    assert path.read_bytes() == original
    assert download.embed_download_metadata(path, tags)
    assert episode_identity.read_episode_skips(path)[0]["type"] == "op"


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="FFmpeg tools unavailable")
def test_worker_mkv_timings_reach_offline_player(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from media_cli.media import download_worker
    from media_cli.playback.desktop import play_desktop

    source = tmp_path / "source.mkv"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=s=32x32:d=3",
         "-c:v", "ffv1", str(source)], check=True,
    )
    target = tmp_path / "download.mkv"
    item = download_worker.DownloadItem(
        id="show:2", title="Show", episode="Episode 2", url=str(source),
        destination_path=str(target), provider_skips=[ENDING], show_metadata={"malId": 42},
    )
    worker = download_worker.DownloadWorker(download_dir=str(tmp_path))
    monkeypatch.setattr(aniskip, "fetch_skip_times", lambda *a: pytest.fail("Unexpected network lookup"))
    worker._download_ffmpeg(item)
    assert item.status == download_worker.DownloadStatus.COMPLETED
    captured = {}
    ipc = SimpleNamespace(
        props={}, load=lambda *args, **kwargs: captured.update(kwargs),
        wait_for_playback=lambda *args: ("QUIT", 0),
    )
    play_desktop(
        ipc, "Show", "2", {"link": str(target), "is_local": True},
        get_resume_time=lambda *a: 0, get_preferred_mirror=lambda *a: {},
        update_stream_info=lambda info: None, mal_id=42,
    )
    assert [row["type"] for row in captured["skip_intervals"]] == ["ed"]
    assert "skip_lookup" not in captured
