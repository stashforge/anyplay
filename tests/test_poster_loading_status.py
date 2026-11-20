import time

import pytest

from media_cli.ui.background_loading import BackgroundLoadingStatus
from media_cli.ui.footer import StatusKind
from media_cli.ui.poster import PosterManager


@pytest.fixture
def poster(monkeypatch):
    reads = []

    class ObservedStatus(BackgroundLoadingStatus):
        def read(self, active):
            reads.append((self, active))
            return super().read(active)

    monkeypatch.setattr("media_cli.ui.poster.BackgroundLoadingStatus", ObservedStatus)
    manager = PosterManager(
        enabled=lambda: True, cache_dir=lambda: "/tmp",
        hovered_show_id=lambda: None, request_redraw=lambda: None,
        loading_frame=lambda: "~",
    )
    return manager, reads


@pytest.mark.parametrize("end", ["ready", "failed", "missing", "no_chafa", "disabled", "empty"])
def test_poster_status_uses_shared_loading_and_retires_on_end(poster, end):
    manager, reads = poster
    show = {"_poster_status": "loading", "_image_message": "Portrait"}
    status = manager.footer_status(show, persistent=True)
    assert status.message == "Loading cover: Portrait"
    assert status.kind is StatusKind.LOADING
    assert status.indicator == "~"
    assert len(reads) == 1
    adapter = reads[0][0]
    if end == "disabled":
        manager.enabled = lambda: False
    elif end != "empty":
        show["_poster_status"] = end
    manager.footer_status(None if end == "empty" else show, persistent=True)
    assert reads == [(adapter, True), (adapter, False)]


def test_switching_loading_posters_retires_previous_task(poster):
    manager, reads = poster
    first = {"_poster_status": "loading"}
    second = {"_poster_status": "loading"}
    manager.footer_status(first)
    manager.footer_status(second)
    assert len(reads) == 3
    assert reads[0] == (reads[1][0], True)
    assert reads[1][1] is False
    assert reads[2][1] is True
    assert reads[2][0] is not reads[0][0]


def test_poster_loading_timeout_and_persistent_mode_keep_existing_behavior(poster, monkeypatch):
    manager, reads = poster
    monkeypatch.setattr(time, "time", lambda: 100.0)
    show = {"_poster_status": "loading", "_poster_status_time": 99.0}
    assert manager.footer_status(show) is not None
    monkeypatch.setattr(time, "time", lambda: 104.0)
    assert manager.footer_status(show) is None
    assert [active for _, active in reads] == [True, False]
    assert manager.footer_status(show, persistent=True).message == "Loading cover"


def test_ready_raw_cache_clears_loading_without_starting_download(poster):
    manager, reads = poster
    show = {"_poster_status": "loading"}
    manager.footer_status(show)
    show["_poster_raw"] = "cached pixels"
    assert manager.footer_status(show) is None
    assert [active for _, active in reads] == [True, False]


def test_same_cover_url_keeps_loading_task_across_show_copies_and_timestamp_changes(poster):
    manager, reads = poster
    first = {"thumbnail": "https://example.test/cover.jpg", "_poster_status": "loading",
             "_poster_status_time": 1}
    second = dict(first, _poster_status_time=2)
    manager.footer_status(first, persistent=True)
    manager.footer_status(second, persistent=True)
    assert len(reads) == 2
    assert reads[0][0] is reads[1][0]
    assert all(active for _, active in reads)


@pytest.mark.parametrize('status', ['missing', 'failed', 'no_chafa'])
def test_terminal_cover_notice_requests_final_expiry_paint(poster, monkeypatch, status):
    manager, _ = poster
    now = [100.0]
    monkeypatch.setattr(time, 'time', lambda: now[0])
    show = {}
    manager.set_status(show, status)
    assert manager.footer_status(show) is not None
    assert manager.needs_tick(show)
    now[0] += 3.1
    assert manager.needs_tick(show)  # Expiry alone must not skip the final paint.
    assert manager.footer_status(show) is None
    assert not manager.needs_tick(show)
    manager.set_status(show, 'ready')
    manager.set_status(show, status)
    assert manager.needs_tick(show)
    assert manager.footer_status(show) is not None


def test_missing_notice_without_timestamp_still_expires(poster, monkeypatch):
    manager, _ = poster
    monkeypatch.setattr(time, 'time', lambda: 100.0)
    show = {'_poster_status': 'missing'}
    assert manager.footer_status(show) is not None
    monkeypatch.setattr(time, 'time', lambda: 104.0)
    assert manager.footer_status(show) is None
    assert not manager.needs_tick(show)
