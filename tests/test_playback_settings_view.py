from copy import deepcopy
from types import SimpleNamespace

import pytest

from media_cli.app.playback_settings_view import (
    build_playback_settings_view,
    playback_settings_options,
    playback_toggle_controls,
)
from media_cli.ui.view_model import Inherit, resolve_details


@pytest.fixture(autouse=True)
def no_preparation(monkeypatch):
    monkeypatch.setattr("media_cli.domain.titles.get_cached_title_language", lambda: "romaji")
    for name in ("ensure_episode_ids", "enrich_show_if_missing", "prepare_show_display_state"):
        monkeypatch.setattr(
            f"media_cli.app_core.{name}",
            lambda *args, **kwargs: pytest.fail("Playback settings view prepared show data"),
        )


def _show():
    return {
        "_id": "series-1",
        "name": "Example Show",
        "englishName": "Example",
        "status": "FINISHED",
        "type": "TV",
        "episodeCount": 12,
        "_local_progress": 4,
        "thumbnail": "//images.example/show.png",
    }


def test_playback_choices_match_settings_and_keep_actions_stable():
    cfg = {"auto_download_next": False, "auto_delete_buffer": 2}
    ms = SimpleNamespace(auto_delete_watched=True)

    options = playback_settings_options(cfg, ms)

    assert isinstance(options, tuple)
    assert [(item.action_id, item.label, item.hint, item.opens_menu) for item in options] == [
        (
            "TOGGLE_AUTO_DOWNLOAD",
            "Auto-Download Next: Off",
            "session only; download next when playback is safe",
            False,
        ),
        (
            "TOGGLE_AUTO_DELETE",
            "Auto-Delete Watched: ON",
            "session only; delete older watched downloads",
            False,
        ),
        (
            "CYCLE_AUTO_DELETE_BUFFER",
            "Auto-Delete Buffer: 2 eps",
            "session only; keep recent watched downloads",
            False,
        ),
        ("SKIP_SETTINGS", "Skip Settings", "AniSkip and custom opening/ending skips", True),
    ]

    changed = playback_settings_options(
        {"auto_download_next": True, "auto_delete_buffer": 1},
        SimpleNamespace(auto_download_next="adaptive", auto_delete_watched=False),
    )
    assert tuple(option.action_id for option in changed) == tuple(option.action_id for option in options)
    assert tuple(option.hint for option in changed) == tuple(option.hint for option in options)
    assert tuple(option.opens_menu for option in changed) == tuple(option.opens_menu for option in options)


def test_playback_choices_resolve_session_modes_and_buffer_pluralization():
    configured = playback_settings_options(
        {"auto_download_next": True, "auto_delete_buffer": 0},
        SimpleNamespace(auto_download_next="off", auto_delete_watched=False),
    )
    assert configured[0].label == "Auto-Download Next: Off"
    assert configured[2].label == "Auto-Delete Buffer: 0 eps"

    singular = playback_settings_options(
        {"auto_download_next": False, "auto_delete_buffer": 3},
        SimpleNamespace(auto_download_next="adaptive", auto_delete_buffer=1),
    )
    assert singular[0].label == "Auto-Download Next: Adaptive"
    assert singular[2].label == "Auto-Delete Buffer: 1 ep"


def test_playback_view_uses_common_show_policy_without_translation_bindings(monkeypatch):
    show = _show()
    original = deepcopy(show)
    cfg = {"auto_download_next": True, "details_cover": True}
    ms = SimpleNamespace(auto_delete_watched=False, auto_delete_buffer=1)
    cfg_before = deepcopy(cfg)
    ms_before = vars(ms).copy()
    options = playback_settings_options(cfg, ms)
    view = build_playback_settings_view(
        show,
        cfg,
        ms,
        view_id="details:anilist:series-1:playback",
        image_key="anilist:series-1",
        ttype="sub",
        context=("AniList", "[Offline]"),
        feedback="Saved",
    )

    assert show == original
    assert cfg == cfg_before
    assert vars(ms) == ms_before
    assert view.prompt == "Playback"
    assert view.id == "details:anilist:series-1:playback"
    assert view.image.key == "anilist:series-1"
    assert view.image.url == "https://images.example/show.png"
    assert view.context == ("AniList", "[Offline]")
    assert view.footer.statuses[0].message == "Saved"
    assert tuple(item.reference for item in view.items) == tuple(option.action_id for option in options)
    assert all(item.details is Inherit and item.image is None for item in view.items)
    assert resolve_details(view.details).title == "Example Show"
    assert {action.id for action in view.actions} == {
        "select", "help", "back", "information", "increase", "decrease",
    }
    assert tuple(item.adjustable for item in view.items) == (True, True, True, False)
    assert next(action for action in view.actions if action.id == "information").footer is False
    assert dict(view.help)["Left Arrow"] == (
        "Previous value at filter start; otherwise move cursor or go back"
    )
    assert dict(view.help)["Right Arrow"] == "Next value at filter end; otherwise move cursor or open"


def test_playback_view_can_hide_cover_and_snapshots_options():
    show = _show()
    cfg = {"details_cover": False}
    ms = SimpleNamespace()
    options = playback_settings_options(cfg, ms)
    view = build_playback_settings_view(
        show,
        cfg,
        ms,
        view_id="details:provider:series-1:playback",
        image_key="provider:series-1",
        ttype="dub",
        show_cover=False,
    )
    cfg["auto_download_next"] = False
    cfg["auto_delete_buffer"] = 5
    ms.auto_download_next = "off"
    ms.auto_delete_watched = True
    ms.auto_delete_buffer = 3

    assert tuple(item.label for item in view.items) == tuple(option.label for option in options)
    assert view.image is None
    assert len(view.items) == 4
    assert view.items[-1].kind == "submenu"


def test_controlled_playback_rows_and_callbacks_share_one_definition():
    changes = []
    cfg = {"auto_download_next": False}
    ms = SimpleNamespace(auto_delete_watched=True)
    controls = playback_toggle_controls(cfg, ms, on_download=lambda value: changes.append(("download", value)),
                                         on_delete=lambda value: changes.append(("delete", value)))
    view = build_playback_settings_view(_show(), cfg, ms, view_id="playback", image_key="one", ttype="sub",
                                        controls=controls)
    assert [item.label for item in view.items[:2]] == [control.item().label for control in controls]
    assert [item.reference for item in view.items[:2]] == ["TOGGLE_AUTO_DOWNLOAD", "TOGGLE_AUTO_DELETE"]
    assert controls[0].change(-1)
    assert controls[1].change(1)
    assert changes == [("download", True), ("delete", False)]
    assert cfg == {"auto_download_next": False}
    assert ms.auto_delete_watched is True
