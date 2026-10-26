"""Prepared details-menu data does not fetch, mutate, or render terminal output."""

from copy import deepcopy

import pytest

from media_cli.app.details_menu_view import DetailsMenuOption, build_details_menu_view
from media_cli.core.terminal import strip_ansi
from media_cli.domain.metadata import format_info_metadata_line
from media_cli.ui.styled_text import StyledText
from media_cli.ui.view_model import Inherit, resolve_details


@pytest.fixture(autouse=True)
def no_preparation(monkeypatch):
    monkeypatch.setattr("media_cli.core.network.is_confirmed_offline", lambda: False)
    monkeypatch.setattr("media_cli.domain.titles.get_cached_title_language", lambda: "romaji")
    for name in ("ensure_episode_ids", "enrich_show_if_missing", "prepare_show_display_state"):
        monkeypatch.setattr(f"media_cli.app_core.{name}", lambda *a, **kw: pytest.fail("Builder prepared data"))


def show(**changes):
    return dict(
        _id="one",
        name="Example",
        englishName="Another name",
        status="FINISHED",
        type="TV",
        episodeCount=12,
        _local_progress=3,
        thumbnail="//images.test/cover.png",
        score=80,
        **changes,
    )


def build(source=None, options=None, **kwargs):
    return build_details_menu_view(
        show() if source is None else source,
        (
            DetailsMenuOption("PLAY_CURRENT", "Play", "play EP 1"),
            DetailsMenuOption("PROGRESS", "Progress", "local EP 3", opens_menu=True),
        )
        if options is None
        else options,
        view_id="details:anikoto:one",
        image_key="anikoto:one",
        ttype="sub",
        context=("Anikoto",),
        **kwargs,
    )


def test_catalog_loading_footer_preserves_changing_spinner_frames():
    from media_cli.ui.footer import render_footer

    first = build(loading_message='Loading SUB episodes…', loading_indicator='⣾')
    second = build(loading_message='Loading SUB episodes…', loading_indicator='⣽')
    assert strip_ansi(render_footer(first.footer, 80)).startswith('⣾ Loading SUB episodes…')
    assert strip_ansi(render_footer(second.footer, 80)).startswith('⣽ Loading SUB episodes…')
    assert first != second


def test_rows_inherit_one_common_show_panel_and_image():
    source = show()
    result = build(source)
    assert result.prompt == "Select action"
    assert result.image.key == "anikoto:one"
    assert result.image.url == "https://images.test/cover.png"
    assert result.context == ("Anikoto",)
    for item in result.items:
        assert item.details is Inherit
        assert item.image is None
        assert item.info == ""
        details = resolve_details(result.details, item.details)
        assert details.title == "Example"
        assert details.secondary == "Another name"
        assert isinstance(details.metadata, StyledText)
        assert details.metadata.text == strip_ansi(format_info_metadata_line(source))
        assert any(run.role == "metadata.status" for run in details.metadata.runs)


def test_stable_action_identity_survives_reorder_and_label_change():
    first = build().items[0]
    result = build(options=(DetailsMenuOption("PROGRESS", "Progress"), DetailsMenuOption("PLAY_CURRENT", "Continue")))
    assert first.id == result.items[1].id
    assert result.items[1].reference == "PLAY_CURRENT"
    assert result.items[1].label == "Continue"
    assert first.hint == "play EP 1"
    assert build().items[1].kind == "submenu"
    assert first.kind == "item"


def test_menu_data_is_snapshotted_without_mutating_or_preparing_the_show():
    source = show()
    before = deepcopy(source)
    options = [DetailsMenuOption("PLAY_CURRENT", "Play")]
    result = build(source, options)
    assert source == before
    options.clear()
    source["name"] = "Changed"
    assert isinstance(result.items, tuple)
    assert len(result.items) == 1
    assert resolve_details(result.details).title == "Example"
    assert not result.selection_policy.auto_single


def test_missing_cover_and_secondary_have_explicit_fallbacks():
    source = show()
    source.update(thumbnail="", englishName="")
    result = build(source)
    assert result.image.state == "unavailable"
    assert resolve_details(result.details).secondary == "No alternative title"
    source["genres"] = ["Adventure", "Comedy"]
    assert resolve_details(build(source).details).secondary == "Adventure • Comedy"


def test_download_metadata_uses_existing_local_only_decisions():
    source = show()
    source.update(watched_episodes=["1", "2"], _downloaded_episodes=["1", "2", "3"])
    result = build(source, local_only=True)
    assert resolve_details(result.details).metadata.text == strip_ansi(
        format_info_metadata_line(source, local_only=True)
    )


def test_details_primary_uses_preferred_title_without_changing_search_primary(monkeypatch):
    from media_cli.app.search_results_view import build_search_results_view

    monkeypatch.setattr("media_cli.domain.titles.get_cached_title_language", lambda: "english")
    source = show()
    assert resolve_details(build(source).details).title == "Another name"
    search = build_search_results_view(
        (source,),
        provider_id="anikoto",
        provider_name="Anikoto",
        query="example",
        ttype="sub",
        prompt="Search",
    )
    assert resolve_details(search.details, search.items[0].details).title == "Example"


def test_provider_text_is_sanitized_and_metadata_contains_roles_not_colors():
    source = show()
    source.update(name="\033[31mExample", englishName="\033[32mAnother name")
    details = resolve_details(build(source).details)
    assert details.title == "Example"
    assert details.secondary == "Another name"
    assert all("\033" not in run.text for run in details.metadata.runs)


def test_declared_bindings_preserve_primary_auxiliary_and_translation_actions():
    result = build()
    bindings = {key: action.id for action in result.actions for key in action.keys}
    assert bindings["ENTER"] == bindings["RIGHT"] == "select"
    assert bindings["ESC"] == bindings["LEFT"] == "back"
    assert bindings["TAB"] == bindings["BTAB"] == bindings["CTRL_N"] == "translation"
    assert bindings["?"] == "help"
    assert bindings["CTRL_O"] == "information"
    assert dict(result.help)["Tab"] == "Toggle Sub/Dub"
    assert dict(result.help)["Ctrl+O"] == "Title information"
    assert next(action for action in result.actions if action.id == "information").footer is False


def test_duplicate_action_ids_fail_instead_of_selecting_by_label_or_row():
    with pytest.raises(ValueError, match="Duplicate item IDs"):
        build(options=(DetailsMenuOption("PLAY", "Play"), DetailsMenuOption("PLAY", "Continue")))


@pytest.mark.parametrize("args", [("", "Play"), ("PLAY", ""), ("PLAY", "\033[31mPlay")])
def test_application_menu_options_reject_empty_identity_label_and_embedded_colors(args):
    with pytest.raises((TypeError, ValueError)):
        DetailsMenuOption(*args)


def test_empty_options_are_explicit_not_a_fake_loading_row():
    result = build(options=())
    assert result.items == ()
    assert result.state == "empty"


def test_disabled_cover_is_absent_not_unavailable(monkeypatch):
    monkeypatch.setattr(
        "media_cli.ui.poster.PosterManager._get_cover_url", lambda *a: pytest.fail("Disabled image inspected")
    )
    result = build(show_cover=False)
    assert result.image is None
    assert all(item.image is None for item in result.items)
    assert resolve_details(result.details).title == "Example"


def test_cover_visibility_requires_boolean():
    with pytest.raises(TypeError, match="show_cover"):
        build(show_cover="false")
