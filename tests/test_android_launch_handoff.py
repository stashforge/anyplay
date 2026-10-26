import logging
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from media_cli.media import aniskip
from media_cli.playback import android, engine
from media_cli.ui import player_screen
from media_cli.ui.actions import ActionAvailability
from media_cli.ui.transition import PreparationCancelled

pytestmark = pytest.mark.usefixtures("installed_android_launcher")


def test_android_dispatch_runs_on_owner_and_only_wait_runs_on_worker(monkeypatch):
    owner = threading.current_thread()
    calls = []

    @contextmanager
    def scope():
        calls.append("enter")
        yield True
        calls.append("leave")

    def communicate():
        assert threading.current_thread() is not owner
        calls.append("wait")
        return "Starting: Intent", ""

    process = SimpleNamespace(communicate=communicate, returncode=0,
                              poll=lambda: 0, stdout=None, stderr=None)

    def dispatch(command, **kwargs):
        assert threading.current_thread() is owner
        assert calls == ["enter"]
        assert kwargs["stdin"] == engine.subprocess.DEVNULL
        calls.append("dispatch")
        return process

    def pump(work, *, task):
        assert task.message == "Opening player..."
        for key in ("q", "Q", "ESC"):
            assert task.actions.resolve(key).availability is ActionAvailability.BLOCKED
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(work).result(timeout=1)

    monkeypatch.setattr(player_screen, "playback_input_scope", scope)
    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    monkeypatch.setattr(engine, "subprocess", SimpleNamespace(
        Popen=dispatch, DEVNULL=-3, PIPE=-1, CompletedProcess=subprocess.CompletedProcess,
    ))
    command = ["am", "start", "-p", "player"]
    result = engine._launch_android_player(command)
    assert result.args == command
    assert result.stdout == "Starting: Intent"
    assert result.returncode == 0
    assert calls == ["enter", "dispatch", "wait", "leave"]


@pytest.mark.parametrize("failure", [PreparationCancelled, KeyboardInterrupt])
def test_pending_cancel_prevents_android_dispatch(monkeypatch, failure):
    @contextmanager
    def scope():
        raise failure
        yield

    monkeypatch.setattr(player_screen, "playback_input_scope", scope)
    monkeypatch.setattr(engine, "subprocess", SimpleNamespace(Popen=lambda *a, **kw: pytest.fail("Cancelled intent dispatched")))
    expected = android.AndroidPreparationCancelled if failure is PreparationCancelled else KeyboardInterrupt
    with pytest.raises(expected):
        engine._launch_android_player(["am", "start"])


def test_ctrl_c_stops_launch_command_without_waiting_for_abandoned_worker(monkeypatch):
    calls = []

    @contextmanager
    def scope():
        yield True

    process = SimpleNamespace(poll=lambda: None, kill=lambda: calls.append("kill"),
                              wait=lambda **kw: calls.append("reap"), stdout=None, stderr=None)

    def pump(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(player_screen, "playback_input_scope", scope)
    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    monkeypatch.setattr(engine, "subprocess", SimpleNamespace(Popen=lambda *a, **kw: process, DEVNULL=-3, PIPE=-1))
    with pytest.raises(KeyboardInterrupt):
        engine._launch_android_player(["am", "start"])
    assert calls == ["kill", "reap"]


def test_interrupt_reaps_real_launch_helper_and_unblocks_communication(monkeypatch):
    import sys

    owner = threading.current_thread()
    started = threading.Event()
    processes, pending = [], []
    popen = subprocess.Popen

    @contextmanager
    def scope():
        yield True

    def dispatch(*args, **kwargs):
        assert threading.current_thread() is owner
        process = popen(*args, **kwargs)
        processes.append(process)
        communicate = process.communicate

        def wait():
            started.set()
            return communicate()

        process.communicate = wait
        return process

    with ThreadPoolExecutor(max_workers=1) as pool:
        def pump(work, **kwargs):
            pending.append(pool.submit(work))
            assert started.wait(1)
            raise KeyboardInterrupt

        monkeypatch.setattr(player_screen, "playback_input_scope", scope)
        monkeypatch.setattr(player_screen, "run_loading_work", pump)
        monkeypatch.setattr(engine.subprocess, "Popen", dispatch)
        with pytest.raises(KeyboardInterrupt):
            engine._launch_android_player([sys.executable, "-c", "import time; time.sleep(60)"])
        assert processes[0].poll() is not None
        pending[0].result(timeout=1)
    assert processes[0].stdout.closed
    assert processes[0].stderr.closed


@pytest.mark.parametrize("outcome", ["success", "android14", "fallback", "failure", "cancel", "interrupt"])
def test_android_launch_adapter_preserves_results_and_cleans_failed_proxy(monkeypatch, caplog, outcome):
    calls, commands, cleaned = [], [], []
    server = object()
    caplog.set_level(logging.DEBUG, logger="media_cli.playback.android")
    monkeypatch.setattr(aniskip, "generate_chapters_file", lambda *a: None)
    monkeypatch.setattr(android, "start_local_proxy", lambda *a, **kw: ("http://127.0.0.1/video", server))
    monkeypatch.setattr(android, "replace_active_local_proxy", lambda value: calls.append("active"))
    monkeypatch.setattr(android, "cleanup_active_local_proxy", lambda: cleaned.append(True))

    def dispatch(command):
        commands.append(command)
        if outcome == "cancel":
            raise android.AndroidPreparationCancelled
        if outcome == "interrupt":
            raise KeyboardInterrupt
        success = outcome == "success" or (outcome == "fallback" and len(commands) == 2)
        return SimpleNamespace(returncode=0 if success else 1,
                               stdout="Starting: Intent" if outcome == "android14" else "",
                               stderr="private https://example.org/video?token=secret")

    def play():
        return android.play_android("Title", "1", {"link": "https://example.org/video.mp4"}, None,
                                    player="mpv", launch_command_fn=dispatch)

    if outcome == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            play()
    else:
        assert play() is (outcome in {"success", "android14", "fallback"})
    assert calls == ["active"]
    assert len(cleaned) == (1 if outcome in {"success", "android14", "fallback"} else 2)
    assert len(commands) == (2 if outcome in {"fallback", "failure"} else 1)
    if outcome == "fallback":
        assert "-n" in commands[0] and "-p" in commands[1]
    results = [record.getMessage() for record in caplog.records if "Android player launch result:" in record.getMessage()]
    assert len(results) == (0 if outcome in {"cancel", "interrupt"} else len(commands))
    for attempt, message in enumerate(results, start=1):
        assert f"attempt={attempt}" in message
        assert "returncode=" in message
        assert "stderr_present=True" in message
    assert "token=secret" not in caplog.text
