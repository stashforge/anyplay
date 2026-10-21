"""Normal search supplies typed data and consumes stable-ID selection events."""

from types import SimpleNamespace

import pytest

from media_cli.app.search_results_controller import SearchResultsController
from media_cli.context import UiState
from media_cli.ui.view_session import UiEvent


@pytest.fixture(autouse=True)
def local_metadata(monkeypatch):
    monkeypatch.setattr("media_cli.core.network.is_confirmed_offline", lambda: False)
    monkeypatch.setattr("media_cli.domain.titles.get_cached_title_language", lambda: "romaji")


def source():
    data = SimpleNamespace(shows=[], loading="Searching...", done=False, error="")
    return data, (
        lambda query: ([], "unused formatted header", data.done),
        lambda: data.shows,
        lambda: data.loading,
        lambda: data.error,
    )


def controller(readers, *, select=lambda show: True, info=lambda show: None, ui=None):
    return SearchResultsController(
        *readers,
        ui=ui or UiState(),
        provider_id="anikoto",
        provider_name="Anikoto",
        query="example",
        ttype="sub",
        prompt="Search Anime",
        auto_single=True,
        select=select,
        information=info,
    )


def send(control, kind, item_id=None, value=None):
    return control.session.dispatch(
        UiEvent(kind, control.session.current.id, control.session.generation, item_id, value)
    )


def publish(control):
    for command in control.updates(force=True):
        control.session.apply(command)


def test_live_rows_keep_cursor_on_identity_when_source_reorders():
    data, readers = source()
    control = controller(readers)
    assert control.session.current.state == "loading"
    data.shows = [{"_id": "one", "name": "Same"}, {"_id": "two", "name": "Same"}]
    publish(control)
    control.session.position.selected_id = "anikoto:two"
    data.shows.reverse()
    publish(control)
    assert control.session.position.selected_id == "anikoto:two"
    assert control.session.current.items[0].id == "anikoto:two"
    assert not control.session.exited


def test_selection_uses_displayed_identity_not_latest_source_index():
    data, readers = source()
    one, two = {"_id": "one", "name": "Same"}, {"_id": "two", "name": "Same"}
    data.shows, data.done, data.loading = [one, two], True, ""
    chosen = []
    control = controller(readers, select=lambda show: chosen.append(show) or True)
    data.shows.reverse()
    send(control, "select", "anikoto:one")
    assert chosen == [one]
    assert control.session.result.kind == "selected"
    assert control.session.result.show is one


def test_failed_or_cancelled_selection_retains_filter_and_cursor():
    data, readers = source()
    data.shows, data.done, data.loading = [{"_id": "one", "name": "Example"}], True, ""
    control = controller(readers, select=lambda show: False)
    control.session.position.query = "exa"
    send(control, "select", "anikoto:one")
    assert not control.session.exited
    assert control.session.position.query == "exa"
    assert control.session.position.selected_id == "anikoto:one"


@pytest.mark.parametrize("kind", ["back", "quit"])
def test_back_and_quit_are_distinct_typed_outcomes(kind):
    _, readers = source()
    control = controller(readers)
    send(control, kind)
    assert control.session.result.kind == kind


def test_finished_empty_search_returns_to_query_entry_without_fake_rows():
    data, readers = source()
    control = controller(readers)
    data.done, data.loading, data.error = True, "", "Provider unavailable"
    publish(control)
    assert control.session.result.kind == "empty"
    assert control.session.result.error == "Provider unavailable"


def test_information_uses_domain_record_not_synthetic_cover_show():
    data, readers = source()
    show = {"_id": "one", "name": "Example"}
    data.shows, data.done, data.loading = [show], True, ""
    information = []
    control = controller(readers, info=information.append)
    send(control, "action", "anikoto:one", "information")
    assert information == [show]
    assert not control.session.exited


def test_completed_visible_cover_is_retained_only_for_same_item_and_url():
    data, readers = source()
    show = {"_id": "one", "name": "Example", "thumbnail": "https://images.test/a"}
    data.shows, data.done, data.loading = [show], True, ""
    ui = UiState()
    ui.hovered_show_obj = {
        "_image_key": "anikoto:one",
        "thumbnail": show["thumbnail"],
        "_poster_status": "ready",
        "_poster_raw": "COVER",
    }
    control = controller(readers, ui=ui)
    send(control, "select", "anikoto:one")
    assert show["_poster_raw"] == "COVER"
