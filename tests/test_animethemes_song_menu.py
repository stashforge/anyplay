"""Shared song menu routes choices without selecting a default media variant."""

import pytest
from test_animethemes_songs import anime_fixture

from media_cli.app.animethemes_song_menu import SongMenuController
from media_cli.services.animethemes import anime_song_rows
from media_cli.ui.view_model import resolve_details
from media_cli.ui.view_session import UiEvent


def controller():
    requests = []
    menu = SongMenuController(anime_song_rows(anime_fixture()), lambda song, variant: requests.append(
        (song["raw"]["id"], variant["stream"]["link"] if variant is not None else None)),
        collection_id="anime:10")
    return menu, requests


def select(menu, identity):
    session = menu.session
    session.dispatch(UiEvent("select", session.current.id, session.generation, identity))


def back(menu):
    session = menu.session
    session.dispatch(UiEvent("back", session.current.id, session.generation))


def test_details_actions_follow_collection_boundaries_without_episode_controls():
    menu, requests = controller()
    items = {item.reference: item for item in menu.session.current.items}
    assert set(items) == {"play", "songs", "version", "quality", "playback", "next"}
    assert items["next"].selectable
    assert items["play"].hint == "play Opening OP 1 from start"
    assert items["next"].hint == "play Ending ED 1"
    assert not menu.session.current.tabs
    select(menu, "action:next")
    assert requests == [(12, None)]
    items = {item.reference: item for item in menu.session.current.items}
    assert items["previous"].selectable
    assert items["previous"].hint == "play Opening OP 1"
    assert "next" not in items
    assert resolve_details(menu.session.current.details).title == "Ending"


def test_songs_are_unique_and_selection_returns_to_details_before_play():
    menu, requests = controller()
    select(menu, "action:songs")
    assert [item.label for item in menu.session.current.items] == ["Opening OP 1", "Ending ED 1"]
    select(menu, "song:1")
    assert menu.session.current.prompt == "Select action"
    assert menu.session.depth == 1
    assert requests == [(12, None)]
    select(menu, "action:songs")
    assert menu.session.position.selected_id == "song:1"


def test_finished_song_offers_play_next_and_replay_without_duplicate_next():
    menu, requests = controller()
    menu.record_playback("EOF")
    items = {item.id: item for item in menu.session.current.items}
    assert items["action:play"].label == "Play Next"
    assert items["action:play"].hint == "play Ending ED 1"
    assert items["action:replay"].hint == "play Opening OP 1 again"
    assert "action:next" not in items
    select(menu, "action:replay")
    assert requests == [(11, None)]
    select(menu, "action:play")
    assert requests[-1] == (12, None)
    menu.record_playback("EOF")
    items = {item.id: item for item in menu.session.current.items}
    assert items["action:play"].label == "Replay"
    assert items["action:play"].hint == "play Ending ED 1 again"
    assert "action:replay" not in items
    assert items["action:previous"].hint == "play Opening OP 1"


@pytest.mark.parametrize("outcome", ["QUIT", "NEXT", "PREV", "ERROR", "CANCELLED", None])
def test_non_eof_outcomes_do_not_mark_song_finished(outcome):
    menu, _ = controller()
    menu.record_playback(outcome)
    assert menu.session.current.items[0].label == "Play"
    fresh, _ = controller()
    assert fresh.session.current.items[0].label == "Play"


def test_stopping_a_replay_clears_completion_without_affecting_other_songs():
    menu, _ = controller()
    menu.record_playback("EOF")
    select(menu, "action:play")
    menu.record_playback("EOF")
    select(menu, "action:previous")
    assert menu.session.current.items[0].label == "Play Next"
    menu.record_playback("QUIT")
    assert menu.session.current.items[0].label == "Play"
    select(menu, "action:next")
    assert menu.session.current.items[0].label == "Replay"


def test_continue_and_replay_use_only_the_current_menu_timestamp():
    menu, _ = controller()
    menu.record_playback("QUIT", position=42.5, duration=90)
    items = {item.id: item for item in menu.session.current.items}
    assert items["action:play"].label == "Continue"
    assert items["action:play"].hint == "resume Opening OP 1 from 0:42"
    assert items["action:replay"].hint == "play Opening OP 1 again"
    select(menu, "action:play")
    assert menu.start_time == 42.5
    select(menu, "action:replay")
    assert menu.start_time == 0
    select(menu, "action:next")
    assert menu.start_time == 0
    select(menu, "action:previous")
    assert menu.session.current.items[0].label == "Continue"
    menu.record_playback("EOF")
    assert menu.session.current.items[0].label == "Play Next"
    fresh, _ = controller()
    assert fresh.session.current.items[0].label == "Play"


@pytest.mark.parametrize("position,duration", [(0, 90), (-1, 90), (90, 90), (91, 90),
                                               (float("nan"), 90), (float("inf"), 90), (None, 90)])
def test_invalid_or_finished_positions_do_not_offer_continue(position, duration):
    menu, _ = controller()
    menu.record_playback("QUIT", position=position, duration=duration)
    assert menu.session.current.items[0].label == "Play"


def test_changing_video_variant_clears_resume_for_the_old_animation():
    menu, _ = controller()
    menu.record_playback("QUIT", position=42, duration=90)
    select(menu, "action:version")
    select(menu, "version:2")
    assert menu.session.current.items[0].label == "Play"


def test_variant_choice_is_session_local_and_not_an_automatic_play_request():
    menu, requests = controller()
    select(menu, "action:version")
    assert [item.label for item in menu.session.current.items] == ["Original", "Version 2"]
    select(menu, "version:2")
    assert menu.session.current.prompt == "Select action"
    assert requests == []
    select(menu, "action:play")
    assert requests == [(11, "https://example.test/opv2.webm")]
    select(menu, "action:next")
    select(menu, "action:previous")
    assert requests[-1] == (11, "https://example.test/opv2.webm")
    fresh, fresh_requests = controller()
    select(fresh, "action:play")
    assert fresh_requests == [(11, None)]


def test_back_from_songs_preserves_details_filter_and_focus():
    menu, _ = controller()
    menu.session.position.query = "song"
    select(menu, "action:songs")
    back(menu)
    assert menu.session.position.query == "song"
    assert menu.session.position.selected_id == "action:songs"
    back(menu)
    assert menu.session.result == "back"


def test_quality_lists_only_files_for_selected_version_and_hides_single_choices():
    menu, requests = controller()
    select(menu, "action:quality")
    assert menu.session.current.prompt == "Select quality"
    assert [item.label for item in menu.session.current.items] == ["720p • WEB • Spoiler", "1080p • BD • NC • Spoiler"]
    select(menu, "quality:0")
    assert requests == []
    select(menu, "action:play")
    assert requests == [(11, "https://example.test/op720.webm")]
    select(menu, "action:version")
    select(menu, "version:2")
    assert "action:quality" not in {item.id for item in menu.session.current.items}
    select(menu, "action:version")
    select(menu, "version:1")
    assert menu.selected_variant()["stream"]["resolution"] == "720"
    select(menu, "action:next")
    assert not {"action:version", "action:quality"} & {item.id for item in menu.session.current.items}


def test_single_animation_with_multiple_files_only_offers_quality():
    songs = anime_song_rows(anime_fixture())
    songs[0]["variants"] = songs[0]["variants"][:2]
    menu = SongMenuController(songs, lambda *args: None, collection_id="test", quality="720p")
    assert "action:version" not in {item.id for item in menu.session.current.items}
    select(menu, "action:quality")
    assert menu.session.position.selected_id == "quality:0"
    back(menu)
    assert menu.session.position.selected_id == "action:quality"


def test_version_fallback_does_not_replace_preferred_quality_and_same_choice_keeps_resume():
    songs = anime_song_rows(anime_fixture())
    menu = SongMenuController(songs, lambda *args: None, collection_id="test", quality="1080p")
    menu.record_playback("QUIT", position=42, duration=90)
    select(menu, "action:quality")
    assert menu.session.position.selected_id == "quality:1"
    select(menu, "quality:1")
    assert menu.session.current.items[0].label == "Continue"
    select(menu, "action:version")
    select(menu, "version:2")
    assert menu.selected_variant()["stream"]["resolution"] == "720"
    select(menu, "action:version")
    select(menu, "version:1")
    assert menu.selected_variant()["stream"]["resolution"] == "1080"


def test_playback_auto_next_is_off_by_default_adjustable_and_menu_local():
    menu, requests = controller()
    select(menu, "action:playback")
    assert menu.session.current.prompt == "Playback"
    item = menu.session.current.items[0]
    assert item.label == "Auto-Play Next: Off"
    assert item.adjustable
    menu.session.position.query = "Auto"
    menu.session.dispatch(UiEvent("action", menu.session.current.id, menu.session.generation,
                                  item.id, value="increase"))
    assert menu.auto_play_next is True
    assert menu.session.current.items[0].label == "Auto-Play Next: On"
    assert menu.session.position.query == "Auto"
    menu.session.dispatch(UiEvent("action", menu.session.current.id, menu.session.generation,
                                  item.id, value="increase"))
    assert menu.auto_play_next is False
    menu.session.dispatch(UiEvent("action", menu.session.current.id, menu.session.generation,
                                  item.id, value="decrease"))
    assert menu.auto_play_next is True
    menu.session.dispatch(UiEvent("action", menu.session.current.id, menu.session.generation,
                                  item.id, value="decrease"))
    assert menu.auto_play_next is False
    select(menu, item.id)
    assert menu.auto_play_next is True
    back(menu)
    assert menu.session.position.selected_id == "action:playback"
    assert requests == []
    fresh, _ = controller()
    assert fresh.auto_play_next is False


def test_auto_next_uses_shared_control_for_rendering_and_owner_callback(monkeypatch):
    from media_cli.app import animethemes_song_menu as module
    from media_cli.ui.setting_control import toggle_setting

    snapshots = []
    changes = []
    menu, _ = controller()

    def build(*args, **kwargs):
        if args[0] == "setting:auto-next":
            snapshots.append(args[2])
        return toggle_setting(*args, **kwargs, on_label="Enabled")

    def apply(value):
        changes.append(value)
        menu.auto_play_next = value

    monkeypatch.setattr(module, "toggle_setting", build)
    monkeypatch.setattr(menu, "_set_auto_play_next", apply)
    select(menu, "action:playback")
    select(menu, "setting:auto-next")
    assert changes == [True]
    assert snapshots == [False, False, True]
    assert menu.session.current.items[0].label == "Auto-Play Next: Enabled"


def test_declined_auto_next_change_rebuilds_from_actual_value(monkeypatch):
    menu, _ = controller()
    requested = []
    monkeypatch.setattr(menu, "_set_auto_play_next", requested.append)
    select(menu, "action:playback")
    select(menu, "setting:auto-next")
    assert requested == [True]
    assert menu.auto_play_next is False
    assert menu.session.current.items[0].label == "Auto-Play Next: Off"


def test_repeat_uses_shared_choices_with_wraparound_and_session_only_state():
    menu, requests = controller()
    select(menu, "action:playback")
    assert menu.session.current.items[1].label == "Repeat: Off"
    menu.session.position.query = "Repeat"
    for label in ("Once", "Song", "Collection", "Off"):
        select(menu, "setting:repeat")
        assert menu.session.current.items[1].label == f"Repeat: {label}"
        assert menu.session.position.query == "Repeat"
    menu.session.dispatch(UiEvent("action", menu.session.current.id, menu.session.generation,
                                  "setting:repeat", value="decrease"))
    assert menu.repeat_mode == "collection"
    back(menu)
    assert requests == []
    fresh, _ = controller()
    assert fresh.repeat_mode == "off"


@pytest.mark.parametrize("songs,index", [([], 0), (anime_song_rows(anime_fixture()), -1),
                                         (anime_song_rows(anime_fixture()), 2)])
def test_invalid_collection_selection_is_rejected(songs, index):
    with pytest.raises(ValueError):
        SongMenuController(songs, lambda *args: None, collection_id="test", selected_index=index)


def test_real_terminal_song_selection_returns_to_details(tmp_path):
    import json

    from test_ui_view_terminal import Terminal

    payload = json.dumps(anime_song_rows(anime_fixture()))
    source = f'''
from media_cli.app.animethemes_song_menu import SongMenuController
requests = []
def request_play(song, variant):
    requests.append(song['raw']['id'])
    terminal_marker('PLAY_REQUEST:' + str(song['raw']['id']))
menu = SongMenuController(json.loads({payload!r}), request_play, collection_id='anime:10')
result = run_view_session(flags, ui, menu.session, poster_manager=manager)
finish(dict(kind=result, requests=requests, selected=menu.selected_index,
            prompt=menu.session.current.prompt))
'''
    with Terminal(tmp_path, source) as terminal:
        terminal.send_after(b"Select action", b"Songs\r")
        terminal.send_after(b"Select song", b"Ending\r")
        terminal.send_after(b"PLAY_REQUEST:12", b"\x1b")
        assert terminal.result() == {"kind": "back", "requests": [12], "selected": 1,
                                     "prompt": "Select action"}


def test_real_terminal_actions_put_play_closest_to_divider(tmp_path):
    import json
    import re

    from test_ui_view_terminal import Terminal

    from media_cli.core.terminal import strip_ansi

    payload = json.dumps(anime_song_rows(anime_fixture()))
    source = f'''
from media_cli.app.animethemes_song_menu import SongMenuController
menu = SongMenuController(json.loads({payload!r}), lambda *args: None, collection_id='anime:10')
menu.session.position.selected_id = 'action:play'
result = run_view_session(flags, ui, menu.session, poster_manager=manager)
finish(result)
'''
    with Terminal(tmp_path, source) as terminal:
        frame = strip_ansi(terminal.until(b"Select action").decode("utf-8", errors="replace"))
        assert "Previous" not in frame
        labels = ["Playback", "Quality", "Version", "Songs", "Next", "Play"]
        offsets = [re.search(r"\b" + re.escape(label) + r"\b", frame).start() for label in labels]
        terminal.send_after(b"Enter=select", b"\x1b")
        assert terminal.result() == "back"
        assert offsets == sorted(offsets)


def test_real_terminal_playback_toggle_and_back(tmp_path):
    import json

    from test_ui_view_terminal import Terminal

    payload = json.dumps(anime_song_rows(anime_fixture()))
    source = f'''
from media_cli.app.animethemes_song_menu import SongMenuController
menu = SongMenuController(json.loads({payload!r}), lambda *args: None, collection_id='anime:10')
result = run_view_session(flags, ui, menu.session, poster_manager=manager)
finish(dict(result=result, auto_next=menu.auto_play_next))
'''
    with Terminal(tmp_path, source) as terminal:
        terminal.send_after(b"Select action", b"Playback\r")
        terminal.send_after(b"Auto-Play Next: Off", b"\r")
        terminal.send_after(b"Auto-Play Next: On", b"\x1b[C")
        terminal.send_after(b"Auto-Play Next: Off", b"\x1b[D")
        terminal.send_after(b"Auto-Play Next: On", b"\x1b[D")
        terminal.send_after(b"Auto-Play Next: Off", b"\x1b[C")
        terminal.send_after(b"Auto-Play Next: On", b"\x1b")
        terminal.send_after(b"Select action", b"\x1b")
        assert terminal.result() == {"result": "back", "auto_next": True}


def test_real_terminal_repeat_cycles_without_losing_filter(tmp_path):
    import json

    from test_ui_view_terminal import Terminal

    payload = json.dumps(anime_song_rows(anime_fixture()))
    source = f'''
from media_cli.app.animethemes_song_menu import SongMenuController
menu = SongMenuController(json.loads({payload!r}), lambda *args: None, collection_id='anime:10')
result = run_view_session(flags, ui, menu.session, poster_manager=manager)
finish(dict(result=result, repeat=menu.repeat_mode))
'''
    with Terminal(tmp_path, source) as terminal:
        terminal.send_after(b"Select action", b"Playback\r")
        terminal.send_after(b"Repeat: Off", b"Repeat\r")
        terminal.send_after(b"replay once, then follow Auto-Play Next", b"\x1b[C")
        terminal.send_after(b"keep replaying this song", b"\x1b[H\x1b[D")
        terminal.send_after(b"replay once, then follow Auto-Play Next", b"\x1b")
        terminal.send_after(b"Select action", b"\x1b")
        assert terminal.result() == {"result": "back", "repeat": "once"}


def test_missing_song_cover_is_explicitly_unavailable():
    from media_cli.app.animethemes_song_menu import _image

    image = _image({"raw": {}}, "song")
    assert image.state == "unavailable"
    assert image.url is None
