"""Normal-search domain data becomes immutable, role-based UI data."""

from copy import deepcopy

import pytest

from media_cli.app.search_results_view import build_search_results_view
from media_cli.core.terminal import strip_ansi
from media_cli.domain.metadata import format_info_metadata_line
from media_cli.ui.styled_text import StyledText
from media_cli.ui.view_model import resolve_details


@pytest.fixture(autouse=True)
def no_fetch(monkeypatch):
    monkeypatch.setattr("media_cli.core.network.is_confirmed_offline", lambda: False)
    monkeypatch.setattr("media_cli.domain.titles.get_cached_title_language", lambda: "romaji")
    monkeypatch.setattr(
        "media_cli.app_core.ensure_episode_ids", lambda *a, **kw: pytest.fail("Builder fetched episodes")
    )
    monkeypatch.setattr(
        "media_cli.app_core.enrich_show_if_missing", lambda *a, **kw: pytest.fail("Builder fetched metadata")
    )


def show(identity="one", **changes):
    return dict(
        {
            "_id": identity,
            "name": "Example",
            "englishName": "Another name",
            "status": "FINISHED",
            "type": "TV",
            "episodeCount": 12,
            "_local_progress": 3,
            "thumbnail": "//images.test/cover.png",
            "airedStart": {"year": 2024},
            "score": 80,
        },
        **changes,
    )


def view(shows=(), **kwargs):
    return build_search_results_view(
        shows,
        provider_id="anikoto",
        provider_name="Anikoto",
        query="example",
        ttype="sub",
        prompt="Search Anime",
        **kwargs,
    )


def test_results_use_provider_identity_not_label_or_order():
    first, second = show("one"), show("two")
    before = view((first, second))
    after = view((second, first))
    assert [item.id for item in before.items] == [item.id for item in reversed(after.items)]
    assert before.items[0].label == before.items[1].label
    assert before.items[0].id != before.items[1].id
    assert before.items[0].reference is first
    assert before.items[0].image.url == "https://images.test/cover.png"


def test_details_keep_existing_text_and_semantic_metadata_roles_without_mutation():
    source = show()
    original = deepcopy(source)
    result = view((source,))
    details = resolve_details(result.details, result.items[0].details)
    assert details.title == "Example"
    assert details.secondary == "Another name"
    assert isinstance(details.metadata, StyledText)
    assert details.metadata.text == strip_ansi(format_info_metadata_line(source))
    assert any(run.role == "metadata.status" for run in details.metadata.runs)
    assert any(run.role == "muted" and run.text == "Watched" for run in details.metadata.runs)
    assert all("\033" not in run.text for run in details.metadata.runs)
    source["name"] = "Changed later"
    assert details.title == "Example"
    source["name"] = original["name"]
    assert source == original


@pytest.mark.parametrize(
    "loading,error,state", [(True, "", "loading"), (False, "", "empty"), (False, "Unavailable", "failed")]
)
def test_empty_search_states_have_no_fake_result_rows(loading, error, state):
    result = view(loading=loading, message="Searching..." if loading else "", error=error)
    assert result.state == state
    assert result.items == ()
    assert result.prompt == "Search Anime"


def test_partial_results_do_not_become_auto_selectable_until_ready():
    partial = view((show(),), loading=True, message="Updating metadata...", auto_single=True)
    assert partial.state == "loading"
    assert len(partial.items) == 1
    assert partial.items[0].selectable
    assert partial.selection_policy.auto_single
    assert view((show(),)).state == "ready"


def test_missing_cover_and_alternative_name_are_explicit():
    result = view((show(thumbnail="", englishName="", genres=["Adventure", "Comedy"]),))
    details = resolve_details(result.details, result.items[0].details)
    assert details.secondary == "Adventure • Comedy"
    assert result.items[0].image.state == "unavailable"
    result = view((show(thumbnail="", englishName=""),))
    assert resolve_details(result.details, result.items[0].details).secondary == "No alternative title"


def test_duplicate_or_missing_provider_ids_fail_instead_of_using_row_numbers():
    with pytest.raises(ValueError, match="Duplicate item IDs"):
        view((show(), show()))
    with pytest.raises(ValueError, match="identity"):
        view(({"name": "No ID"},))


@pytest.mark.parametrize("provider_id", ["anikoto", "animexin", "allanime"])
def test_same_projection_works_for_each_normal_provider_and_numeric_identity(provider_id):
    result = build_search_results_view(
        (show(0),), provider_id=provider_id, provider_name=provider_id, query="example", ttype="dub", prompt="Search"
    )
    assert result.items[0].id == f"{provider_id}:0"
    assert "DUB" in resolve_details(result.details, result.items[0].details).metadata.text
