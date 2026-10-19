import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from media_cli.core import streams


@pytest.mark.parametrize("outcome", ["valid", "dead", "superseded", "changed"])
def test_resolver_startup_validates_in_worker_without_lock_or_stale_publication(monkeypatch, outcome):
    from media_cli.core import storage

    owner = threading.current_thread()
    key = streams.make_stream_key("show", "1", "sub", "example", "best")
    original = {"link": "old", "source_name": "mirror", "resolution": "720", "validated_at": 1}
    entry = {"streams": [original], "state": "READY"}
    started, release = threading.Event(), threading.Event()
    monkeypatch.setattr(streams, "_streams_cache", {key: entry})
    monkeypatch.setattr(streams, "all_streams", [])
    monkeypatch.setattr(streams, "_streams_lock", threading.RLock())
    monkeypatch.setattr(streams, "_streams_generation", 0)
    monkeypatch.setattr(streams, "_active_stream_key", None)
    monkeypatch.setattr(streams, "_bg_generation", 0)
    monkeypatch.setattr(streams, "_bg_thread", None)
    monkeypatch.setattr(streams, "_bg_stats", {})
    monkeypatch.setattr(streams, "_start_revalidation_timer", lambda: None)
    monkeypatch.setattr(storage, "get_preferred_mirror", lambda _: None)
    monkeypatch.setattr(streams, "_episode_data_fn", None)

    def health(candidate, now):
        assert threading.current_thread() is not owner, "Startup must not validate on the UI thread"
        started.set()
        assert release.wait(2)
        candidate["validated_at"] = 100
        return outcome != "dead"

    monkeypatch.setattr(streams, "check_stream_health_and_refresh", health)
    try:
        streams.start_bg_resolve({"episode": {"sourceUrls": []}}, set(),
                                 "show", "1", "sub", "example", "best")
        assert started.wait(1)
        assert streams._streams_lock.acquire(timeout=0.2), "Validation must release the cache lock"
        try:
            assert streams.all_streams == [], "Unvalidated cache must not be published"
            if outcome == "superseded":
                streams._streams_generation += 1
                streams.all_streams.append({"link": "next-attempt"})
            elif outcome == "changed":
                original["link"] = "replacement"
        finally:
            streams._streams_lock.release()
    finally:
        release.set()
        if streams._bg_thread is not None:
            streams._bg_thread.join(timeout=2)
            assert not streams._bg_thread.is_alive()

    if outcome == "valid":
        assert streams.all_streams == [original]
        assert streams.all_streams[0] is original
        assert original["validated_at"] == 100
        assert entry["state"] == "READY"
    else:
        assert original["validated_at"] == 1
        if outcome == "superseded":
            assert streams.all_streams == [{"link": "next-attempt"}]
            assert entry["state"] == "REFRESHING"
        elif outcome == "dead":
            assert entry["streams"] == []
            assert entry["state"] == "FAILED"
        else:
            assert entry["streams"] == [original]
            assert original["link"] == "replacement"
            assert streams.all_streams == []


@pytest.mark.parametrize("change", ["none", "cancel", "append", "replace", "version", "mutate"])
def test_revalidation_releases_cache_lock_and_rejects_abandoned_or_changed_result(monkeypatch, change):
    key = streams.make_stream_key("show", "1", "sub", "example", "best")
    original = {"link": "old", "validated_at": 1}
    entry = {"streams": [original]}
    cache = {key: entry}
    started, release, cancelled = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(streams, "_streams_cache", cache)
    monkeypatch.setattr(streams, "_streams_lock", threading.RLock())

    def health(candidate, now):
        started.set()
        assert release.wait(2)
        candidate["validated_at"] = 100
        return True

    monkeypatch.setattr(streams, "check_stream_health_and_refresh", health)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(streams._stream_snapshot, "show", "1", "sub", "example", "best",
                             cancel_check=cancelled.is_set)
        try:
            if not started.wait(1):
                future.result(timeout=1)
                pytest.fail("Revalidation did not start")
            acquired = streams._streams_lock.acquire(timeout=0.2)
            assert acquired, "Network revalidation must not hold the cache lock"
            try:
                if change == "cancel":
                    cancelled.set()
                elif change == "append":
                    entry["streams"].append({"link": "new"})
                elif change == "replace":
                    cache[key] = {"streams": [{"link": "replacement"}]}
                elif change == "version":
                    entry["ep_data_version"] = "new"
                elif change == "mutate":
                    original["link"] = "refreshed"
            finally:
                streams._streams_lock.release()
        finally:
            release.set()
        result = future.result(timeout=1)
    if change == "none":
        assert result == [original]
        assert result[0] is original
        assert original["validated_at"] == 100
    else:
        assert result == []
        assert original["validated_at"] == 1
        assert cache[key]["streams"][-1]["link"] == {
            "cancel": "old", "append": "new", "replace": "replacement", "version": "old", "mutate": "refreshed",
        }[change]


def test_player_cache_loading_uses_worker_and_retires_its_cancellation_scope(monkeypatch):
    from media_cli.app import playback_player
    from media_cli.ui import player_screen

    owner = threading.current_thread()
    checks = []

    def snapshot(*args, cancel_check):
        assert threading.current_thread() is not owner
        assert not cancel_check()
        checks.append(cancel_check)
        return []

    def pump(work, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(work).result(timeout=1)

    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    monkeypatch.setattr(playback_player.app_core, "_stream_snapshot", snapshot)
    assert playback_player._load_player_cached_streams({}, ("show", "1", "sub", "example", "best")) == []
    assert checks[0]()
