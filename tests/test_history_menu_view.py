"""History rows project ready application data without fetching or mutating it."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from media_cli.ui.view_model import Blank, DetailLines, resolve_details


@pytest.fixture(autouse=True)
def no_preparation(monkeypatch):
    monkeypatch.setattr("media_cli.domain.titles.get_cached_title_language", lambda: "romaji")
    for name in ("load_history", "load_downloads_db", "get_local_progress"):
        monkeypatch.setattr(f"media_cli.core.storage.{name}", lambda *a, **kw: pytest.fail("History row read storage"))
    for name in ("ensure_episode_ids", "enrich_show_if_missing", "prepare_show_display_state"):
        monkeypatch.setattr(f"media_cli.app_core.{name}", lambda *a, **kw: pytest.fail("History row prepared data"))


def entry(identity="one", provider="example", translation="sub", **changes):
    return {
        "show": {"_id": identity, "_provider": provider, "name": "Example", "englishName": "Another name",
              "thumbnail": "//images.test/cover.png", "_local_progress": 3, "episodeCount": 12,
              "status": "FINISHED", "type": "TV"},
        "translation_type": translation, "has_new_release": False, **changes,
    }


def build(entries, **kwargs):
    from media_cli.app.history_menu_view import build_history_items

    return build_history_items(entries, **kwargs)


def test_history_identity_includes_provider_title_and_translation_not_position_or_label():
    entries = (entry(), entry(provider="other"), entry(translation="dub"))
    rows = build(entries)
    assert len({row.id for row in rows}) == 3
    assert tuple(row.id for row in build(entries[::-1])) == tuple(row.id for row in rows[::-1])
    assert rows[0].label_text == "Example"
    assert rows[2].label_text == "Example (dub)"
    assert rows[0].reference.provider_id == "example"
    assert rows[0].reference.show_id == "one"
    assert rows[2].reference.translation_type == "dub"


def test_history_rows_snapshot_metadata_and_do_not_mutate_input():
    source = entry(has_new_release=True)
    original = deepcopy(source)
    rows = build([source])
    fields = resolve_details(DetailLines(Blank, Blank, Blank), rows[0].details)
    assert fields.title == "Example" and fields.secondary == "Another name"
    assert "Watched 3/12" in fields.metadata.text
    assert source == original
    source["show"]["name"] = "Changed"
    source["has_new_release"] = False
    assert rows[0].label_text == "Example" and rows[0].prefix_text == "\u25c6 "
    assert rows[0].image.url == "https://images.test/cover.png"


def test_new_release_marker_uses_an_application_theme_role():
    from media_cli.state.theme import APPLICATION_THEME
    from media_cli.ui.row_text import render_row_text

    new, old = build((entry(has_new_release=True), entry(identity="two")))
    assert new.prefix.runs[0].role == "history.new_release"
    assert old.prefix_text == ""
    assert "\033" not in new.prefix_text
    assert APPLICATION_THEME.prefix(APPLICATION_THEME.resolve_style("history.new_release")) in render_row_text(
        new.prefix, columns=20, theme=APPLICATION_THEME,
    )


def test_cover_policy_only_describes_available_images():
    assert build((entry(),), show_cover=False)[0].image is None
    source = entry()
    source["show"].pop("thumbnail")
    assert build((source,))[0].image.state == "unavailable"


def test_history_progress_and_images_use_each_entries_translation():
    source = entry(translation="dub")
    fields = resolve_details(DetailLines(Blank, Blank, Blank), build((source,))[0].details)
    assert "DUB" in fields.metadata.text
    assert build((source,))[0].image.key == build((entry(),))[0].image.key


@pytest.mark.parametrize("entries", [({},), ({"show": None},), (entry(identity=""),), (entry(has_new_release="yes"),)])
def test_invalid_history_row_data_fails_early(entries):
    with pytest.raises((TypeError, ValueError)):
        build(entries)


def test_duplicate_history_identities_fail_early():
    with pytest.raises(ValueError, match="Duplicate"):
        build((entry(), entry()))


def test_empty_history_projection_is_an_immutable_empty_tuple():
    assert build(()) == ()


def test_history_screen_assembles_tabs_actions_and_counts_without_filtering_input():
    from media_cli.app.history_menu_view import build_history_view

    entries = (entry(), entry(identity="two", provider="other"))
    view = build_history_view(entries, mode="Up to date", total_count=12, now=120)
    assert view.prompt == "Watch History"
    assert tuple(tab.label for tab in view.tabs) == ("Active", "Up to date", "Completed", "All")
    assert view.active_tab_id == "Up to date"
    assert view.total_count == 12 and len(view.items) == 2
    actions = {action.id: action.keys for action in view.actions}
    assert actions["refresh_title"] == ("CTRL_R",)
    assert actions["refresh_view"] == ("F5", "ALT_R")
    assert actions["delete"] == ("DELETE", "CTRL_D")
    assert not view.selection_policy.auto_single


def test_history_footer_context_is_common_plus_selected_entry_data():
    from media_cli.app.history_menu_view import build_history_view
    from media_cli.ui.view_presentation import ViewPresentation
    from media_cli.ui.view_session import ViewSession

    view = build_history_view((entry(timestamp=60), entry(identity="two", provider="other", timestamp=120)),
                              mode="All", total_count=2, now=180, incognito=True)
    presentation = ViewPresentation(ViewSession(view), SimpleNamespace(enabled=lambda: False))
    assert presentation._footer(item_id=view.items[0].id).context == (
        "History", "INCOGNITO (READ-ONLY)", "Example", "Updated 2m ago",
    )
    assert "Other" in presentation.plain_details(view.items[1].id)[-1]
    assert "Updated 1m ago" in presentation.plain_details(view.items[1].id)[-1]
    assert not any(action.id == "delete" for action in view.actions)


@pytest.mark.parametrize("mode,message", [
    ("Active", "No active history entries."), ("Up to date", "No up-to-date titles."),
    ("Completed", "No completed titles."), ("All", "Watch history is empty."),
])
def test_history_empty_states_have_no_placeholder_result(mode, message):
    from media_cli.app.history_menu_view import build_history_view

    view = build_history_view((), mode=mode, total_count=4)
    assert view.state == "empty" and view.items == ()
    assert view.message == message


@pytest.mark.parametrize("kwargs", [
    {"mode": "Unknown"}, {"incognito": "yes"}, {"total_count": True}, {"total_count": 0}, {"total_count": None},
])
def test_history_screen_rejects_invalid_modes_flags_and_counts(kwargs):
    from media_cli.app.history_menu_view import build_history_view

    with pytest.raises((TypeError, ValueError)):
        build_history_view((entry(),), **({"mode": "All", "total_count": 1} | kwargs))


def test_history_provider_stays_visible_after_details_lookup_fails():
    source = entry(provider="anidb")
    source["show"]["_provider_name"] = "AniDB.app"
    before = build([source])[0]
    source["show"]["_has_provider_link"] = False
    after = build([source])[0]
    assert after.label == before.label
    assert after.footer_context == before.footer_context
    assert after.footer_context[0] == "AniDB.app"


def test_history_information_uses_shared_metadata_colors():
    from media_cli.state.theme import MINT_SCREEN_THEME
    from media_cli.ui.detail_panel import render_detail_lines
    fields = resolve_details(build([entry()])[0].details)
    line = render_detail_lines(primary=fields.title, secondary=fields.secondary,
                               details=fields.metadata, width=160, theme=MINT_SCREEN_THEME)[2]
    assert "\033[38;5;114mFINISHED" in line
    assert "\033[38;5;114mWatched" in line
    assert "\033[38;5;240m • " in line


def test_torrent_history_recovers_name_and_uses_torrent_panel_without_cover():
    record = entry(identity="a" * 40, provider="torrent")
    record["show"].update(name="a" * 40, _torrent={
        "infohash": "a" * 40, "name": "Example.S01.Dual.Audio.mkv",
        "source": "nyaa", "seeders": 22, "leechers": 3, "size_bytes": 1000000000,
    })
    before = deepcopy(record)
    item = build([record])[0]
    assert item.label_text == "Example.S01.Dual.Audio.mkv"
    assert item.image is None
    text = "\n".join("".join(run.text for run in line.runs) for line in item.info_lines)
    assert "S 22 · L 3" in text
    assert "Watched 3" in text
    assert "No genres" not in text and "★" not in text
    assert record == before


def test_history_smart_filters_use_loaded_metadata_and_activity_counts():
    from media_cli.app.history_menu_view import build_history_view
    from media_cli.app.history_activity import HistoryActivity
    from media_cli.ui.view_model import visible_items
    source = entry(provider='anipm', has_new_release=True)
    source['show'].update(genres=['Magic', 'Fantasy'], airedStart={'year': 2020}, season='SPRING')
    activity = HistoryActivity({('anipm', 'one'): 2})
    view = build_history_view([source], mode='All', total_count=1, activity=activity)
    query = 'provider:anipm,anikoto year:>=2008 genre:magic status:finished type:tv season:spring watched:>=3 downloaded:>0 new:true'
    assert len(visible_items(view, query)) == 1
    assert not visible_items(view, 'new:false')
    assert not visible_items(view, 'downloaded:0')
    assert len(visible_items(view, 'title:"Another name" translation:sub')) == 1
    source['show']['genres'].append('Romance')
    assert not visible_items(view, 'genre:romance')


def test_history_missing_metadata_is_not_invented():
    from media_cli.app.history_menu_view import build_history_view
    from media_cli.app.history_activity import HistoryActivity
    from media_cli.ui.view_model import visible_items
    view = build_history_view([entry()], mode='All', total_count=1, activity=HistoryActivity({}))
    assert not visible_items(view, 'year:>2000')
    assert not visible_items(view, 'genre:fantasy')
    assert len(visible_items(view, 'downloaded:0 new:false')) == 1
    assert len(visible_items(view, 'Exm')) == 1
