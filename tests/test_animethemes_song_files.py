"""Exact song file identities are shared by downloads and local-first playback."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_animethemes_songs import anime_fixture

from media_cli.app import animethemes_song_files as files
from media_cli.app import animethemes_song_playback as flow
from media_cli.media import download_worker
from media_cli.services.animethemes import anime_song_rows
from media_cli.ui.view_session import UiEvent


def song_row():
    return anime_song_rows(anime_fixture())[0]["variants"][1]


@pytest.fixture
def local_video(tmp_path):
    cfg = {"download_dir": str(tmp_path)}
    row = song_row()
    target = files.song_file_target(row, cfg)
    target.path.parent.mkdir(parents=True)
    target.path.write_bytes(b"\x1a\x45\xdf\xa3" + bytes(1024))
    return row, cfg, target


def test_valid_file_is_used_without_changing_provider_data(local_video, monkeypatch):
    row, cfg, target = local_video
    monkeypatch.setattr(download_worker, "get_global_worker_if_started", lambda: None)
    original = deepcopy(row)
    result = files.local_song_row(row, cfg)
    assert result["stream"]["link"] == str(target.path)
    assert result["stream"]["is_local"] is True
    assert result["stream"]["source_name"] == "Local"
    assert result["raw"] == row["raw"]
    assert row == original
    other = anime_song_rows(anime_fixture())[0]["variants"][0]
    assert files.local_song_row(other, cfg) is other


@pytest.mark.parametrize("marker", [".aria2", ".part", ".ytdl"])
def test_partial_markers_prevent_using_a_final_named_file(local_video, monkeypatch, marker):
    row, cfg, target = local_video
    monkeypatch.setattr(download_worker, "get_global_worker_if_started", lambda: None)
    target.path.with_name(target.path.name + marker).touch()
    assert files.local_song_row(row, cfg) is row


@pytest.mark.parametrize("status", ["queued", "resolving", "downloading", "paused", "failed", "cancelled"])
def test_worker_incomplete_files_are_never_reused(local_video, monkeypatch, status):
    row, cfg, target = local_video
    worker = SimpleNamespace(get_items=lambda: [SimpleNamespace(id=target.item_id, status=status)])
    monkeypatch.setattr(download_worker, "get_global_worker_if_started", lambda: worker)
    assert files.local_song_row(row, cfg) is row


@pytest.mark.parametrize("payload", [b"", b"#EXTM3U\nhttps://example.test/segment.ts", b"<html>not video</html>"])
def test_invalid_files_fall_back_to_remote(local_video, monkeypatch, payload):
    row, cfg, target = local_video
    monkeypatch.setattr(download_worker, "get_global_worker_if_started", lambda: None)
    target.path.write_bytes(payload)
    assert files.local_song_row(row, cfg) is row


def test_identity_survives_title_changes_and_signed_url_changes_when_ids_exist(local_video, monkeypatch):
    row, cfg, target = local_video
    monkeypatch.setattr(download_worker, "get_global_worker_if_started", lambda: None)
    row["song"], row["anime"] = "New song title", "New anime title"
    row["stream"]["link"] += "?token=renewed"
    assert files.song_file_target(row, cfg).item_id == target.item_id
    assert files.find_song_file(row, cfg) == str(target.path)


def test_real_song_selection_and_native_fetch_prefer_local_file(local_video, monkeypatch):
    row, cfg, target = local_video
    monkeypatch.setattr(download_worker, "get_global_worker_if_started", lambda: None)
    received = []
    def play(selected, flags, config, args, show, ui, collection):
        received.append(selected["stream"]["link"])
        assert collection["fetch_callback"](1)[0]["link"] == str(target.path)
        return ("QUIT", 0, 0, 90, 0)
    monkeypatch.setattr(flow, "play_variant", play)
    menu = flow.create_song_menu(SimpleNamespace(), None, cfg, SimpleNamespace(quality="1080p"),
                                 anime_song_rows(anime_fixture()), 0, collection_id="test")
    menu.play(menu.songs[0], None)
    assert received == [str(target.path)]


def test_existing_download_does_not_start_worker_or_queue_again(local_video, monkeypatch):
    from media_cli.app import animethemes_song_download as download

    row, cfg, _ = local_video
    monkeypatch.setattr(download_worker, "get_global_worker_if_started", lambda: None)
    monkeypatch.setattr(download, "get_global_worker", lambda *a, **kw: pytest.fail("Existing song started a download"))
    monkeypatch.setattr(download, "run_download_manager", lambda *a, **kw: pytest.fail("Existing song opened manager"))
    assert download.download_song(row, cfg, SimpleNamespace()) == "LOCAL"


def test_android_local_song_uses_existing_local_file_launcher(local_video, monkeypatch):
    from media_cli.app.animethemes import _play_row
    from media_cli.playback import engine

    row, cfg, target = local_video
    launched = []
    monkeypatch.setattr(download_worker, "get_global_worker_if_started", lambda: None)
    monkeypatch.setattr(flow.app_core, "is_termux", lambda: True)
    monkeypatch.setattr(flow.app_core, "_exit_player_screen", lambda: None)
    monkeypatch.setattr(flow.app_core, "play_android", lambda *a, **kw: pytest.fail("Local song used remote stream launcher"))
    monkeypatch.setattr(engine, "play_local_video", lambda path, player: launched.append((path, player)) or True)
    assert _play_row(files.local_song_row(row, cfg), cfg, SimpleNamespace(player=None)) is True
    assert launched == [(str(target.path), "mpv")]


def test_missing_file_keeps_remote_stream_and_does_not_start_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(download_worker, "get_global_worker", lambda *a, **kw: pytest.fail("Playback started downloader"))
    row = song_row()
    assert files.local_song_row(row, {"download_dir": str(tmp_path)}) is row


def test_desktop_local_song_retains_media_title_and_no_history_identity(local_video, monkeypatch):
    from media_cli.app.animethemes import _play_row

    row, cfg, target = local_video
    monkeypatch.setattr(download_worker, "get_global_worker_if_started", lambda: None)
    monkeypatch.setattr(flow.app_core, "is_termux", lambda: False)
    monkeypatch.setattr(flow.app_core, "_exit_player_screen", lambda: None)
    def play(title, episode, stream, **kwargs):
        assert stream["link"] == str(target.path)
        assert stream["source_name"] == "Local"
        assert stream["media_title"].startswith("Opening by Singer - Anime OP 1")
        assert kwargs["show_id"] is None
        assert kwargs["aniskip_enabled"] is False
        return ("QUIT", 0, 0, 90, 0)
    monkeypatch.setattr(flow.app_core, "play_desktop", play)
    assert _play_row(files.local_song_row(row, cfg), cfg, SimpleNamespace(player=None))[0] == "QUIT"


def test_song_and_download_selection_rows_share_downloaded_mark(local_video, monkeypatch):
    row, cfg, _ = local_video
    monkeypatch.setattr(download_worker, "get_global_worker_if_started", lambda: None)
    menu = flow.create_song_menu(SimpleNamespace(), None, cfg, SimpleNamespace(quality="1080p"),
                                 anime_song_rows(anime_fixture()), 0, collection_id="test")
    assert menu.songs_view().items[0].label_text == "Opening OP 1  ✔"
    assert menu.songs_view().items[1].label_text == "Ending ED 1"
    marked = menu.songs_view().items[0].label
    assert marked.runs[-1].role == "episode.downloaded"
    assert menu.download_selection_view().items[0].label_text == "Opening OP 1  ✔"
    menu.session.dispatch(UiEvent("select", menu.root_id, menu.session.generation, "action:quality"))
    menu.session.dispatch(UiEvent("select", menu.session.current.id, menu.session.generation, "quality:0"))
    assert menu.songs_view().items[0].label_text == "Opening OP 1"
