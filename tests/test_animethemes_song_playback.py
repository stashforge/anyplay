"""Themes autoplay selects original animation before applying quality preference."""

import pytest
from test_animethemes_songs import anime_fixture

from media_cli.app import animethemes_song_playback as flow
from media_cli.services.animethemes import anime_song_rows
from media_cli.ui.view_session import UiEvent


@pytest.mark.parametrize("quality,link", [
    ("1080p", "op1080.webm"), ("720p", "op720.webm"), ("best", "op1080.webm"),
    ("480p", "op720.webm"),
])
def test_default_uses_original_version_and_existing_quality_rules(quality, link):
    song = anime_song_rows(anime_fixture())[0]
    song["variants"] = tuple(reversed(song["variants"]))
    assert flow.default_variant(song, quality)["stream"]["link"] == "https://example.test/" + link


def test_default_uses_earliest_available_version_not_best_quality_across_versions():
    song = anime_song_rows(anime_fixture())[0]
    song["variants"] = tuple(row for row in song["variants"] if row["theme"] == "OP1v2")
    assert flow.default_variant(song, "1080p")["stream"]["link"].endswith("opv2.webm")


def test_native_next_previous_switch_songs_and_stay_in_details(monkeypatch):
    from types import SimpleNamespace

    requests = []
    outcomes = iter(["NEXT", "PREV", "QUIT"])
    def play(row, flags, cfg, args, show, ui, collection):
        requests.append((row["song"], collection["episode_index"], collection["total_eps"]))
        target = 2 if collection["episode_index"] == 0 else 1
        assert collection["fetch_callback"](target)[0]["link"].endswith(
            "ed.webm" if target == 2 else "op1080.webm")
        return (next(outcomes), 0, 0, 0, 0)
    monkeypatch.setattr(flow, "play_variant", play)
    monkeypatch.setattr(flow, "run_view_session", lambda flags, ui, session, **kwargs: session.current.prompt)
    result = flow.run_song_collection(SimpleNamespace(), SimpleNamespace(), {"quality": "1080p"},
                                      SimpleNamespace(quality=None), anime_song_rows(anime_fixture()), 0)
    assert result == "Select action"
    assert requests == [("Opening", 0, 2), ("Ending", 1, 2), ("Opening", 0, 2)]


def test_only_backend_eof_offers_play_next_without_automatic_playback(monkeypatch):
    from types import SimpleNamespace

    requests = []
    def play(row, *args):
        requests.append(row["song"])
        return ("EOF", 100, 90, 90, 90)
    monkeypatch.setattr(flow, "play_variant", play)
    def run(flags, ui, session, **kwargs):
        assert session.current.items[0].label == "Play Next"
        assert session.current.items[0].hint == "play Ending ED 1"
        assert session.position.selected_id == "action:play"
        return "back"
    monkeypatch.setattr(flow, "run_view_session", run)
    assert flow.run_song_collection(SimpleNamespace(), None, {}, SimpleNamespace(quality=None),
                                    anime_song_rows(anime_fixture()), 0) == "back"
    assert requests == ["Opening"]


def test_continue_passes_reported_position_to_desktop_without_history(monkeypatch):
    from types import SimpleNamespace

    starts = []
    def play(row, flags, cfg, args, show, ui, collection):
        starts.append(collection.get("start_time_override", 0))
        return ("QUIT", 50, 42.5, 90, 10)
    monkeypatch.setattr(flow, "play_variant", play)
    menu = flow.create_song_menu(SimpleNamespace(), None, {}, SimpleNamespace(quality=None),
                                 anime_song_rows(anime_fixture()), 0, collection_id="test")
    menu.play(menu.songs[0], None)
    assert menu.session.current.items[0].label == "Continue"
    menu.session.dispatch(UiEvent("select", menu.root_id, menu.session.generation, "action:play"))
    assert starts == [0, 42.5]
    menu.session.dispatch(UiEvent("select", menu.root_id, menu.session.generation, "action:replay"))
    assert starts == [0, 42.5, 0]


@pytest.mark.parametrize("outcome,expected", [("EOF", ["Opening", "Ending"]),
                                            ("QUIT", ["Opening"]), ("ERROR", ["Opening"]),
                                            ("CANCELLED", ["Opening"])])
def test_enabled_auto_next_only_advances_on_eof_and_stops_at_collection_end(monkeypatch, outcome, expected):
    from types import SimpleNamespace

    requests = []
    def play(row, *args):
        requests.append(row["song"])
        return (outcome, 100, 90, 90, 90)
    monkeypatch.setattr(flow, "play_variant", play)
    menu = flow.create_song_menu(SimpleNamespace(), None, {}, SimpleNamespace(quality=None),
                                 anime_song_rows(anime_fixture()), 0, collection_id="test")
    menu.session.dispatch(UiEvent("select", menu.root_id, menu.session.generation, "action:playback"))
    menu.session.dispatch(UiEvent("select", menu.session.current.id, menu.session.generation, "setting:auto-next"))
    menu.session.dispatch(UiEvent("back", menu.session.current.id, menu.session.generation))
    menu.session.dispatch(UiEvent("select", menu.root_id, menu.session.generation, "action:play"))
    assert requests == expected
    if outcome == "EOF":
        assert menu.session.current.items[0].label == "Replay"


@pytest.mark.parametrize("mode,auto_next,outcomes,expected", [
    ("once", False, ["EOF", "EOF"], ["Opening", "Opening"]),
    ("once", True, ["EOF", "EOF", "EOF", "EOF"], ["Opening", "Opening", "Ending", "Ending"]),
    ("song", False, ["EOF", "EOF", "QUIT"], ["Opening"] * 3),
    ("collection", False, ["EOF", "EOF", "QUIT"], ["Opening", "Ending", "Opening"]),
    ("once", False, ["EOF", "NEXT", "EOF", "EOF"], ["Opening", "Opening", "Ending", "Ending"]),
    ("song", True, ["ERROR"], ["Opening"]),
    ("collection", True, ["CANCELLED"], ["Opening"]),
    ("once", True, ["QUIT"], ["Opening"]),
])
def test_repeat_modes_honor_eof_manual_navigation_and_stop_outcomes(monkeypatch, mode, auto_next, outcomes, expected):
    from types import SimpleNamespace

    requests = []
    results = iter(outcomes)
    def play(row, flags, cfg, args, show, ui, collection):
        requests.append(row["song"])
        assert collection["start_time_override"] == 0
        return (next(results), 100, 90, 90, 90)
    monkeypatch.setattr(flow, "play_variant", play)
    menu = flow.create_song_menu(SimpleNamespace(), None, {}, SimpleNamespace(quality=None),
                                 anime_song_rows(anime_fixture()), 0, collection_id="test")
    menu.auto_play_next = auto_next
    menu.session.dispatch(UiEvent("select", menu.root_id, menu.session.generation, "action:playback"))
    for _ in range(4):
        if menu.repeat_mode == mode:
            break
        menu.session.dispatch(UiEvent("select", menu.session.current.id, menu.session.generation, "setting:repeat"))
    assert menu.repeat_mode == mode
    menu.session.dispatch(UiEvent("back", menu.session.current.id, menu.session.generation))
    menu.session.dispatch(UiEvent("select", menu.root_id, menu.session.generation, "action:play"))
    assert requests == expected


def test_once_allowance_survives_stopping_and_continuing_the_extra_replay(monkeypatch):
    from types import SimpleNamespace

    starts = []
    results = iter([("EOF", 100, 90, 90, 90), ("QUIT", 50, 42, 90, 10), ("EOF", 100, 90, 90, 48)])
    def play(row, flags, cfg, args, show, ui, collection):
        starts.append(collection["start_time_override"])
        return next(results)
    monkeypatch.setattr(flow, "play_variant", play)
    menu = flow.create_song_menu(SimpleNamespace(), None, {}, SimpleNamespace(quality=None),
                                 anime_song_rows(anime_fixture()), 0, collection_id="test")
    menu._set_repeat_mode("once")
    menu.play(menu.songs[0], None)
    assert menu.session.current.items[0].label == "Continue"
    menu.session.dispatch(UiEvent("select", menu.root_id, menu.session.generation, "action:play"))
    assert starts == [0, 0, 42]
    assert menu.session.current.items[0].label == "Play Next"


def test_explicit_replay_starts_a_fresh_once_allowance(monkeypatch):
    from types import SimpleNamespace

    played = []
    def play(row, *args):
        played.append(row["song"])
        if len(played) > 4:
            pytest.fail("Once must not loop indefinitely")
        return ("EOF", 100, 90, 90, 90)
    monkeypatch.setattr(flow, "play_variant", play)
    menu = flow.create_song_menu(SimpleNamespace(), None, {}, SimpleNamespace(quality=None),
                                 anime_song_rows(anime_fixture()), 0, collection_id="test")
    menu._set_repeat_mode("once")
    menu.play(menu.songs[0], None)
    assert played == ["Opening"] * 2
    menu.session.dispatch(UiEvent("select", menu.root_id, menu.session.generation, "action:replay"))
    assert played == ["Opening"] * 4


@pytest.mark.parametrize("selected,primary", [(0, "Play Next"), (1, "Replay")])
def test_song_end_returns_to_details_and_closes_idle_mpv(monkeypatch, selected, primary):
    from types import SimpleNamespace

    from media_cli.playback import desktop, engine

    loaded, closed = [], []
    process, client = SimpleNamespace(poll=lambda: None), object()
    ipc = SimpleNamespace(process=process, client=client,
                          props={"duration": 90, "playback-time": 90, "percent-pos": 100},
                          load=lambda url, *args, **kwargs: loaded.append(url),
                          wait_for_playback=lambda *args, **kwargs: ("EOF", 90),
                          quit=lambda: closed.append(True))
    monkeypatch.setattr(flow.app_core, "_ipc_player", ipc)
    monkeypatch.setattr(flow.app_core, "is_termux", lambda: False)
    monkeypatch.setattr(engine, "MpvIpc", lambda: pytest.fail("Running mpv must be reused, not replaced"))

    def play(row):
        return desktop.play_desktop(ipc, row["song"], row["theme"], row["stream"],
                                     start_player_fn=engine._start_player_for_playback,
                                     get_resume_time=lambda *args: pytest.fail("Songs must not read saved episode resume"),
                                     get_preferred_mirror=lambda *args: {}, update_stream_info=lambda info: None,
                                     aniskip_enabled=False, show_id=None)

    menu = flow.create_song_menu(SimpleNamespace(), None, {}, SimpleNamespace(quality=None),
                                 anime_song_rows(anime_fixture()), selected, collection_id="test", play_callback=play)
    menu.play(menu.songs[selected], None)
    assert len(loaded) == 1
    assert menu.session.current.prompt == "Select action"
    assert menu.session.current.items[0].label == primary
    assert closed == [True]


@pytest.mark.parametrize("mode,auto_next,outcomes,expected", [
    ("off", True, ["EOF", "EOF"], ["Opening", "Ending"]),
    ("once", False, ["EOF", "EOF"], ["Opening", "Opening"]),
    ("song", False, ["EOF", "QUIT"], ["Opening", "Opening"]),
    ("collection", False, ["EOF", "EOF", "QUIT"], ["Opening", "Ending", "Opening"]),
    ("off", False, ["ERROR"], ["Opening"]),
    ("off", False, ["CANCELLED"], ["Opening"]),
])
def test_player_stays_open_between_songs_but_closes_when_collection_playback_returns(
    monkeypatch, mode, auto_next, outcomes, expected,
):
    from types import SimpleNamespace

    closed, played = [], []
    results = iter(outcomes)
    monkeypatch.setattr(flow.app_core, "is_termux", lambda: False)
    monkeypatch.setattr(flow.app_core, "_ipc_player", SimpleNamespace(
        process=object(), client=object(), quit=lambda: closed.append(True)))
    def play(row):
        assert not closed
        played.append(row["song"])
        return (next(results), 100, 90, 90, 90)
    menu = flow.create_song_menu(SimpleNamespace(), None, {}, SimpleNamespace(),
                                 anime_song_rows(anime_fixture()), 0, collection_id="test", play_callback=play)
    menu._set_repeat_mode(mode)
    menu.auto_play_next = auto_next
    menu.play(menu.songs[0], None)
    assert played == expected
    assert closed == [True]


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_song_player_is_closed_even_when_playback_raises(monkeypatch, failure):
    from types import SimpleNamespace

    closed = []
    monkeypatch.setattr(flow.app_core, "is_termux", lambda: False)
    monkeypatch.setattr(flow.app_core, "_ipc_player", SimpleNamespace(
        process=object(), client=None, quit=lambda: closed.append(True)))
    def fail(row):
        raise failure("stopped")
    menu = flow.create_song_menu(SimpleNamespace(), None, {}, SimpleNamespace(),
                                 anime_song_rows(anime_fixture()), 0, collection_id="test", play_callback=fail)
    if failure is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            menu.play(menu.songs[0], None)
    else:
        menu.play(menu.songs[0], None)
    assert closed == [True]


def test_android_song_return_does_not_touch_desktop_mpv(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(flow.app_core, "is_termux", lambda: True)
    monkeypatch.setattr(flow.app_core, "_ipc_player", SimpleNamespace(
        process=object(), client=None, quit=lambda: pytest.fail("Android must not close desktop mpv")))
    menu = flow.create_song_menu(SimpleNamespace(), None, {}, SimpleNamespace(),
                                 anime_song_rows(anime_fixture()), 0, collection_id="test",
                                 play_callback=lambda row: True)
    menu.play(menu.songs[0], None)
    assert menu.session.current.prompt == "Select action"


def test_collection_preparation_keeps_selected_theme_and_reuses_complete_data():
    anime = anime_fixture()
    rows = [{"kind": "theme", "raw": {**theme, "anime": anime}}
            for theme in anime["animethemes"]]
    songs, selected = flow.prepare_collection(rows, 1)
    assert [song["raw"]["id"] for song in songs] == [11, 12]
    assert selected == 1


def test_summary_theme_is_loaded_with_identity_checked(monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    anime = anime_fixture()
    theme = {**anime["animethemes"][0], "anime": anime}
    requests = []
    def request(url, **kwargs):
        requests.append(url)
        return {"animetheme": theme}
    monkeypatch.setattr(flow.service, "request_json", request)
    songs, selected = flow.prepare_collection([{"raw": {"id": 11}}], 0)
    assert songs[0]["raw"]["id"] == 11
    assert len(songs[0]["variants"]) == 3
    assert selected == 0
    assert urlsplit(requests[0]).path == "/animetheme/11"
    assert parse_qs(urlsplit(requests[0]).query) == {
        "include": ["anime.images,song.artists,animethemeentries.videos"]}


def test_wrong_theme_identity_is_not_used_for_playback(monkeypatch):
    monkeypatch.setattr(flow.service, "request_json", lambda *args, **kwargs: {"animetheme": {"id": 99}})
    with pytest.raises(ValueError, match="different theme identity"):
        flow.prepare_collection([{"raw": {"id": 11}}], 0)


def test_autoplay_precedes_menu_and_back_returns_without_replaying(monkeypatch):
    from types import SimpleNamespace

    songs = anime_song_rows(anime_fixture())
    order = []
    monkeypatch.setattr(flow, "play_variant", lambda row, *args: order.append(row["stream"]["link"]))
    def run(flags, ui, session, **kwargs):
        order.append(session.current.prompt)
        assert session.position.selected_id == "action:play"
        session.dispatch(UiEvent("back", session.current.id, session.generation))
        return session.result
    monkeypatch.setattr(flow, "run_view_session", run)
    result = flow.run_song_collection(SimpleNamespace(), SimpleNamespace(), {"quality": "1080p"},
                                      SimpleNamespace(quality=None), songs, 1)
    assert result == "back"
    assert order == ["https://example.test/ed.webm", "Select action"]


def test_filtered_theme_collection_and_source_position_are_preserved(monkeypatch):
    from types import SimpleNamespace

    from media_cli.ui.picker_state import PickerState

    position = PickerState(query="Opening", selected_index=0, query_cursor=3, scroll=2)
    seen = []
    monkeypatch.setattr(flow.app_core, "with_loading", lambda msg, fn, *args: fn(*args))
    def run(flags, ui, cfg, args, songs, selected):
        seen.append(([song["raw"]["id"] for song in songs], selected))
        return "back"
    monkeypatch.setattr(flow, "run_song_collection", run)
    result = flow.open_theme_results(None, SimpleNamespace(), None, {}, None,
                                     anime_song_rows(anime_fixture()), 0, "anime", position)
    assert result == "back"
    assert seen == [([11], 0)]
    assert (position.query, position.selected_index, position.query_cursor, position.scroll) == ("Opening", 0, 3, 2)


def test_cancelled_preparation_never_launches_a_player(monkeypatch):
    from media_cli.ui.picker_state import PickerState

    def cancelled(*args):
        raise flow.PreparationCancelled
    def forbidden(*args):
        pytest.fail("Cancelled preparation must not enter playback")
    monkeypatch.setattr(flow.app_core, "with_loading", cancelled)
    monkeypatch.setattr(flow, "run_song_collection", forbidden)
    assert flow.open_theme_results(None, None, None, {}, None, anime_song_rows(anime_fixture()),
                                   0, "anime", PickerState()) is None


@pytest.mark.parametrize("failed", [False, True])
def test_player_adapter_activates_screen_and_always_cleans_up(monkeypatch, failed):
    from types import SimpleNamespace

    from media_cli.ui import player_screen

    calls = []
    monkeypatch.setattr(flow.app_core, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(flow.app_core, "is_termux", lambda: False)
    monkeypatch.setattr(player_screen, "activate", lambda show, ep, total: calls.append((show["name"], ep)))
    monkeypatch.setattr(flow.app_core, "_exit_player_screen", lambda: calls.append("cleanup"))
    def play(*args, **kwargs):
        assert args[0] == "Opening by Singer - Anime OP 1 (EP 1-6)"
        assert kwargs["show_id"] is None
        assert kwargs["aniskip_enabled"] is False
        assert kwargs["total_eps"] == 2
        assert kwargs["episode_index"] == 0
        assert kwargs["start_time_override"] == 42.5
        assert kwargs["fetch_callback"](2)[0]["link"].endswith("ed.webm")
        if failed:
            raise RuntimeError("player unavailable")
        return ("QUIT", 20, 10, 50, 10)
    monkeypatch.setattr(flow.app_core, "play_desktop", play)
    row = anime_song_rows(anime_fixture())[0]["variants"][0]
    def invoke():
        return flow.play_variant(row, SimpleNamespace(plain_mode=False), {},
                                  SimpleNamespace(player=None), {"name": "Anime"}, collection={
                                      "total_eps": 2, "episode_index": 0,
                                      "start_time_override": 42.5,
                                      "fetch_callback": lambda ordinal: ({"link": "https://example.test/ed.webm"},)})
    if failed:
        with pytest.raises(RuntimeError, match="player unavailable"):
            invoke()
    else:
        assert invoke() == ("QUIT", 20, 10, 50, 10)
    assert calls == [("Anime", "OP1"), "cleanup"]


def test_themes_search_routes_to_direct_play_collection(monkeypatch):
    from types import SimpleNamespace

    from media_cli.app import animethemes as search_flow

    themes = [row["raw"] for row in anime_song_rows(anime_fixture())]
    class ImmediateThread:
        def __init__(self, target, **kwargs):
            self.target = target
        def start(self):
            self.target()
    monkeypatch.setattr(search_flow.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(search_flow.animethemes_service, "search", lambda query: {"animethemes": themes})
    monkeypatch.setattr(search_flow.app_core, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(search_flow.app_core, "make_info_fn", lambda *args: None)
    selected = []
    def open_results(flags, ui, ms, cfg, args, rows, index, query, position):
        selected.append((rows[index]["raw"]["id"], position.selected_index, query))
        return "quit"
    monkeypatch.setattr(flow, "open_theme_results", open_results)
    def pick(*args, **kwargs):
        kwargs["live_fn"]()
        return 1
    monkeypatch.setattr(search_flow, "tui_pick", pick)
    result = search_flow.handle_animethemes_search_state(None, SimpleNamespace(),
        SimpleNamespace(query_str="anime"), {}, SimpleNamespace(), "sub")
    assert result == "QUIT"
    assert selected == [(12, 1, "anime")]


def test_anime_parent_opens_shared_details_without_autoplay(monkeypatch):
    from types import SimpleNamespace

    seen = []
    def run(flags, ui, cfg, args, songs, selected, **kwargs):
        seen.append(([song["raw"]["id"] for song in songs], selected, kwargs["autoplay"]))
        return "back"
    monkeypatch.setattr(flow, "run_song_collection", run)
    assert flow.open_anime_collection(None, SimpleNamespace(), {}, None, anime_fixture()) == "back"
    assert seen == [([11, 12], 0, False)]


def test_other_provider_song_entry_uses_shared_rows_and_headers(monkeypatch):
    from types import SimpleNamespace

    from media_cli.app import animethemes as entry
    from media_cli.ui.view_model import resolve_details

    observed = []
    monkeypatch.setattr(entry.app_core, "with_loading", lambda msg, fn, *args: fn(*args))
    monkeypatch.setattr(flow.service, "get_anime_for_show", lambda show: anime_fixture())
    def no_play(*args, **kwargs):
        pytest.fail("Opening a parent should show details, not autoplay")
    monkeypatch.setattr(flow, "play_variant", no_play)
    def run(flags, ui, session, **kwargs):
        session.dispatch(UiEvent("select", session.current.id, session.generation, "action:songs"))
        observed.extend(item.label for item in session.current.items)
        details = resolve_details(session.current.details, session.current.items[0].details)
        assert details.title == "Opening"
        assert details.secondary == "No alternative title"
        assert details.metadata == "by Singer · Anime · OP 1"
        return "back"
    monkeypatch.setattr(flow, "run_view_session", run)
    assert entry.open_anime_songs(SimpleNamespace(), SimpleNamespace(), None, {},
                                  SimpleNamespace(quality=None), {"_provider": "anikoto", "name": "Anime"}) == "DETAILS"
    assert observed == ["Opening OP 1", "Ending ED 1"]


def test_real_terminal_autoplay_then_details_songs_and_source_return(tmp_path):
    import json

    from test_ui_view_terminal import Terminal

    payload = json.dumps(anime_song_rows(anime_fixture()))
    source = f'''
from media_cli.app import animethemes_song_playback as flow
played = []
def play(row, *args):
    played.append(row['stream']['link'])
    print('PLAYED:' + str(len(played)), flush=True)
flow.play_variant = play
result = flow.run_song_collection(flags, ui, dict(quality='1080p'), SimpleNamespace(quality=None),
                                  json.loads({payload!r}), 0)
finish(dict(kind=result, played=played))
'''
    with Terminal(tmp_path, source) as terminal:
        terminal.until(b"PLAYED:1")
        terminal.send_after(b"Select action", b"\x15Songs\r")
        terminal.send_after(b"Select song", b"Ending\r")
        terminal.send_after(b"PLAYED:2", b"\x1b")
        assert terminal.result() == {"kind": "back", "played": [
            "https://example.test/op1080.webm", "https://example.test/ed.webm"]}


def test_real_player_screen_receives_cover_renderer_during_song_playback(tmp_path):
    from test_ui_view_terminal import Terminal

    source = '''
from media_cli.app import animethemes_song_playback as flow
from media_cli.ui import player_screen
covers = []
def cover(show):
    covers.append(show['thumbnail'])
    return 'PLAYER_COVER'
flow.app_core._poster_manager = SimpleNamespace(enabled=lambda: True, get=cover)
flow.app_core.is_termux = lambda: False
def desktop(*args, **kwargs):
    player_screen._player_ui_state.update(dict(
        stream_info=dict(mirror='Unknown', quality='1080'),
        mpv_props={'playback-time': 10, 'duration': 90, 'pause': False}))
    player_screen.render()
    return ('QUIT', 0, 0, 0, 0)
flow.app_core.play_desktop = desktop
row = dict(anime='Anime', theme='OP1', song='Song', artists='Singer', episodes='1-11',
           raw=dict(entry=dict(version=1), video=dict(nc=True)),
           stream=dict(link='https://example.test/song.webm', resolution='1080', source='BD'))
flow.play_variant(row, flags, {}, SimpleNamespace(player=None),
                  dict(_id='anime', name='Anime', type='TV', airedStart=dict(year=2023),
                       thumbnail='https://example.test/cover.jpg'))
finish(dict(covers=covers))
'''
    with Terminal(tmp_path, source) as terminal:
        assert terminal.result() == {"covers": ["https://example.test/cover.jpg"]}
        assert b"PLAYER_COVER" in terminal.output
        assert b"Singer" in terminal.output
        assert b"Song" in terminal.output
        assert b"Song by Singer" in terminal.output
        assert terminal.output.index(b"Anime") < terminal.output.index(b"Song by Singer")
        assert b"Original" in terminal.output
        assert b"EP 1-11" in terminal.output
        assert b"1080p" in terminal.output
        assert b"BD" in terminal.output
        assert b"Episode OP1" not in terminal.output
        assert b"Unknown" not in terminal.output
        assert b"TV" not in terminal.output
        assert b"SUB" not in terminal.output
