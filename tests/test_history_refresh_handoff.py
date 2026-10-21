"""Background refreshes may prepare copies, never mutate displayed history."""

from copy import deepcopy
from threading import Thread

import pytest


def entry(identity="one"):
    return dict(show=dict(_id=identity, _provider="example", name="Example", availableEpisodes={"sub": 10}),
                translation_type="sub", episode="9")


def inbox():
    from media_cli.app.history_refresh_handoff import HistoryRefreshInbox

    return HistoryRefreshInbox()


def refresh(entries):
    for item in entries:
        item["show"]["availableEpisodes"]["sub"] = 11
    return True


def test_owner_merge_preserves_newer_progress_and_unrelated_live_metadata():
    from media_cli.app.history_refresh_handoff import merge_history_refresh

    source = entry()
    source["show"].update(_poster_state="loading", _action_feedback="current", genres=["Old"])
    source["show"]["availableEpisodes"]["dub"] = 2
    work = inbox()
    tickets = work.begin((source,))
    work.prepare(tickets, refresh)
    source["show"].update(_poster_state="ready", _action_feedback="new", genres=["New"])
    source["show"]["availableEpisodes"]["dub"] = 3
    latest = deepcopy(source)
    latest.update(episode="11", progress=11, timestamp=123)
    work.drain(lambda update: merge_history_refresh(source, update, latest))

    assert source["episode"] == "11"
    assert source["progress"] == 11
    assert source["timestamp"] == 123
    assert source["show"]["availableEpisodes"] == {"sub": 11, "dub": 3}
    assert source["show"]["genres"] == ["New"]
    assert source["show"]["_poster_state"] == "ready"
    assert source["show"]["_action_feedback"] == "new"
    assert source["has_new_release"] is False


def test_owner_merge_retains_new_release_when_current_progress_is_behind():
    from media_cli.app.history_refresh_handoff import merge_history_refresh

    source = entry()
    source.update(episode="10", progress=10, was_caught_up=True)
    work = inbox()
    tickets = work.begin((source,))
    work.prepare(tickets, refresh)
    work.drain(lambda update: merge_history_refresh(source, update, deepcopy(source)))

    assert source["show"]["availableEpisodes"]["sub"] == 11
    assert source["has_new_release"] is True


def test_worker_prepares_detached_snapshot_and_owner_accepts_it():
    source = entry()
    original = deepcopy(source)
    work = inbox()
    tickets = work.begin((source,))
    source["episode"] = "10"
    worker = Thread(target=work.prepare, args=(tickets, refresh))
    worker.start()
    worker.join(2)
    assert not worker.is_alive()
    assert source["show"] == original["show"]
    accepted = []
    assert work.drain(accepted.append) == 1
    assert accepted[0].reference.show_id == "one"
    assert accepted[0].entry["show"]["availableEpisodes"]["sub"] == 11
    assert source["episode"] == "10"


def test_newer_request_wins_and_each_result_is_accepted_once():
    work = inbox()
    old = work.begin((entry(),))
    new = work.begin((entry(),))
    work.prepare(new, refresh)
    work.prepare(old, refresh)
    accepted = []
    assert work.drain(accepted.append) == 1
    assert work.drain(accepted.append) == 0


@pytest.mark.parametrize("close", [False, True])
def test_deleted_or_closed_view_cannot_accept_late_results(close):
    work = inbox()
    tickets = work.begin((entry(),))
    work.prepare(tickets, refresh)
    if close:
        work.close()
    else:
        work.invalidate(tickets[0].reference)
    assert work.drain(lambda result: pytest.fail("Accepted obsolete result")) == 0


def test_failed_refresh_preserves_displayed_data_and_reports_completion():
    work, source = inbox(), entry()
    tickets = work.begin((source,))

    def fail(entries):
        entries[0]["show"]["availableEpisodes"]["sub"] = 1
        raise RuntimeError("Provider unavailable")

    work.prepare(tickets, fail)
    accepted = []
    assert work.drain(accepted.append) == 1
    assert accepted[0].entry is None and accepted[0].error == "Provider unavailable"
    assert source["show"]["availableEpisodes"]["sub"] == 10


def test_only_owner_may_begin_or_accept_work():
    work = inbox()
    errors = []

    def wrong_thread():
        for operation in (lambda: work.begin((entry(),)), lambda: work.drain(lambda result: None)):
            try:
                operation()
            except RuntimeError as error:
                errors.append(str(error))

    worker = Thread(target=wrong_thread)
    worker.start()
    worker.join(2)
    assert len(errors) == 2 and all("owner" in error for error in errors)


def test_result_copies_worker_data_before_publication():
    work = inbox()
    held = []

    def keep(entries):
        held.extend(entries)
        return refresh(entries)

    work.prepare(work.begin((entry(),)), keep)
    held[0]["show"]["availableEpisodes"]["sub"] = 1
    accepted = []
    work.drain(accepted.append)
    assert accepted[0].entry["show"]["availableEpisodes"]["sub"] == 11


def test_batch_drops_only_superseded_entries():
    work = inbox()
    batch = work.begin((entry("one"), entry("two")))
    selected = work.begin((entry("one"),))
    work.prepare(batch, refresh)
    accepted = []
    assert work.drain(accepted.append) == 1
    assert accepted[0].reference.show_id == "two"
    work.prepare(selected, refresh)
    assert work.drain(accepted.append) == 1
    assert accepted[1].reference.show_id == "one"


def test_foreign_requests_and_invalid_refresh_results_fail_early():
    first, second = inbox(), inbox()
    tickets = first.begin((entry(),))
    with pytest.raises(ValueError, match="inbox"):
        second.prepare(tickets, refresh)
    with pytest.raises(TypeError, match="boolean"):
        first.prepare(tickets, lambda entries: None)


def test_closed_inbox_does_not_start_provider_work():
    work = inbox()
    tickets = work.begin((entry(),))
    work.close()
    work.prepare(tickets, lambda entries: pytest.fail("Started work after close"))


def test_failure_after_partial_batch_mutation_still_completes_all_requests():
    work = inbox()
    tickets = work.begin((entry("one"), entry("two")))

    def fail(entries):
        entries.pop()
        raise RuntimeError("Interrupted batch")

    work.prepare(tickets, fail)
    accepted = []
    assert work.drain(accepted.append) == 2
    assert all(update.entry is None and update.error == "Interrupted batch" for update in accepted)
