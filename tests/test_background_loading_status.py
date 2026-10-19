from concurrent.futures import ThreadPoolExecutor

import pytest

from media_cli.ui.actions import ActionBindings
from media_cli.ui.background_loading import BackgroundLoadingStatus
from media_cli.ui.footer import StatusKind
from media_cli.ui.loading_model import (
    LoadingInteraction,
    LoadingOwner,
    LoadingPlacement,
    LoadingProgress,
    LoadingTaskSpec,
)


def task(**changes):
    values = dict(
        task_id="search", owner=LoadingOwner("search", 1), message="Searching...",
        interaction=LoadingInteraction.BACKGROUND,
        placement=LoadingPlacement.FOOTER, actions=ActionBindings(()), delay=0.15,
    )
    values.update(changes)
    return LoadingTaskSpec(**values)


def test_background_status_uses_shared_delay_and_finishes():
    now = [0.0]
    status = BackgroundLoadingStatus(task(), "dots", clock=lambda: now[0])
    assert status.read(True) is None
    now[0] = 0.15
    shown = status.read(True)
    assert shown.message == "Searching..."
    assert shown.kind is StatusKind.LOADING
    assert shown.indicator
    assert status.read(False) is None
    assert status.read(False) is None


def test_background_status_preserves_supplied_frame_callback_after_delay():
    now = [0.0]
    frames = iter(("*", "~"))
    calls = []

    def frame():
        calls.append(now[0])
        return next(frames)

    status = BackgroundLoadingStatus(task(), "dots", clock=lambda: now[0], frame=frame)
    assert status.read(True) is None
    assert calls == []
    now[0] = 0.15
    assert status.read(True).indicator == "*"
    now[0] = 0.20
    assert status.read(True).indicator == "~"
    assert status.read(False) is None
    assert calls == [0.15, 0.20]


def test_background_frame_callback_uses_ui_thread_during_preparation():
    import threading

    from media_cli.ui.transition import run_preparation

    ui_thread = threading.current_thread()

    def frame():
        assert threading.current_thread() is ui_thread
        return "*"

    status = BackgroundLoadingStatus(task(delay=0), "dots", frame=frame)

    def prepare():
        shown = status.read(True)
        status.read(False)
        return shown

    assert run_preparation(prepare, tick=lambda: None).indicator == "*"


def test_background_status_rejects_noncallable_frame():
    with pytest.raises(TypeError, match="frame.*callable"):
        BackgroundLoadingStatus(task(), "dots", frame="*")


def test_background_progress_updates_same_task_without_restarting(monkeypatch):
    from media_cli.ui.loading_lifecycle import LoadingLifecycle

    lifecycles = []

    class ObservedLifecycle(LoadingLifecycle):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            lifecycles.append(self)

    monkeypatch.setattr("media_cli.ui.background_loading.LoadingLifecycle", ObservedLifecycle)
    now = [0.0]
    spec = task(delay=0)
    status = BackgroundLoadingStatus(spec, "dots", clock=lambda: now[0])
    status.read(True, progress=LoadingProgress(1, 3))
    first = lifecycles[0].snapshots(spec.owner)[0]
    assert first.progress == LoadingProgress(1, 3)
    now[0] = 2
    status.read(True, progress=LoadingProgress(2, 3))
    updated = lifecycles[0].snapshots(spec.owner)[0]
    assert updated.token == first.token
    assert updated.progress == LoadingProgress(2, 3)
    assert updated.elapsed == 2
    assert status.read(True, message="Checking the remaining sources").message == "Checking the remaining sources"
    renamed = lifecycles[0].snapshots(spec.owner)[0]
    assert renamed.token == first.token
    assert renamed.elapsed == 2
    status.read(False)
    assert lifecycles[0].snapshots(spec.owner) == ()


@pytest.mark.parametrize("message", ["", "x" * 121, "bad\nmessage", []])
def test_background_message_update_rejects_invalid_wording(message):
    status = BackgroundLoadingStatus(task(delay=0), "dots")
    assert status.read(True).message == "Searching..."
    with pytest.raises(ValueError):
        status.read(True, message=message)
    assert status.read(True).message == "Searching..."


def test_completed_before_first_poll_never_starts_task(monkeypatch):
    def unexpected():
        pytest.fail("Completed work must not create a loading lifecycle")

    monkeypatch.setattr("media_cli.ui.background_loading.LoadingLifecycle", unexpected)
    status = BackgroundLoadingStatus(task(), "dots")
    assert status.read(False) is None


def test_background_status_binds_on_reader_not_creator_thread():
    with ThreadPoolExecutor(max_workers=1) as pool:
        status = pool.submit(BackgroundLoadingStatus, task(delay=0), "dots").result()
    assert status.read(True).message == "Searching..."
    assert status.read(False) is None


def test_preparation_status_check_and_picker_read_share_ui_owner():
    import threading

    from media_cli.ui.transition import call_on_ui_thread, run_preparation

    ui_thread = threading.current_thread()
    status = BackgroundLoadingStatus(task(delay=0), "dots")

    def prepare():
        assert threading.current_thread() is not ui_thread
        # AniList checks status before handing the picker to the UI thread.
        assert status.read(True).message == "Searching..."

        def picker_read():
            assert threading.current_thread() is ui_thread
            return status.read(True)

        shown = call_on_ui_thread(picker_read)
        assert status.read(False) is None
        return shown

    assert run_preparation(prepare, tick=lambda: None).message == "Searching..."


def test_background_status_rejects_blocking_tasks_and_nonboolean_state():
    with pytest.raises(ValueError, match="background footer"):
        BackgroundLoadingStatus(task(interaction=LoadingInteraction.BLOCKING), "dots")
    status = BackgroundLoadingStatus(task(), "dots")
    with pytest.raises(TypeError, match="bool"):
        status.read(1)


@pytest.mark.parametrize("fails", [False, True])
def test_anilist_search_reports_pending_and_completion_through_adapter(monkeypatch, fails):
    from media_cli.app import search_coordinator as search
    from media_cli.core.api import SearchFailure

    workers = []
    reads = []

    class DeferredThread:
        def __init__(self, *, target, daemon):
            workers.append(target)

        def start(self):
            pass

    class ObservedStatus(BackgroundLoadingStatus):
        def read(self, active):
            reads.append(active)
            return super().read(active)

    def fetch(*args, **kwargs):
        if fails:
            raise SearchFailure("Search failed")
        return []

    monkeypatch.setattr(search.threading, "Thread", DeferredThread)
    monkeypatch.setattr(search, "search_anilist", fetch)
    monkeypatch.setattr("media_cli.ui.background_loading.BackgroundLoadingStatus", ObservedStatus)
    live, results, status, error = search.make_anilist_oneshot_search("", "test")
    assert status().kind is StatusKind.LOADING
    workers[0]()
    assert status() == ""
    assert reads == [True, False]
    assert live()[2] is True
    assert results() == []
    assert bool(error()) is fails


@pytest.mark.parametrize("outcome", ["empty", "error", "enriching", "enrichment_error", "plain"])
def test_provider_search_uses_shared_status_then_preserves_enrichment(monkeypatch, outcome):
    import media_cli.app_core as app_core
    from media_cli.app import search_coordinator as search
    from media_cli.context import FLAGS

    workers = []
    reads = []

    class DeferredThread:
        def __init__(self, *, target, daemon=False):
            self.target = target
            self.daemon = daemon

        def start(self):
            if self.daemon:
                workers.append(self.target)
            else:
                self.target()

        def join(self):
            pass

    class ObservedStatus(BackgroundLoadingStatus):
        def read(self, active):
            reads.append((self._spec.task_id, active))
            return super().read(active)

    show = {"title": "Test"}

    def fetch(*args, **kwargs):
        if outcome == "error":
            raise RuntimeError("Provider unavailable")
        return [show] if outcome in ("enriching", "enrichment_error", "plain") else []

    def enrich(*args):
        if outcome == "enrichment_error":
            raise RuntimeError("Enrichment unavailable")

    monkeypatch.setattr(search, "_provider_search_cache", {})
    monkeypatch.setattr(search, "load_config", lambda: {})
    monkeypatch.setattr(search.threading, "Thread", DeferredThread)
    monkeypatch.setattr(search, "search_anime", fetch)
    monkeypatch.setattr(search.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(FLAGS, "plain_mode", outcome == "plain")
    monkeypatch.setattr(app_core, "batch_prepare_shows_display_state", lambda *args: None)
    monkeypatch.setattr(
        "media_cli.core.enrichment.enrich_provider_results_fast",
        lambda shows, _: (shows, shows),
    )
    monkeypatch.setattr(
        "media_cli.core.enrichment.enrich_provider_results_background", enrich,
    )
    monkeypatch.setattr("media_cli.ui.background_loading.BackgroundLoadingStatus", ObservedStatus)
    live, results, status, error = search.make_provider_oneshot_search("test", "sub", "anikoto")
    assert status().message == "Searching…"
    workers[0]()
    shown = status()
    assert reads == [("search", True), ("enrichment", False), ("search", False),
                     ("enrichment", outcome in ("enriching", "enrichment_error"))]
    if outcome in ("enriching", "enrichment_error"):
        assert shown.message == "Enriching metadata…"
        assert live()[2] is False
        assert results() == [show]
        if outcome == "enrichment_error":
            with pytest.raises(RuntimeError, match="Enrichment unavailable"):
                workers[1]()
        else:
            workers[1]()
        assert status() == ""
        assert reads[-2:] == [("search", False), ("enrichment", False)]
    elif outcome == "plain":
        assert shown == ""
        assert results() == [show]
        assert len(workers) == 1
    else:
        assert shown == ""
        assert results() == []
    assert live()[2] is True
    assert bool(error()) is (outcome == "error")


def test_cached_provider_search_creates_no_loading_adapter(monkeypatch):
    from media_cli.app import search_coordinator as search

    def unexpected(*args):
        pytest.fail("Cached search must not create a loading adapter")

    monkeypatch.setattr(search, "load_config", lambda: {})
    monkeypatch.setattr(search, "_provider_search_cache", {("test", "sub", "anikoto"): []})
    monkeypatch.setattr("media_cli.ui.background_loading.BackgroundLoadingStatus", unexpected)
    live, _, status, _ = search.make_provider_oneshot_search("test", "sub", "anikoto")
    assert status() == ""
    assert live() == ([], "", True)
