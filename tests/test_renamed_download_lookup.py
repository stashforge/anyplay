"""Normal local playback discovers renamed downloads using explicit identity."""
import pytest

from media_cli.core import storage
from media_cli.media import episode_identity
from media_cli.media.episode_identity import EmbeddedEpisodeIdentity


@pytest.fixture
def downloads(monkeypatch, tmp_path):
    root = tmp_path / "downloads"
    folder = root / "Example"
    folder.mkdir(parents=True)
    file = folder / "unrelated.mkv"
    file.write_bytes(b"video")
    metadata = dict(_id="title-id", name="Example", _provider="example",
                    _episode_labels={"opaque-episode-id": "31 [463]"})
    db = {"shows": {"Example": {"metadata": metadata, "episode_files": {}, "episodes": ["31"]}}}
    identities = {str(file): EmbeddedEpisodeIdentity("Example", "31 [463]", "title-id", "opaque-episode-id")}
    monkeypatch.setattr(storage, "load_downloads_db", lambda: db)
    monkeypatch.setattr(episode_identity, "read_episode_identity", lambda path: identities.get(str(path)))
    return root, folder, file, identities, db


@pytest.mark.parametrize("identifier", ["31", "463", "opaque-episode-id"])
@pytest.mark.parametrize("rename_folder", [False, True])
def test_renamed_video_is_found_by_embedded_episode_and_title(downloads, identifier, rename_folder):
    root, folder, file, identities, db = downloads
    if rename_folder:
        identity = identities.pop(str(file))
        new_folder = folder.rename(root / "Renamed folder")
        file = new_folder / file.name
        identities[str(file)] = identity
    assert storage.find_offline_file_for_episode("Example", identifier, {"download_dir": str(root)}) == str(file)


def test_filename_cannot_override_conflicting_embedded_episode(downloads):
    root, folder, file, identities, db = downloads
    renamed = file.rename(folder / "Example - Episode 31.mkv")
    identities.clear()
    identities[str(renamed)] = EmbeddedEpisodeIdentity("Example", "32", "title-id", "other-episode")
    assert storage.find_offline_file_for_episode("Example", "31", {"download_dir": str(root)}) is None


def test_cross_title_tags_are_not_selected_from_renamed_folder(downloads):
    root, folder, file, identities, db = downloads
    identity = EmbeddedEpisodeIdentity("Other", "31", "other-title", "other-episode")
    renamed = folder.rename(root / "Other folder") / file.name
    identities.clear()
    identities[str(renamed)] = identity
    assert storage.find_offline_file_for_episode("Example", "31", {"download_dir": str(root)}) is None


def test_registered_file_path_works_without_tags_or_expected_folder(downloads):
    root, folder, file, identities, db = downloads
    renamed = folder.rename(root / "Renamed") / file.name
    identities.clear()
    db["shows"]["Example"]["episode_files"] = {"31": [str(renamed)]}
    assert storage.find_offline_file_for_episode("Example", "31", {"download_dir": str(root)}) == str(renamed)


@pytest.mark.parametrize("registered", [False, True])
def test_cleanup_lookup_uses_snapshot_without_discovery(downloads, monkeypatch, registered):
    root, _, file, _, db = downloads
    if registered:
        db["shows"]["Example"]["episode_files"] = {"31": [str(file)]}
    monkeypatch.setattr(storage, "load_downloads_db", lambda: pytest.fail("Repeated database read"))
    monkeypatch.setattr(storage, "_find_offline_file_by_name", lambda *a: pytest.fail("Unregistered discovery"))
    assert storage.find_offline_file_for_episode(
        "Example", "31", {"download_dir": str(root)},
        registered_only=True, downloads_db=db,
    ) == (str(file) if registered else None)


def test_legacy_named_file_still_works_without_probe_identity(downloads):
    root, folder, file, identities, db = downloads
    renamed = file.rename(folder / "Example - Episode 31.mkv")
    identities.clear()
    assert storage.find_offline_file_for_episode("Example", "31", {"download_dir": str(root)}) == str(renamed)


def test_untagged_file_in_another_folder_is_not_guessed(downloads):
    root, folder, file, identities, db = downloads
    other = folder.rename(root / "Other")
    (other / file.name).rename(other / "Example - Episode 31.mkv")
    identities.clear()
    assert storage.find_offline_file_for_episode("Example", "31", {"download_dir": str(root)}) is None


def test_registered_path_outside_download_root_is_not_selected(downloads, tmp_path):
    root, folder, file, identities, db = downloads
    outside = tmp_path / "external.mkv"
    outside.write_bytes(b"video")
    identities.clear()
    db["shows"]["Example"]["episode_files"] = {"31": [str(outside)]}
    assert storage.find_offline_file_for_episode("Example", "31", {"download_dir": str(root)}) is None


def test_lookup_does_not_wait_for_blocked_probes(monkeypatch):
    from threading import Event
    import time

    release = Event()
    def blocked(path):
        release.wait(2)
        return None
    monkeypatch.setattr(episode_identity, "read_episode_identity", blocked)
    started = time.monotonic()
    try:
        assert episode_identity.find_matching_episode_identity(["a", "b"], lambda identity: True, timeout=0.03) is None
        assert time.monotonic() - started < 1
    finally:
        release.set()
