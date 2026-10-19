import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from media_cli.core import reporting
from media_cli.ui import loading_preparation
from media_cli.ui.actions import ActionBindings, ResolvedAction
from media_cli.ui.loading_model import LoadingInteraction, LoadingOwner, LoadingPlacement, LoadingTaskSpec
from media_cli.ui.player_preparation import run_player_preparation
from media_cli.ui.transition import PreparationCancelled
from media_cli.ui.view_model import ViewAction


def task(**changes):
    return replace(LoadingTaskSpec(
        "stream", LoadingOwner("player", 1), "Loading stream...",
        LoadingInteraction.BLOCKING, LoadingPlacement.PLAYER_STATUS,
        ActionBindings((ResolvedAction(ViewAction("back", "Cancel", ("q", "ESC"))),)), delay=0,
    ), **changes)


class Keys:
    def __init__(self, fd, *, on_key, cancel_keys=()):
        self.on_key = on_key
        self.has_pending_sequence = False

    def poll(self):
        return None


def test_player_adapter_runs_work_off_thread_and_paints_reports_on_owner(monkeypatch):
    monkeypatch.setattr(loading_preparation, "LoadingInput", Keys)
    owner = threading.current_thread()
    release = threading.Event()
    frames, reports = [], []

    def prepare(status):
        assert threading.current_thread() is not owner
        reporting.info("Checking sources")
        status("Resolving stream...")
        assert release.wait(1)
        return 42

    def report(message, color):
        assert threading.current_thread() is owner
        reports.append(message)

    def render(snapshot):
        assert threading.current_thread() is owner
        if snapshot.message == "Resolving stream...":
            frames.append(snapshot)
            release.set()

    try:
        assert run_player_preparation(
            task(), prepare, fd=10, render=render, report=report, active=lambda: True,
        ) == 42
    finally:
        release.set()
    assert frames[-1].message == "Resolving stream..."
    assert reports == ["[INFO] Checking sources"]


def test_player_adapter_keeps_only_last_eight_reports(monkeypatch):
    monkeypatch.setattr(loading_preparation, "LoadingInput", Keys)
    reports = []

    def prepare(status):
        for index in range(12):
            reporting.info(str(index))

    run_player_preparation(
        task(delay=10), prepare, fd=10, render=lambda snapshot: None,
        report=lambda message, color: reports.append(message), active=lambda: True,
    )
    assert reports == [f"[INFO] {index}" for index in range(4, 12)]


def test_cancelled_player_adapter_consumes_late_reports_without_rendering(monkeypatch):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    reports = []

    class CancelKeys(Keys):
        def poll(self):
            if started.is_set():
                self.on_key("q")
            return None

    monkeypatch.setattr(loading_preparation, "LoadingInput", CancelKeys)

    def prepare(status):
        started.set()
        try:
            assert release.wait(1)
            reporting.info("Late report")
        finally:
            finished.set()

    try:
        with pytest.raises(PreparationCancelled):
            run_player_preparation(
                task(), prepare, fd=10, render=lambda snapshot: None,
                report=lambda *args: reports.append(args), active=lambda: True,
            )
    finally:
        release.set()
        assert finished.wait(1)
    assert reports == []


def test_player_adapter_requires_main_ui_owner_and_player_placement():
    def run(spec):
        return run_player_preparation(
            spec, lambda status: None, fd=10, render=lambda snapshot: None,
            report=lambda *args: None, active=lambda: True,
        )

    with pytest.raises(ValueError, match="player status"):
        run(task(placement=LoadingPlacement.FOOTER))
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError, match="main UI thread"):
            pool.submit(run, task()).result()


@pytest.mark.parametrize("fails", [False, True])
def test_inactive_player_adapter_never_paints_or_publishes_reports(monkeypatch, fails):
    monkeypatch.setattr(loading_preparation, "LoadingInput", Keys)
    monkeypatch.setattr(reporting, "_status_sink", lambda *args: pytest.fail("Worker report escaped its scope"))
    output = []

    def prepare(status):
        reporting.info("Source status")
        if fails:
            raise ValueError("Source failed")
        return 7

    def run():
        return run_player_preparation(
            task(), prepare, fd=10, render=lambda snapshot: output.append(snapshot),
            report=lambda *args: output.append(args), active=lambda: False,
        )

    if fails:
        with pytest.raises(ValueError, match="Source failed"):
            run()
    else:
        assert run() == 7
    assert output == []
