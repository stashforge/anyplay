"""Episode presentation consumes resolved state without scanning or fetching."""

from copy import deepcopy

import pytest

from media_cli.app.episode_menu_view import EpisodeMenuOption, build_episode_menu_view
from media_cli.state.theme import APPLICATION_THEME
from media_cli.ui.row_text import render_row_text
from media_cli.ui.view_model import Inherit, resolve_details


@pytest.fixture(autouse=True)
def no_preparation(monkeypatch):
    monkeypatch.setattr("media_cli.domain.titles.get_cached_title_language", lambda: "romaji")
    for name in ("ensure_episode_ids", "get_local_progress", "prepare_show_display_state"):
        monkeypatch.setattr(f"media_cli.app_core.{name}", lambda *a, **kw: pytest.fail("Builder fetched state"))
    monkeypatch.setattr("media_cli.core.storage.load_downloads_db", lambda: pytest.fail("Builder scanned downloads"))


def build(options, **kwargs):
    return build_episode_menu_view(
        {"_id": "one", "name": "Example", "englishName": "Another name", "_local_progress": 1},
        options, view_id="episodes:example:one", image_key="example:one", ttype="sub", **kwargs,
    )


def test_episode_identity_survives_order_and_label_changes():
    first = build((EpisodeMenuOption("one", "Episode 1"), EpisodeMenuOption("two", "Episode 2")))
    second = build((EpisodeMenuOption("two", "Finale"), EpisodeMenuOption("one", "Opening")))
    assert first.items[0].id == second.items[1].id
    assert second.items[0].reference == "two"
    assert second.items[0].label_text == "Finale"


@pytest.mark.parametrize("watched,downloaded,local_only,label,hint", [
    (False, False, False, "Episode 1", ""),
    (True, False, False, "Episode 1", "watched"),
    (False, True, False, "Episode 1  \u2714", "ready to watch"),
    (True, True, False, "Episode 1  \u2714", "watched \u2022 ready to watch"),
    (False, False, True, "Episode 1", "\u2718 not downloaded"),
    (True, False, True, "Episode 1", "watched \u2022 not downloaded"),
])
def test_resolved_status_flags_produce_existing_labels_and_hints(watched, downloaded, local_only, label, hint):
    view = build((EpisodeMenuOption("one", "Episode 1", watched=watched, downloaded=downloaded),),
                 local_only=local_only)
    row = view.items[0]
    assert row.label_text == label and row.hint_text == hint
    for content in (row.label, row.hint):
        assert "\033" not in (content if isinstance(content, str) else content.text)
        render_row_text(content, columns=80, theme=APPLICATION_THEME)
    assert any(action.id == "delete" for action in view.actions) is downloaded


@pytest.mark.parametrize("local_only", [False, True])
def test_episode_footer_labels_follow_prepared_download_state(local_only):
    from media_cli.ui.view_presentation import ViewPresentation
    from media_cli.ui.view_session import ViewSession

    view = build((EpisodeMenuOption("one", "Episode 1", downloaded=True),
                  EpisodeMenuOption("two", "Episode 2")), local_only=local_only)
    presentation = ViewPresentation(ViewSession(view), object())
    first = presentation._footer(item_id="episode:sub:one").actions
    second = presentation._footer(item_id="episode:sub:two").actions
    assert "Enter=Play" in first
    assert "Delete/Ctrl+D=Delete" in first
    assert f"Enter={'Download' if local_only else 'Play'}" in second
    assert not any("=Delete" in text for text in second)


def test_common_details_and_feedback_are_shared_without_mutation():
    options = [EpisodeMenuOption("one", "Movie", enabled=False)]
    before = deepcopy(options)
    view = build(options, feedback="Saved", show_cover=False, context=("Example provider",))
    assert options == before
    options.clear()
    assert len(view.items) == 1 and not view.items[0].selectable
    assert view.items[0].details is Inherit
    assert resolve_details(view.details).title == "Example"
    assert view.footer.statuses[0].message == "Saved"
    assert view.image is None and view.context == ("Example provider",)
    assert view.prompt == "Select episode"
    assert not view.selection_policy.auto_single


def test_shortcuts_keep_flip_separate_from_title_refresh():
    view = build((EpisodeMenuOption("one", "Movie", downloaded=True),))
    bindings = {key: action.id for action in view.actions for key in action.keys}
    assert bindings["CTRL_R"] == "flip"
    assert bindings["DELETE"] == bindings["CTRL_D"] == "delete"
    assert bindings["LEFT"] == bindings["ESC"] == "back"
    assert bindings["RIGHT"] == bindings["ENTER"] == "select"
    assert bindings["TAB"] == "translation"
    assert dict(view.help)["Ctrl+R"] == "Flip order"


def test_empty_and_duplicate_episode_data_are_not_ambiguous():
    assert build(()).state == "empty"
    with pytest.raises(ValueError, match="Duplicate item IDs"):
        build((EpisodeMenuOption("one", "First"), EpisodeMenuOption("one", "Another")))


@pytest.mark.parametrize("kwargs", [
    {"episode_id": ""}, {"label": ""}, {"watched": 1}, {"downloaded": "yes"},
    {"enabled": None}, {"label": "\033[31mEpisode"},
])
def test_invalid_episode_data_fails_early(kwargs):
    with pytest.raises((TypeError, ValueError)):
        EpisodeMenuOption(**({"episode_id": "one", "label": "Episode 1"} | kwargs))
