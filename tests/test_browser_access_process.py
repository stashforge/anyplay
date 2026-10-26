"""Owned Chromium lifecycle without launching a real GUI in unit tests."""

import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from media_cli.services.browser_access import process as browser_process
from media_cli.services.browser_access.policy import BrowserPolicy, BrowserPolicyError
from media_cli.services.browser_access.process import BrowserProcessError, BrowserWindowNotReady, OwnedChromium
from media_cli.services.browser_access.profiles import BrowserAccessCancelled, BrowserProfile


class FakeProcess:
    pid = 43210

    def __init__(self):
        self.done = threading.Event()
        self.returncode = None
        self.ignore_term = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if not self.done.wait(timeout):
            raise subprocess.TimeoutExpired("owned-browser", timeout)
        return self.returncode

    def finish(self):
        self.returncode = 0
        self.done.set()


class FakeBridge:
    def __init__(self):
        self.calls = []
        self.reply = {"state": "normal"}

    def request(self, payload, **kwargs):
        self.calls.append((payload, kwargs))
        return self.reply


@pytest.fixture
def setup_owner(tmp_path, monkeypatch):
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(mode=0o700)
    extension = profile_dir / "bridge-123"
    extension.mkdir(mode=0o700)
    process = FakeProcess()
    launches, signals = [], []
    monkeypatch.setenv("DISPLAY", ":1")
    monkeypatch.setattr(browser_process.sys, "platform", "linux")
    monkeypatch.setattr(browser_process.shutil, "which", lambda name: "/usr/bin/chromium")

    def launch(args, **kwargs):
        launches.append((args, kwargs))
        return process

    def killpg(pid, sig):
        assert pid == process.pid
        signals.append(sig)
        if sig == signal.SIGKILL or not process.ignore_term:
            process.finish()

    monkeypatch.setattr(browser_process.subprocess, "Popen", launch)
    monkeypatch.setattr(browser_process.os, "killpg", killpg)
    def waitid(kind, pid, flags):
        assert pid == process.pid
        assert flags & browser_process.os.WNOWAIT
        process.done.wait()
    monkeypatch.setattr(browser_process.os, "waitid", waitid)
    bridge = FakeBridge()
    owner = OwnedChromium(BrowserProfile("test", profile_dir), BrowserPolicy("test", ["https://site.test"]),
                          extension, "https://site.test/", bridge, shutdown_seconds=0.02)
    return owner, process, bridge, launches, signals


def test_lazy_native_app_launch_and_cleanup(setup_owner):
    owner, process, _, launches, signals = setup_owner
    assert launches == []
    with owner:
        args, options = launches[0]
        assert args[0] == "/usr/bin/chromium"
        assert "--no-startup-window" in args
        assert "--window-size=760,640" in args
        assert not any(arg.startswith("--app=") for arg in args)
        assert "--disable-background-mode" not in args
        assert not any("debug" in arg or "headless" in arg for arg in args)
        assert options["start_new_session"] is True
        assert options["stdin"] == options["stdout"] == options["stderr"] == subprocess.DEVNULL
        assert not owner.closed.is_set()
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert process.done.is_set()
    assert owner.closed.is_set()


def test_natural_browser_exit_is_observable(setup_owner):
    owner, process, *_ = setup_owner
    with owner:
        process.finish()
        assert owner.closed.wait(1)


def test_stubborn_owned_process_is_killed(setup_owner):
    owner, process, _, _, signals = setup_owner
    process.ignore_term = True
    with owner:
        pass
    assert signals == [signal.SIGTERM, signal.SIGKILL]


def test_leader_exit_does_not_shorten_descendant_shutdown_grace(setup_owner, monkeypatch):
    owner, _, _, _, _ = setup_owner
    times = {}
    signal_group = owner._signal_group
    def timed_signal(sig):
        times[sig] = time.monotonic()
        signal_group(sig)
    monkeypatch.setattr(owner, "_signal_group", timed_signal)
    with owner:
        pass
    assert times[signal.SIGKILL] - times[signal.SIGTERM] >= owner.shutdown_seconds * 0.9


def test_interrupt_after_launch_stops_and_reaps_owned_browser(setup_owner, monkeypatch):
    owner, process, _, _, signals = setup_owner
    def interrupted_start(self):
        raise KeyboardInterrupt
    monkeypatch.setattr(browser_process.threading.Thread, "start", interrupted_start)
    with pytest.raises(KeyboardInterrupt):
        owner.__enter__()
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert process.done.is_set()
    assert owner.closed.is_set()


def test_exception_cleanup_and_repeated_close(setup_owner):
    owner, _, _, _, signals = setup_owner
    with pytest.raises(ValueError):
        with owner:
            raise ValueError("caller failed")
    owner.close()
    assert signals == [signal.SIGTERM, signal.SIGKILL]


def test_no_reentry_or_restart(setup_owner):
    owner, *_ = setup_owner
    with owner:
        with pytest.raises(BrowserProcessError):
            owner.__enter__()
    with pytest.raises(BrowserProcessError):
        owner.__enter__()


@pytest.mark.parametrize("failure", ["missing", "no_display", "platform", "public_profile", "foreign_extension"])
def test_invalid_environment_never_launches(setup_owner, monkeypatch, failure):
    owner, _, _, launches, _ = setup_owner
    if failure == "missing":
        monkeypatch.setattr(browser_process.shutil, "which", lambda _: None)
    elif failure == "no_display":
        monkeypatch.delenv("DISPLAY", raising=False)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    elif failure == "platform":
        monkeypatch.setattr(browser_process.sys, "platform", "win32")
    elif failure == "public_profile":
        owner.profile.path.chmod(0o755)
    else:
        owner.extension = Path("/tmp/not-owned")
    with pytest.raises(BrowserProcessError):
        with owner:
            pytest.fail("unsafe launch accepted")
    assert launches == []


def test_invalid_url_is_rejected(setup_owner):
    owner, *_ = setup_owner
    owner.startup_url = "https://other.test/"
    with pytest.raises(BrowserPolicyError):
        with owner:
            pytest.fail("invalid startup URL accepted")


def test_popen_error_is_sanitized(setup_owner, monkeypatch):
    owner, *_ = setup_owner

    def fail(*args, **kwargs):
        raise OSError("sensitive arguments")

    monkeypatch.setattr(browser_process.subprocess, "Popen", fail)
    with pytest.raises(BrowserProcessError, match="could not start") as error:
        owner.__enter__()
    assert "sensitive" not in str(error.value)
    owner.close()


def test_owned_window_controls_use_authenticated_bridge(setup_owner):
    owner, _, bridge, *_ = setup_owner
    with owner:
        owner.set_window_state("normal", timeout=3)
        assert bridge.calls[0][0] == {"operation": "window", "state": "normal"}
        assert bridge.calls[0][1]["timeout"] == 3
        bridge.reply = {"state": "minimized"}
        owner.set_window_state("minimized")


@pytest.mark.parametrize("state", ["fullscreen", "close", "", None])
def test_unknown_window_controls_never_sent(setup_owner, state):
    owner, _, bridge, *_ = setup_owner
    with owner:
        with pytest.raises(BrowserProcessError):
            owner.set_window_state(state)
        assert bridge.calls == []


def test_window_control_cancellation_and_closed_browser(setup_owner):
    owner, process, bridge, *_ = setup_owner
    with owner:
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(BrowserAccessCancelled):
            owner.set_window_state("normal", cancel=cancel)
        process.finish()
        assert owner.closed.wait(1)
        with pytest.raises(BrowserAccessCancelled):
            owner.set_window_state("normal")
        assert bridge.calls == []


def test_window_control_rejects_bad_reply(setup_owner):
    owner, _, bridge, *_ = setup_owner
    with owner:
        bridge.reply = {"state": "minimized"}
        with pytest.raises(BrowserProcessError):
            owner.set_window_state("normal")


def test_page_reload_during_window_control_has_distinct_retryable_error(setup_owner):
    owner, _, bridge, *_ = setup_owner
    with owner:
        bridge.reply = {"error": "browser_operation_failed"}
        with pytest.raises(BrowserWindowNotReady):
            owner.set_window_state("normal")


def test_concurrent_close_waits_for_cleanup(setup_owner):
    owner, process, _, _, signals = setup_owner
    process.ignore_term = True
    owner.__enter__()
    threads = [threading.Thread(target=owner.close) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)
        assert not thread.is_alive()
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    assert process.done.is_set()


def test_real_process_group_cleanup_leaves_unrelated_process_alive(tmp_path, monkeypatch):
    profile_dir = tmp_path / "profile"
    profile_dir.mkdir(mode=0o700)
    extension = profile_dir / "bridge"
    extension.mkdir(mode=0o700)
    program = tmp_path / "fake-chromium"
    child_code = (
        "import os,signal,time; from pathlib import Path; "
        "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
        f"Path({str(profile_dir / 'child.pid')!r}).write_text(str(os.getpid())); time.sleep(60)"
    )
    program.write_text(
        f"#!{sys.executable}\nimport subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        "time.sleep(60)\n"
    )
    program.chmod(0o700)
    monkeypatch.setattr(browser_process.shutil, "which", lambda _: str(program))
    monkeypatch.setenv("DISPLAY", ":1")
    unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"])
    try:
        with OwnedChromium(BrowserProfile("test", profile_dir), BrowserPolicy("test", ["https://site.test"]),
                           extension, "https://site.test/", FakeBridge(), shutdown_seconds=0.1) as owner:
            deadline = time.monotonic() + 5
            pid_file = profile_dir / "child.pid"
            while not pid_file.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert pid_file.exists(), "owned child did not start"
            child_pid = int(pid_file.read_text())
            # Simulate the app leader exiting while its stubborn child remains.
            browser_process.os.kill(owner._process.pid, signal.SIGTERM)
            assert owner.closed.wait(2)
            assert owner._process.returncode is None, "leader must remain unreaped until group cleanup"
            assert Path(f"/proc/{owner._process.pid}/stat").read_text().split()[2] == "Z"
        assert owner.closed.is_set()
        assert unrelated.poll() is None
        # A terminated orphan may briefly remain a zombie until init reaps it.
        deadline = time.monotonic() + 2
        status = Path(f"/proc/{child_pid}/stat")
        while status.exists() and status.read_text().split()[2] != "Z" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not status.exists() or status.read_text().split()[2] == "Z"
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)
