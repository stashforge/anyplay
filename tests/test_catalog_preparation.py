from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from copy import deepcopy
from dataclasses import FrozenInstanceError
from threading import Event, Lock, current_thread
from types import SimpleNamespace

import pytest

from media_cli.core.api import ProviderDependencyError
from media_cli.services import catalog


@pytest.fixture(autouse=True)
def isolated_catalog_workers(monkeypatch):
    with ThreadPoolExecutor(max_workers=4) as executor:
        monkeypatch.setattr(catalog, "_catalog_executor", executor)
        monkeypatch.setattr(catalog, "_in_flight_catalog_tasks", {})
        monkeypatch.setattr(catalog, "enrich_show_if_missing", lambda show: None)
        monkeypatch.setattr(catalog, "_download_catalog_for_show", lambda *args: ([], {}, None, None))
        yield


def make_show(**values):
    return {"_id": "title-1", "_provider": "allanime", "name": "Demo", **values}


def loaded_catalog():
    return {
        "state": "loaded",
        "ids": ["1", "2"],
        "labels": {"1": "First", "2": "Second"},
        "episodes_map": {"1": {"title": "First", "sources": ["source-1"]}},
        "by_number": {"1": "1", "2": "2"},
    }


def install_provider(monkeypatch, fetch=None):
    monkeypatch.setattr(
        catalog,
        "_provider_for_title",
        lambda show: SimpleNamespace(episode_catalog=fetch or (lambda *args: loaded_catalog())),
    )


def test_timed_out_fetch_cannot_later_mutate_source(monkeypatch):
    started, release, finished = Event(), Event(), Event()
    show = make_show(
        availableEpisodes={"dub": 4},
        _episode_ids_by_ttype={"dub": ["dub-1"]},
        _episodes_map_by_ttype={"dub": {"dub-1": {"sources": ["dub-source"]}}},
    )
    nested_counts = show["availableEpisodes"]

    def fetch(*args):
        started.set()
        assert release.wait(5)
        return loaded_catalog()

    install_provider(monkeypatch, fetch)
    original_result = Future.result

    def time_out(future, timeout=None):
        if timeout == 30:
            assert started.wait(5)
            future.add_done_callback(lambda completed: finished.set())
            raise TimeoutError()
        return original_result(future, timeout)

    monkeypatch.setattr(Future, "result", time_out)
    try:
        assert catalog.ensure_episode_ids(show, "sub") == []
        assert show["_episode_catalog_state"] == "unavailable"
        assert "timed out" in show["_episode_catalog_error"]
        after_timeout = deepcopy(show)
    finally:
        release.set()
    assert finished.wait(5)
    assert show == after_timeout
    assert nested_counts == {"dub": 4}


def test_deduplicated_callers_receive_catalogs_in_their_own_dicts(monkeypatch):
    started, both_waiting, release = Event(), Event(), Event()
    lock = Lock()
    waiters = 0
    fetches = []
    first = make_show(thumbnail="first-cover", availableEpisodes={"sub": 2, "dub": 3})
    second = make_show(thumbnail="second-cover", availableEpisodes={"dub": 8})

    def fetch(*args):
        fetches.append(args)
        started.set()
        assert release.wait(5)
        return loaded_catalog()

    install_provider(monkeypatch, fetch)
    original_result = Future.result

    def track_waiters(future, timeout=None):
        nonlocal waiters
        if timeout == 30:
            with lock:
                waiters += 1
                if waiters == 2:
                    both_waiting.set()
        return original_result(future, timeout)

    monkeypatch.setattr(Future, "result", track_waiters)
    with ThreadPoolExecutor(max_workers=2) as callers:
        first_call = callers.submit(catalog.ensure_episode_ids, first, "sub")
        try:
            assert started.wait(5)
            second_call = callers.submit(catalog.ensure_episode_ids, second, "sub")
            assert both_waiting.wait(5)
        finally:
            release.set()
        assert first_call.result(5) == ["1", "2"]
        assert second_call.result(5) == ["1", "2"]

    assert len(fetches) == 1
    for show in (first, second):
        assert show["_episode_ids"] == ["1", "2"]
        assert show["_episode_labels"] == {"1": "First", "2": "Second"}
        assert show["_episodes_by_number"] == {"1": "1", "2": "2"}
        assert show["_episodes_map_by_ttype"]["sub"]["1"]["sources"] == ["source-1"]
    assert first["availableEpisodes"] == {"sub": 2, "dub": 3}
    assert second["availableEpisodes"] == {"sub": 2, "dub": 8}
    assert second["thumbnail"] == "second-cover"
    second["_episodes_map"]["1"]["sources"].append("second-only")
    assert first["_episodes_map"]["1"]["sources"] == ["source-1"]


def test_prepare_is_detached_and_can_be_cancelled_by_not_committing(monkeypatch):
    install_provider(monkeypatch)
    show = make_show(
        availableEpisodes={"dub": 4},
        _episodes_map_by_ttype={"dub": {"1": {"sources": ["dub-source"]}}},
    )
    original = deepcopy(show)
    result = catalog.prepare_episode_selection(show, "sub")

    assert result.episode_ids == ("1", "2")
    assert result.show["_episode_ids"] == ["1", "2"]
    assert show == original
    with pytest.raises(FrozenInstanceError):
        result.episode_ids = ()
    result.show["_episodes_map_by_ttype"]["dub"]["1"]["sources"].append("prepared-only")
    assert show == original
    show["availableEpisodes"]["dub"] = 9
    assert result.show["availableEpisodes"]["dub"] == 4


def test_commit_applies_delta_without_replacing_background_enrichment(monkeypatch):
    install_provider(monkeypatch)

    def enrich(detached):
        detached["description"] = "Fetched description"
        detached["season"]["year"] = 2026
        detached.pop("stale", None)

    monkeypatch.setattr(catalog, "enrich_show_if_missing", enrich)
    show = make_show(
        thumbnail="old-cover",
        availableEpisodes={"dub": 4},
        season={"year": 2025, "name": "SPRING"},
        stale="obsolete",
        _episode_catalog_error="old error",
    )
    result = catalog.prepare_episode_selection(show, "sub")
    show["thumbnail"] = "new-cover"
    show["banner"] = "new-banner"
    show["availableEpisodes"]["dub"] = 9
    show["season"]["name"] = "FALL"
    show["background_metadata"] = {"ready": True}

    catalog.commit_episode_selection(show, result)

    assert show["_episode_catalog_state"] == "loaded"
    assert show["_episode_ids"] == ["1", "2"]
    assert "_episode_catalog_error" not in show
    assert "stale" not in show
    assert show["description"] == "Fetched description"
    assert show["season"] == {"year": 2026, "name": "FALL"}
    assert show["availableEpisodes"] == {"sub": 2, "dub": 9}
    assert show["thumbnail"] == "new-cover"
    assert show["banner"] == "new-banner"
    assert show["background_metadata"] == {"ready": True}
    show["_episodes_map"]["1"]["sources"].append("committed-only")
    assert result.show["_episodes_map"]["1"]["sources"] == ["source-1"]


@pytest.mark.parametrize("operation", ["_perform_catalog_fetch", "prepare_episode_selection"])
def test_dependency_error_propagates_without_terminal_exit(monkeypatch, operation):
    error = ProviderDependencyError("Missing provider dependency")

    def fetch(*args):
        raise error

    def forbidden_exit(*args):
        pytest.fail("Catalog preparation must not exit or restore the terminal")

    install_provider(monkeypatch, fetch)
    monkeypatch.setattr("media_cli.ui.display.fatal_terminal_exit", forbidden_exit)
    show = make_show()
    with pytest.raises(ProviderDependencyError, match="Missing provider dependency"):
        getattr(catalog, operation)(show, "sub")


def test_noninteractive_dependency_error_is_handled_by_caller(monkeypatch):
    def fetch(*args):
        raise ProviderDependencyError("Missing dependency")

    def terminal_exit(message):
        pytest.fail("The CLI owner must handle the dependency error after cleanup")

    install_provider(monkeypatch, fetch)
    monkeypatch.setattr("media_cli.ui.display.fatal_terminal_exit", terminal_exit)
    with pytest.raises(ProviderDependencyError, match="Missing dependency"):
        catalog.ensure_episode_ids(make_show(), "sub")


@pytest.mark.parametrize(
    "show, expected",
    [
        ({}, ()),
        (make_show(_episode_ids=["2", "1"], _episode_ids_ttype="sub"), ("2", "1")),
        (make_show(_episode_ids_by_ttype={"sub": ["1", "2"]}), ("1", "2")),
        (make_show(_id="local:demo", _downloaded_episodes=["1", "2"], _folder_name="Demo"), ("1", "2")),
        (make_show(_provider=None, _episode_ids=["1", "2"], _folder_name="Demo"), ("1", "2")),
        (make_show(_id="local:demo", availableEpisodesDetail={"sub": ["1", "2"]}), ("1", "2")),
    ],
)
def test_prepare_preserves_cached_and_local_semantics_without_mutation(monkeypatch, show, expected):
    def unexpected_fetch(*args):
        pytest.fail("Cached/local episodes must not fetch a provider catalog")

    install_provider(monkeypatch, unexpected_fetch)
    original = deepcopy(show)
    result = catalog.prepare_episode_selection(show, "sub")
    assert result.episode_ids == expected
    assert show == original
    catalog.commit_episode_selection(show, result)
    assert catalog.ensure_episode_ids(show, "sub") == list(expected)


def test_download_labels_do_not_become_online_provider_catalogue(monkeypatch):
    from media_cli.core.storage import sanitize_show_for_history

    ids = [f"https://provider.test/episode-{i}" for i in range(1, 5)]
    labels = {eid: f"{i} [{i + 100}]" for i, eid in enumerate(ids, 1)}
    calls = []

    def fetch(*args):
        calls.append(args)
        return {"state": "loaded", "ids": ids, "labels": labels}

    install_provider(monkeypatch, fetch)
    show = make_show(_downloaded_episodes=["2", "3"], _folder_name="Demo",
                     availableEpisodes={"sub": 3}, _provider_catalog_checked_at=9999999999)
    for _ in range(2):
        assert catalog.ensure_episode_ids(show, "sub") == ids
        assert show["_episode_labels"] == labels
        show = sanitize_show_for_history(show)
    assert len(calls) == 2


def test_translation_catalogue_wins_over_download_labels(monkeypatch):
    ids = ["https://provider.test/one", "https://provider.test/two"]
    show = make_show(_downloaded_episodes=["1"], _folder_name="Demo",
                     _episode_ids_by_ttype={"sub": ids})
    install_provider(monkeypatch, lambda *args: pytest.fail("Valid provider IDs should be reused"))
    assert catalog.ensure_episode_ids(show, "sub") == ids


def test_history_does_not_persist_runtime_download_labels():
    from media_cli.core.storage import sanitize_show_for_history

    show = make_show(_downloaded_episodes=["2", "3"], _folder_name="Demo", downloadedCount=2)
    stored = sanitize_show_for_history(show)
    assert "_downloaded_episodes" not in stored
    assert show["_downloaded_episodes"] == ["2", "3"]


def test_unavailable_catalog_is_only_applied_on_commit(monkeypatch):
    install_provider(monkeypatch, lambda *args: {"state": "unavailable", "ids": [], "error": "Unavailable"})
    show = make_show(_episode_ids=[], _episode_catalog_state="loaded")
    original = deepcopy(show)
    result = catalog.prepare_episode_selection(show, "sub")
    assert result.episode_ids == ()
    assert show == original
    catalog.commit_episode_selection(show, result)
    assert show["_episode_catalog_state"] == "unavailable"
    assert show["_episode_catalog_error"] == "Unavailable"
    assert "_episode_ids" not in show


def test_unavailable_result_clears_each_callers_stale_episode_ids(monkeypatch):
    install_provider(monkeypatch, lambda *args: {"state": "unavailable", "ids": [], "error": "Unavailable"})
    first = make_show(_episode_catalog_state="unavailable", _episode_catalog_error="Unavailable")
    result = catalog.prepare_episode_selection(first, "sub")
    second = make_show(_episode_ids=["old-dub"], _episode_ids_ttype="dub", _episode_catalog_error="Old error")

    catalog.commit_episode_selection(second, result)

    assert second["_episode_catalog_state"] == "unavailable"
    assert second["_episode_catalog_error"] == "Unavailable"
    assert "_episode_ids" not in second


def test_failed_task_can_be_retried_before_cleanup_callback_runs(monkeypatch):
    def fail_fetch(*args):
        raise ValueError("Temporary failure")

    install_provider(monkeypatch, fail_fetch)
    monkeypatch.setattr(catalog, "_forget_catalog_task", lambda *args: None)
    show = make_show()
    assert catalog.ensure_episode_ids(show, "sub") == []
    assert show["_episode_catalog_error"] == "Temporary failure"

    install_provider(monkeypatch)
    assert catalog.ensure_episode_ids(show, "sub") == ["1", "2"]
    assert show["_episode_catalog_state"] == "loaded"
    assert "_episode_catalog_error" not in show


def test_direct_fetch_signature_and_status_callback_remain_compatible(monkeypatch):
    install_provider(monkeypatch)
    statuses = []
    show = make_show()

    assert catalog._perform_catalog_fetch(show, "sub", statuses.append, "Fetching catalog") == ["1", "2"]
    assert show["_episode_ids"] == ["1", "2"]
    assert statuses[-1] == "Fetching catalog"
