"""Browser profile ownership must not leak state across providers or modes."""

import os
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from media_cli.services.browser_access.profiles import (
    BrowserAccessCancelled,
    BrowserProfileError,
    BrowserProfiles,
    ProfileBusyError,
)


def test_construction_is_lazy(tmp_path):
    root = tmp_path / "browser"
    BrowserProfiles(root)
    assert not root.exists()


def test_provider_profiles_survive_and_do_not_override_each_other(tmp_path):
    store = BrowserProfiles(tmp_path / "browser")
    with store.lease("animepahe") as first, store.lease("senshi") as second:
        assert first.path != second.path
        assert first.persistent and second.persistent
        (first.path / "state").write_text("catalogue and embed state")
        (second.path / "state").write_text("senshi state")
    with store.lease("animepahe") as reopened:
        assert (reopened.path / "state").read_text() == "catalogue and embed state"
    with store.lease("senshi") as reopened:
        assert (reopened.path / "state").read_text() == "senshi state"


@pytest.mark.parametrize("name", ["", "../senshi", "/tmp/test", "a/b", "a\\b", ".", "A", " a", "x" * 65])
def test_unsafe_provider_names_fail_without_creating_state(tmp_path, name):
    root = tmp_path / "browser"
    with pytest.raises(ValueError), BrowserProfiles(root).lease(name):
        pytest.fail("unsafe name admitted")
    assert not root.exists()


def test_root_profiles_and_lock_permissions_are_private(tmp_path):
    root = tmp_path / "browser"
    with BrowserProfiles(root).lease("animepahe") as profile:
        for path in (root, root / "profiles", root / "locks", profile.path):
            assert stat.S_IMODE(path.stat().st_mode) == 0o700
        assert stat.S_IMODE((root / "locks" / "animepahe.lock").stat().st_mode) == 0o600


def test_contention_and_release_after_exception(tmp_path):
    store = BrowserProfiles(tmp_path / "browser")
    with pytest.raises(RuntimeError, match="owner failure"), store.lease("animepahe"):
        with pytest.raises(ProfileBusyError), store.lease("animepahe"):
            pytest.fail("second owner admitted")
        raise RuntimeError("owner failure")
    with store.lease("animepahe"):
        pass


def test_another_process_cannot_own_the_same_profile(tmp_path):
    root = tmp_path / "browser"
    code = """
import sys
from pathlib import Path
from media_cli.services.browser_access.profiles import BrowserProfiles, ProfileBusyError
try:
    with BrowserProfiles(Path(sys.argv[1])).lease('animepahe'):
        raise SystemExit(3)
except ProfileBusyError:
    raise SystemExit(0)
"""
    with BrowserProfiles(root).lease("animepahe"):
        result = subprocess.run([sys.executable, "-c", code, str(root)],
                                cwd=Path(__file__).resolve().parents[1], timeout=10)
        assert result.returncode == 0


def test_cancelled_before_acquisition_creates_nothing(tmp_path):
    cancel = threading.Event()
    cancel.set()
    root = tmp_path / "browser"
    with pytest.raises(BrowserAccessCancelled), BrowserProfiles(root).lease("senshi", cancel=cancel):
        pytest.fail("cancelled lease admitted")
    assert not root.exists()


def test_waiting_owner_can_be_cancelled_without_releasing_first_owner(tmp_path):
    store = BrowserProfiles(tmp_path / "browser")
    cancel = threading.Event()
    timer = threading.Timer(0.05, cancel.set)
    with store.lease("senshi"):
        timer.start()
        try:
            with pytest.raises(BrowserAccessCancelled), store.lease("senshi", timeout=3, cancel=cancel):
                pytest.fail("cancelled waiter admitted")
            with pytest.raises(ProfileBusyError), store.lease("senshi"):
                pytest.fail("first ownership was lost")
        finally:
            timer.join()


def test_incognito_does_not_open_saved_state_and_is_cleaned_on_exception(tmp_path):
    root = tmp_path / "browser"
    store = BrowserProfiles(root)
    with store.lease("senshi") as saved:
        (saved.path / "state").write_text("saved secret")
        with pytest.raises(RuntimeError), store.lease("senshi", incognito=True) as private:
            assert not private.persistent
            assert private.path != saved.path
            assert not list(private.path.iterdir())
            (private.path / "state").write_text("private secret")
            private_path = private.path
            raise RuntimeError("cancel browser")
        assert not private_path.exists()
        assert (saved.path / "state").read_text() == "saved secret"
    with store.lease("senshi", incognito=True) as other:
        assert not list(other.path.iterdir())


def test_incognito_alone_does_not_create_persistent_provider_state(tmp_path):
    root = tmp_path / "browser"
    with BrowserProfiles(root).lease("senshi", incognito=True):
        assert not (root / "profiles").exists()
        assert not (root / "locks").exists()


@pytest.mark.parametrize("target", ["root", "profiles", "provider", "lock"])
def test_symlinks_are_rejected_without_touching_target(tmp_path, target):
    root = tmp_path / "browser"
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    marker = outside / "marker"
    marker.write_text("unchanged")
    if target == "root":
        root.symlink_to(outside, target_is_directory=True)
    else:
        with BrowserProfiles(root).lease("senshi"):
            pass
        if target == "profiles":
            link = root / "profiles"
            (link / "senshi").rmdir()
            link.rmdir()
            link.symlink_to(outside, target_is_directory=True)
        elif target == "provider":
            link = root / "profiles" / "senshi"
            link.rmdir()
            link.symlink_to(outside, target_is_directory=True)
        else:
            link = root / "locks" / "senshi.lock"
            link.unlink()
            link.symlink_to(marker)
    with pytest.raises(BrowserProfileError), BrowserProfiles(root).lease("senshi"):
        pytest.fail("symlink admitted")
    assert marker.read_text() == "unchanged"


def test_existing_public_root_is_rejected_not_silently_changed(tmp_path):
    root = tmp_path / "browser"
    root.mkdir(mode=0o755)
    with pytest.raises(BrowserProfileError), BrowserProfiles(root).lease("senshi"):
        pytest.fail("public state admitted")
    assert stat.S_IMODE(root.stat().st_mode) == 0o755


def test_hardlinked_lock_is_rejected(tmp_path):
    root = tmp_path / "browser"
    store = BrowserProfiles(root)
    with store.lease("senshi"):
        pass
    os.link(root / "locks" / "senshi.lock", tmp_path / "external-lock")
    with pytest.raises(BrowserProfileError), store.lease("senshi"):
        pytest.fail("shared lock inode admitted")


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan"), True])
def test_invalid_wait_timeout_creates_nothing(tmp_path, timeout):
    root = tmp_path / "browser"
    with pytest.raises(ValueError), BrowserProfiles(root).lease("senshi", timeout=timeout):
        pytest.fail("invalid timeout admitted")
    assert not root.exists()


def test_waiter_can_acquire_after_owner_releases(tmp_path):
    store = BrowserProfiles(tmp_path / "browser")
    entered = threading.Event()
    errors = []

    def wait_for_owner():
        try:
            with store.lease("senshi", timeout=2):
                entered.set()
        except Exception as exc:
            errors.append(exc)

    with store.lease("senshi"):
        worker = threading.Thread(target=wait_for_owner)
        worker.start()
        assert not entered.wait(0.05)
    worker.join(timeout=3)
    assert not worker.is_alive()
    assert not errors
    assert entered.is_set()


@pytest.mark.parametrize("kind", ["public", "directory", "fifo"])
def test_unsafe_lock_types_and_permissions_rejected(tmp_path, kind):
    root = tmp_path / "browser"
    store = BrowserProfiles(root)
    with store.lease("senshi"):
        pass
    lock = root / "locks" / "senshi.lock"
    if kind == "public":
        lock.chmod(0o644)
    else:
        lock.unlink()
        if kind == "directory":
            lock.mkdir(mode=0o700)
        else:
            os.mkfifo(lock, mode=0o600)
    with pytest.raises(BrowserProfileError), store.lease("senshi"):
        pytest.fail("unsafe lock admitted")


@pytest.mark.parametrize("directory,incognito", [("locks", False), ("incognito", True)])
def test_other_state_directory_symlinks_rejected(tmp_path, directory, incognito):
    root = tmp_path / "browser"
    store = BrowserProfiles(root)
    with store.lease("senshi", incognito=incognito):
        pass
    target = tmp_path / "outside"
    target.mkdir(mode=0o700)
    link = root / directory
    if directory == "locks":
        (link / "senshi.lock").unlink()
    link.rmdir()
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(BrowserProfileError), store.lease("senshi", incognito=incognito):
        pytest.fail("external state directory admitted")
    assert not list(target.iterdir())


def test_foreign_owned_root_rejected(tmp_path, monkeypatch):
    uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: uid + 1)
    with pytest.raises(BrowserProfileError), BrowserProfiles(tmp_path / "browser").lease("senshi"):
        pytest.fail("foreign state admitted")
