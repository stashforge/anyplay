"""Read-only embedded download identity, independent of filenames."""

import json
import shutil
import subprocess
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

import pytest


@pytest.fixture
def reader(monkeypatch, tmp_path):
    from media_cli.media import episode_identity

    episode_identity.clear_identity_cache()
    path = tmp_path / "renamed.mkv"
    path.write_bytes(b"video")
    probe = Mock(return_value=b'{"format":{"tags":{"ANYPLAY_TITLE":"Example","ANYPLAY_EPISODE":"31 [463]","ANYPLAY_TITLE_ID":"title-id","ANYPLAY_EPISODE_ID":"episode-id"}}}')
    process = Mock(returncode=0)
    monkeypatch.setattr(episode_identity.shutil, "which", lambda _: "/fake/ffprobe")
    monkeypatch.setattr(episode_identity.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(episode_identity, "read_bounded_process_stdout", probe)
    yield episode_identity, path, probe, process
    episode_identity.clear_identity_cache()


def test_reads_uppercase_tags_into_immutable_identity(reader):
    module, path, probe, _ = reader
    identity = module.read_episode_identity(path)
    assert (identity.title, identity.episode, identity.title_id, identity.episode_id) == (
        "Example", "31 [463]", "title-id", "episode-id",
    )
    with pytest.raises(FrozenInstanceError):
        identity.episode = "99"
    assert path.read_bytes() == b"video"
    probe.assert_called_once()
    args = module.subprocess.Popen.call_args.args[0]
    assert args[-1] == str(path)
    assert "-protocol_whitelist" in args and "file" in args
    assert module.subprocess.Popen.call_args.kwargs["stderr"] == subprocess.DEVNULL
    assert probe.call_args.kwargs["max_bytes"] == 65536
    assert probe.call_args.kwargs["timeout"] == 3


def test_unchanged_files_are_probed_once_and_changes_invalidate(reader):
    module, path, probe, _ = reader
    first = module.read_episode_identity(path)
    assert module.read_episode_identity(path) is first
    assert probe.call_count == 1
    path.write_bytes(b"updated video")
    assert module.read_episode_identity(path) == first
    assert probe.call_count == 2


def test_rename_preserves_embedded_identity(reader):
    module, path, _, _ = reader
    first = module.read_episode_identity(path)
    renamed = path.rename(path.with_name("nothing-to-do-with-episodes.mkv"))
    assert module.read_episode_identity(renamed) == first


def test_changed_file_during_probe_is_not_assigned_old_identity(reader):
    module, path, probe, _ = reader
    original = probe.return_value

    def replace_during_probe(*args, **kwargs):
        path.write_bytes(b"replacement video")
        return original

    probe.side_effect = replace_during_probe
    assert module.read_episode_identity(path) is None
    probe.side_effect = None
    assert module.read_episode_identity(path).episode_id == "episode-id"
    assert probe.call_count == 2


def test_partial_tags_do_not_infer_missing_identity(reader):
    module, path, probe, _ = reader
    probe.return_value = b'{"format":{"tags":{"anyplay_title":"Example"}}}'
    identity = module.read_episode_identity(path)
    assert identity.title == "Example"
    assert (identity.episode, identity.title_id, identity.episode_id) == ("", "", "")


@pytest.mark.parametrize("data", [{}, [], {"format": []}, {"format": {"tags": []}},
                                 {"format": {"tags": {"title": "Unrelated"}}}])
def test_no_anyplay_tags_is_a_cached_miss(reader, data):
    module, path, probe, _ = reader
    probe.return_value = json.dumps(data).encode()
    assert module.read_episode_identity(path) is None
    assert module.read_episode_identity(path) is None
    assert probe.call_count == 1


@pytest.mark.parametrize("tags", [
    {"anyplay_episode": 31}, {"anyplay_episode": "x" * 4097},
    {"anyplay_episode": "31\x1b[0m"}, {"anyplay_episode": "31", "ANYPLAY_EPISODE": "32"},
])
def test_invalid_or_conflicting_tags_are_not_identity(reader, tags):
    module, path, probe, _ = reader
    probe.return_value = json.dumps({"format": {"tags": tags}}).encode()
    assert module.read_episode_identity(path) is None


@pytest.mark.parametrize("error", [OSError("missing"), subprocess.TimeoutExpired("ffprobe", 3),
                                  ValueError("oversized output"), RuntimeError("reader failed")])
def test_probe_failures_fall_back_without_printing_and_can_retry(reader, error, capsys):
    module, path, probe, _ = reader
    probe.side_effect = error
    assert module.read_episode_identity(path) is None
    probe.side_effect = None
    assert module.read_episode_identity(path).episode_id == "episode-id"
    assert probe.call_count == 2
    assert capsys.readouterr() == ("", "")


def test_nonzero_exit_and_bad_json_are_not_cached(reader):
    module, path, probe, process = reader
    process.returncode = 1
    assert module.read_episode_identity(path) is None
    process.returncode = 0
    original = probe.return_value
    probe.return_value = b"not json"
    assert module.read_episode_identity(path) is None
    probe.return_value = original
    assert module.read_episode_identity(path) is not None
    assert probe.call_count == 3


def test_missing_probe_or_nonfile_uses_fallback(reader, monkeypatch):
    module, path, probe, _ = reader
    monkeypatch.setattr(module.shutil, "which", lambda _: None)
    assert module.read_episode_identity(path) is None
    assert module.read_episode_identity(path.parent) is None
    assert module.read_episode_identity(path.with_name("missing.mkv")) is None
    assert module.read_episode_identity("https://example.test/video.mkv") is None
    probe.assert_not_called()


def test_real_tagged_mkv_remains_identifiable_after_rename(tmp_path):
    from media_cli.media.download import anyplay_metadata_args
    from media_cli.media.episode_identity import clear_identity_cache, read_episode_identity

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe required for real tagged-video test")
    path = tmp_path / "original.mkv"
    subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "color=size=16x16:rate=1",
                    "-frames:v", "1", "-c:v", "ffv1",
                    *anyplay_metadata_args("Example", "31 [463]", "title-id", "episode-id"), str(path)],
                   check=True, capture_output=True, timeout=10)
    clear_identity_cache()
    original = read_episode_identity(path)
    assert original.episode_id == "episode-id"
    renamed = path.rename(tmp_path / "unrelated.mkv")
    assert read_episode_identity(renamed) == original
    clear_identity_cache()
