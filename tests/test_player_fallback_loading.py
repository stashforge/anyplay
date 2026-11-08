import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from media_cli.app import playback_player
from media_cli.core import streams
from media_cli.ui import player_screen
from media_cli.ui.transition import PreparationCancelled

KEY = ("show", "1", "sub", "example", "1080p")


@pytest.mark.parametrize("during_resolve", [False, True])
def test_fallback_resolver_observes_cancellation_before_and_after_request(monkeypatch, during_resolve):
    cancelled = threading.Event()
    calls = []
    data = {"episode": {"sourceUrls": [{"sourceName": "One", "link": "one"}]}}
    if not during_resolve:
        cancelled.set()

    def resolve(source):
        calls.append(source)
        cancelled.set()
        return [{"source_name": "One", "link": "late"}]

    monkeypatch.setattr(streams, "get_preferred_mirror", lambda *a: {})
    monkeypatch.setattr(streams, "resolve_source", resolve)
    assert streams.fetch_episode_stream(
        *KEY[:3], KEY[4], provider_id=KEY[3], ep_data=data, cancel_check=cancelled.is_set,
    ) is None
    assert len(calls) == int(during_resolve)


def test_fallback_fetch_uses_worker_and_detached_episode_data(monkeypatch):
    owner = threading.current_thread()
    data = {"sources": []}

    def fetch(*args, **kwargs):
        assert threading.current_thread() is not owner
        assert args == ("show", "1", "sub", "1080p")
        assert kwargs["provider_id"] == "example"
        assert kwargs["exclude_sources"] == frozenset(("Failed",))
        assert not kwargs["cancel_check"]()
        kwargs["ep_data"]["sources"].append("resolved")
        return ("selected", "mirror", kwargs["ep_data"], [])

    def pump(work, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(work).result(timeout=1)

    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    monkeypatch.setattr(playback_player.app_core, "fetch_episode_stream", fetch)
    assert playback_player._fetch_player_fallback(data, KEY, exclude_sources=frozenset(("Failed",)))[0] == "selected"
    assert data == {"sources": []}


def test_cancelled_fallback_retires_worker_and_retry_is_fresh(monkeypatch):
    started, release = threading.Event(), threading.Event()
    checks, threads = [], []
    data = {"sources": []}

    def fetch(*args, **kwargs):
        checks.append(kwargs["cancel_check"])
        if len(checks) == 1:
            started.set()
            assert release.wait(2)
            assert checks[0]()
            kwargs["ep_data"]["sources"].append("late")
        else:
            assert not checks[-1]()
        return ("selected", "mirror", kwargs["ep_data"], [])

    def cancel(work, **kwargs):
        thread = threading.Thread(target=work)
        threads.append(thread)
        thread.start()
        assert started.wait(1)
        raise PreparationCancelled

    monkeypatch.setattr(playback_player.app_core, "fetch_episode_stream", fetch)
    monkeypatch.setattr(player_screen, "run_loading_work", cancel)
    try:
        with pytest.raises(PreparationCancelled):
            playback_player._fetch_player_fallback(data, KEY)
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=1)
            assert not thread.is_alive()
    assert data == {"sources": []}
    monkeypatch.setattr(player_screen, "run_loading_work", lambda work, **kw: work())
    assert playback_player._fetch_player_fallback(data, KEY)[0] == "selected"


def test_download_fallback_does_not_borrow_player_ui(monkeypatch):
    monkeypatch.setattr(player_screen, "run_loading_work", lambda *a, **kw: pytest.fail("Download entered player UI"))
    monkeypatch.setattr(playback_player.app_core, "fetch_episode_stream", lambda *a, **kw: None)
    assert playback_player._fetch_player_fallback({}, KEY, download=True) is None


def test_queued_extraction_does_not_start_after_cancellation(monkeypatch):
    from concurrent.futures import CancelledError
    from media_cli.core import source_batch

    started, release, cancelled = (threading.Event() for _ in range(3))
    calls = []

    def resolve(source):
        calls.append(source['sourceName'])
        started.set()
        assert release.wait(2)
        return []

    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(source_batch, '_executor', lambda: pool)
        batch = source_batch.SourceBatch(resolve, cancel_check=cancelled.is_set)
        try:
            batch.poll([{'sourceName': 'native'}, {'sourceName': 'yt-dlp'}], set(), timeout=0)
            assert started.wait(1)
            cancelled.set()
            release.set()
            futures = list(batch.futures.values())
            assert futures[0].result(timeout=1) == []
            with pytest.raises(CancelledError):
                futures[1].result(timeout=1)
            assert calls == ['native']
        finally:
            release.set()
            batch.close()


def test_accepted_batch_survives_foreground_owner_retirement(monkeypatch):
    from media_cli.core import source_batch

    cancelled = threading.Event()
    calls = []
    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(source_batch, '_executor', lambda: pool)
        batch = source_batch.SourceBatch(lambda source: calls.append(source) or [], cancel_check=cancelled.is_set)
        try:
            batch.detach_cancel_check()
            cancelled.set()
            assert batch.get({'sourceName': 'background'}) == []
            assert calls == [{'sourceName': 'background'}]
        finally:
            batch.close()
