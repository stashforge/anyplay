"""Manual song downloads use the common worker without episode tracking."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_animethemes_songs import anime_fixture

from media_cli.app import animethemes_song_download as download
from media_cli.app import animethemes_song_playback as flow
from media_cli.services.animethemes import anime_song_rows
from media_cli.ui.view_session import UiEvent


def test_song_download_queues_exact_file_in_separate_folder_and_opens_manager(monkeypatch, tmp_path):
    rows = anime_song_rows(anime_fixture())
    row = deepcopy(rows[0]["variants"][2])
    row["stream"]["headers"] = {"X-Test": "value"}
    row["stream"]["referer"] = "https://example.test/anime"
    before = deepcopy(row)
    queued, opened = [], []
    worker = SimpleNamespace(add_item=lambda item: queued.append(item) or item)
    monkeypatch.setattr(download, "get_global_worker", lambda *args, **kwargs: worker)
    monkeypatch.setattr(download, "run_download_manager", lambda actual, **kwargs: opened.append(actual) or "BACK")
    assert download.download_song(row, {"download_dir": str(tmp_path)}, SimpleNamespace(downloader="ffmpeg")) == "BACK"
    assert row == before
    assert opened == [worker]
    item = queued[0]
    assert item.url.endswith("opv2.webm")
    assert item.headers == {"X-Test": "value", "Referer": "https://example.test/anime"}
    assert item.engine == "ffmpeg"
    assert item.automatic is False
    assert item.on_complete is None
    assert item.resolve_fn is None
    assert "Episode" not in item.episode
    assert item.destination_path.startswith(str(tmp_path / "AnimeThemes" / "Anime") + "/")
    assert not list(tmp_path.iterdir())


def test_download_identity_separates_variants_and_ignores_equal_titles(monkeypatch, tmp_path):
    queued = []
    worker = SimpleNamespace(add_item=lambda item: queued.append(item) or item)
    monkeypatch.setattr(download, "get_global_worker", lambda *args, **kwargs: worker)
    monkeypatch.setattr(download, "run_download_manager", lambda *args, **kwargs: "BACK")
    rows = anime_song_rows(anime_fixture())[0]["variants"]
    for row in (*rows, rows[0]):
        download.download_song(row, {"download_dir": str(tmp_path)}, SimpleNamespace())
    assert len({item.id for item in queued}) == 3
    assert len({item.destination_path for item in queued}) == 3
    assert queued[0].id == queued[-1].id


def test_invalid_download_url_does_not_start_worker(monkeypatch):
    row = anime_song_rows(anime_fixture())[0]["variants"][0]
    row["stream"]["link"] = "file:///etc/passwd"
    monkeypatch.setattr(download, "get_global_worker", lambda *args, **kwargs: pytest.fail("Unsafe URL started worker"))
    with pytest.raises(ValueError):
        download.download_song(row, {}, SimpleNamespace())


@pytest.mark.parametrize("result", ["BACK", "QUIT"])
@pytest.mark.parametrize("alternate", [False, True])
def test_details_download_uses_current_variant_without_playing_or_advancing(monkeypatch, result, alternate):
    requested = []
    monkeypatch.setattr(download, "download_songs", lambda rows, cfg, args: requested.extend(rows) or result)
    monkeypatch.setattr(flow, "play_variant", lambda *args: pytest.fail("Download must not play"))
    menu = flow.create_song_menu(SimpleNamespace(), None, {}, SimpleNamespace(quality="1080p"),
                                 anime_song_rows(anime_fixture()), 0, collection_id="test")
    def select(identity):
        menu.session.dispatch(UiEvent("select", menu.session.current.id, menu.session.generation, identity))
    if alternate:
        select("action:version")
        select("version:2")
    select("action:download")
    assert menu.session.current.prompt == "Download"
    select("download:current")
    assert len(requested) == 1
    assert requested[0]["stream"]["link"].endswith("opv2.webm" if alternate else "op1080.webm")
    assert menu.selected_index == 0
    assert menu.session.current.prompt == "Download"
    assert menu.session.result == ("quit" if result == "QUIT" else None)


@pytest.mark.parametrize("plain,no_tui", [(True, False), (False, True)])
def test_plain_paths_do_not_gain_a_curses_download_action(plain, no_tui):
    menu = flow.create_song_menu(SimpleNamespace(plain_mode=plain), None, {}, SimpleNamespace(no_tui=no_tui),
                                 anime_song_rows(anime_fixture()), 0, collection_id="test")
    assert "action:download" not in {item.id for item in menu.session.current.items}


def test_queue_failure_stays_in_details_without_playback_or_song_change(monkeypatch):
    def fail(*args):
        raise OSError("queue unavailable")
    monkeypatch.setattr(download, "download_songs", fail)
    menu = flow.create_song_menu(SimpleNamespace(), None, {}, SimpleNamespace(),
                                 anime_song_rows(anime_fixture()), 0, collection_id="test")
    menu.session.dispatch(UiEvent("select", menu.root_id, menu.session.generation, "action:download"))
    menu.session.dispatch(UiEvent("select", menu.session.current.id, menu.session.generation, "download:current"))
    assert menu.selected_index == 0
    assert menu.session.current.message == "Could not add this download. Try again."
    assert menu.session.result is None


def test_download_all_queues_one_batch_with_each_songs_own_variant(monkeypatch):
    batches = []
    monkeypatch.setattr(download, "download_songs", lambda rows, cfg, args: batches.append(tuple(rows)) or "BACK")
    menu = flow.create_song_menu(SimpleNamespace(), None, {}, SimpleNamespace(quality="1080p"),
                                 anime_song_rows(anime_fixture()), 0, collection_id="test")
    def select(identity):
        menu.session.dispatch(UiEvent("select", menu.session.current.id, menu.session.generation, identity))
    select("action:version")
    select("version:2")
    menu.session.position.query = "Download"
    select("action:download")
    assert [item.label for item in menu.session.current.items] == ["Download Current", "Select Songs", "Download All"]
    assert menu.session.current.items[0].hint == "download Opening OP 1"
    select("download:all")
    assert len(batches) == 1
    assert [row["stream"]["link"].rsplit("/", 1)[-1] for row in batches[0]] == ["opv2.webm", "ed.webm"]
    assert menu.selected_index == 0
    menu.session.dispatch(UiEvent("back", menu.session.current.id, menu.session.generation))
    assert menu.session.current.prompt == "Select action"
    assert menu.session.position.query == "Download"
    assert menu.session.position.selected_id == "action:download"


def test_single_song_download_is_still_direct(monkeypatch):
    batches = []
    monkeypatch.setattr(download, "download_songs", lambda rows, cfg, args: batches.append(tuple(rows)) or "BACK")
    menu = flow.create_song_menu(SimpleNamespace(), None, {}, SimpleNamespace(),
                                 anime_song_rows(anime_fixture())[:1], 0, collection_id="test")
    menu.session.dispatch(UiEvent("select", menu.root_id, menu.session.generation, "action:download"))
    assert len(batches) == 1 and len(batches[0]) == 1
    assert menu.session.current.prompt == "Select action"


def test_batch_prepares_all_jobs_before_starting_worker_and_opens_manager_once(monkeypatch, tmp_path):
    queued, opened = [], []
    worker = SimpleNamespace(add_item=lambda item: queued.append(item) or item)
    monkeypatch.setattr(download, "get_global_worker", lambda *args, **kwargs: worker)
    monkeypatch.setattr(download, "run_download_manager", lambda actual, **kwargs: opened.append(len(queued)) or "BACK")
    rows = [song["variants"][0] for song in anime_song_rows(anime_fixture())]
    assert download.download_songs([*rows, rows[0]], {"download_dir": str(tmp_path)}, SimpleNamespace()) == "BACK"
    assert len(queued) == 2
    assert opened == [2]
    bad = deepcopy(rows[1])
    bad["stream"]["link"] = "file:///etc/passwd"
    queued.clear()
    with pytest.raises(ValueError):
        download.download_songs([rows[0], bad], {"download_dir": str(tmp_path)}, SimpleNamespace())
    assert queued == []


def test_batch_skips_existing_files_and_does_not_open_manager_when_all_local(monkeypatch, tmp_path):
    queued, opened = [], []
    worker = SimpleNamespace(add_item=lambda item: queued.append(item) or item)
    monkeypatch.setattr(download, "get_global_worker", lambda *args, **kwargs: worker)
    monkeypatch.setattr(download, "run_download_manager", lambda *args, **kwargs: opened.append(True) or "BACK")
    rows = [song["variants"][0] for song in anime_song_rows(anime_fixture())]
    monkeypatch.setattr(download, "find_song_file", lambda row, cfg: "saved.mkv" if row["song"] == "Opening" else None)
    assert download.download_songs(rows, {"download_dir": str(tmp_path)}, SimpleNamespace()) == "BACK"
    assert len(queued) == 1 and queued[0].episode.startswith("Ending")
    monkeypatch.setattr(download, "find_song_file", lambda *args: "saved.mkv")
    assert download.download_songs(rows, {"download_dir": str(tmp_path)}, SimpleNamespace()) == "LOCAL"
    assert len(queued) == 1 and opened == [True]


def test_select_songs_uses_shared_multi_selection_and_batches_only_marks(monkeypatch):
    batches = []
    monkeypatch.setattr(download, "download_songs", lambda rows, cfg, args: batches.append(tuple(rows)) or "BACK")
    menu = flow.create_song_menu(SimpleNamespace(), None, {}, SimpleNamespace(quality="1080p"),
                                 anime_song_rows(anime_fixture()), 0, collection_id="test")
    def select(identity):
        menu.session.dispatch(UiEvent("select", menu.session.current.id, menu.session.generation, identity))
    select("action:download")
    select("download:select")
    assert menu.session.current.selection_policy.multiple
    assert menu.session.current.prompt == "Select songs to download"
    assert not menu.session.dispatch(UiEvent("select", menu.session.current.id, menu.session.generation,
                                             value=("song:unknown",)))
    assert batches == []
    menu.session.dispatch(UiEvent("select", menu.session.current.id, menu.session.generation, value=("song:1",)))
    assert [[row["song"] for row in batch] for batch in batches] == [["Ending"]]
    assert menu.session.current.prompt == "Download"
    assert menu.selected_index == 0
    select("download:select")
    assert menu.session.position.marked_ids == {"song:1"}
    menu.session.dispatch(UiEvent("back", menu.session.current.id, menu.session.generation))
    assert len(batches) == 1


def test_real_terminal_download_submenu_batches_without_playing_and_restores_details(tmp_path):
    import json

    from test_ui_view_terminal import Terminal

    payload = json.dumps(anime_song_rows(anime_fixture()))
    source = f'''
from media_cli.app.animethemes_song_menu import SongMenuController
batches = []
def queue(indices):
    batches.append(list(indices))
    print('DOWNLOAD_BATCH', flush=True)
menu = SongMenuController(json.loads({payload!r}), lambda *args: None, collection_id='test', download=queue)
result = run_view_session(flags, ui, menu.session, poster_manager=manager)
finish(dict(result=result, batches=batches, selected=menu.selected_index))
'''
    with Terminal(tmp_path, source) as terminal:
        terminal.send_after(b"Select action", b"Download\r")
        terminal.send_after(b"Download Current", b"Download All\r")
        terminal.send_after(b"DOWNLOAD_BATCH", b"\x1b")
        terminal.send_after(b"Select action", b"\x1b")
        assert terminal.result() == {"result": "back", "batches": [[0, 1]], "selected": 0}


def test_real_terminal_select_songs_filters_marks_and_confirms_one_batch(tmp_path):
    import json

    from test_ui_view_terminal import Terminal

    payload = json.dumps(anime_song_rows(anime_fixture()))
    source = f'''
from media_cli.app.animethemes_song_menu import SongMenuController
batches = []
def queue(indices):
    batches.append(list(indices))
    print('SELECTED_BATCH', flush=True)
menu = SongMenuController(json.loads({payload!r}), lambda *args: None, collection_id='test', download=queue)
result = run_view_session(flags, ui, menu.session, poster_manager=manager)
finish(dict(result=result, batches=batches, selected=menu.selected_index))
'''
    with Terminal(tmp_path, source) as terminal:
        terminal.send_after(b"Select action", b"Download\r")
        terminal.send_after(b"Download Current", b"Select Songs\r")
        terminal.send_after(b"Select songs to download", b"Ending \r")
        terminal.send_after(b"SELECTED_BATCH", b"\x1b")
        terminal.send_after(b"Select action", b"\x1b")
        assert terminal.result() == {"result": "back", "batches": [[1]], "selected": 0}
