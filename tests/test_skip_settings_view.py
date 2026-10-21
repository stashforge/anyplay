"""Skip settings snapshots project into the existing shared picker components."""

from copy import deepcopy

import pytest

from media_cli.app.skip_settings_view import (
    SkipSettingsState,
    build_skip_settings_view,
    skip_settings_options,
)
from media_cli.ui.view_model import Inherit, resolve_details


@pytest.fixture(autouse=True)
def no_preparation(monkeypatch):
    monkeypatch.setattr("media_cli.domain.titles.get_cached_title_language", lambda: "romaji")
    for name in ("ensure_episode_ids", "enrich_show_if_missing", "prepare_show_display_state", "get_custom_skips"):
        monkeypatch.setattr(f"media_cli.app_core.{name}", lambda *a, **kw: pytest.fail("Builder fetched state"))


def test_skip_options_keep_existing_order_labels_hints_and_action_ids():
    options = skip_settings_options(SkipSettingsState(True, False, "Session"))
    assert isinstance(options, tuple)
    assert tuple((item.action_id, item.label, item.hint) for item in options) == (
        ("TOGGLE_ANISKIP", "AniSkip: ON", "global/configured AniSkip lookup"),
        ("TOGGLE_AUTO_SKIP", "Auto-Skip: OFF", "auto-skip when skip intervals are available"),
        ("TOGGLE_CUSTOM_SKIPS", "Custom Skips: Session", "custom skips override AniSkip when present"),
        ("EDIT_CUSTOM_SKIPS", "Edit Custom Skips", "open editor for this title/provider skip form"),
        ("CLEAR_SESSION_SKIPS", "Clear Session Skips", "remove custom skips for this title until exit"),
        ("CLEAR_SAVED_SKIPS", "Clear Saved Title Skips", "remove persisted custom skips for this title"),
    )
    assert options[3].opens_menu
    assert all(item.enabled for item in options)


@pytest.mark.parametrize("status", ["OFF", "Session", "Title"])
def test_values_change_labels_without_changing_identity(status):
    before = skip_settings_options(SkipSettingsState(True, False, "OFF"))
    after = skip_settings_options(SkipSettingsState(False, True, status))
    assert after[0].label == "AniSkip: OFF"
    assert after[1].label == "Auto-Skip: ON"
    assert after[2].label == f"Custom Skips: {status}"
    assert tuple(item.action_id for item in before) == tuple(item.action_id for item in after)


@pytest.mark.parametrize("values", [(1, True, "OFF"), (True, "yes", "OFF"), (True, True, "Unknown")])
def test_state_rejects_ambiguous_or_unknown_values(values):
    with pytest.raises((TypeError, ValueError)):
        SkipSettingsState(*values)


@pytest.mark.parametrize("cover", [True, False])
def test_view_reuses_common_panel_footer_and_adjustment_contract(cover):
    show = dict(_id="one", name="Example", englishName="Another name", type="TV",
                thumbnail="https://images.example/cover.png", episodeCount=12, _local_progress=4)
    before = deepcopy(show)
    view = build_skip_settings_view(
        show, SkipSettingsState(True, True, "Title"), view_id="details:one:skips",
        image_key="example:one", ttype="sub", context=("Example provider",),
        feedback="Saved", show_cover=cover,
    )
    assert show == before
    assert view.id == "details:one:skips" and view.prompt == "Skip Settings"
    assert resolve_details(view.details).title == "Example"
    assert view.context == ("Example provider",)
    assert view.footer.statuses[0].message == "Saved"
    assert all(item.details is Inherit for item in view.items)
    assert tuple(item.adjustable for item in view.items) == (True, True, True, False, False, False)
    assert view.items[3].kind == "submenu"
    assert {action.id for action in view.actions} == {
        "select", "back", "help", "information", "increase", "decrease",
    }
    assert "filter start" in dict(view.help)["Left Arrow"]
    assert "filter end" in dict(view.help)["Right Arrow"]
    if cover:
        assert view.image.key == "example:one"
    else:
        assert view.image is None
