import json
import os
import types
from pathlib import Path

import pytest

from media_cli.playback.mpv import MpvIpc
from media_cli.playback.desktop import play_desktop
from media_cli.torrents.runtime import (
    TorrentCancelled,
    TorrentRuntimeConfig,
    contiguous_ready_mib,
    first_missing_piece,
    prepare_torrent_stream,
    prioritize_stream,
    select_video_file,
)


class FakeFiles:
    def __init__(self, rows):
        self.rows = rows

    def num_files(self):
        return len(self.rows)

    def file_path(self, index):
        return self.rows[index][0]

    def file_size(self, index):
        return self.rows[index][1]


class FakeInfo:
    def __init__(self, rows, piece_length=1024 * 1024):
        self._files = FakeFiles(rows)
        self._piece_length = piece_length

    def files(self):
        return self._files

    def piece_length(self):
        return self._piece_length

    def map_file(self, index, offset, size):
        class Mapping:
            pass

        mapping = Mapping()
        mapping.piece = offset // self._piece_length
        return mapping


class FakeStatus:
    def __init__(self, pieces):
        self.pieces = pieces
        self.progress = 0.0
        self.total_done = 0
        self.download_rate = 0
        self.num_peers = 0
        self.num_seeds = 0


class FakeHandle:
    def __init__(self, pieces):
        self._status = FakeStatus(pieces)
        self.priorities = []
        self.deadlines = []
        self.cleared = False

    def status(self):
        return self._status

    def clear_piece_deadlines(self):
        self.cleared = True

    def piece_priority(self, piece, priority):
        self.priorities.append((piece, priority))

    def set_piece_deadline(self, piece, deadline):
        self.deadlines.append((piece, deadline))


def test_select_video_file_picks_largest_video():
    info = FakeInfo(
        [
            ("readme.txt", 1_000),
            ("episode-720p.mkv", 400),
            ("episode-1080p.mp4", 800),
        ]
    )

    assert select_video_file(info) == (2, 800, "episode-1080p.mp4")


def test_select_video_file_allows_large_extensionless_video_like_file():
    info = FakeInfo(
        [
            ("readme", 10_000),
            ("Movie.Title.2026.1080p.WEB-DL", 900 * 1024 * 1024),
        ]
    )

    assert select_video_file(info) == (1, 900 * 1024 * 1024, "Movie.Title.2026.1080p.WEB-DL")


def test_select_video_file_rejects_obvious_non_video_torrent():
    info = FakeInfo(
        [
            ("archive.zip", 900 * 1024 * 1024),
            ("book.pdf", 20 * 1024 * 1024),
            ("album.flac", 300 * 1024 * 1024),
        ]
    )

    try:
        select_video_file(info)
    except RuntimeError as exc:
        assert "No playable video file found" in str(exc)
    else:
        raise AssertionError("expected non-video torrent to be rejected")


def test_piece_readiness_helpers_use_contiguous_start_only():
    handle = FakeHandle([True, True, False, True])

    assert contiguous_ready_mib(handle, 0, 3, 1024 * 1024) == 2.0
    assert first_missing_piece(handle, 0, 3) == 2


def test_prioritize_stream_marks_start_window_only_by_default():
    handle = FakeHandle([False] * 10)

    prioritize_stream(handle, first_piece=0, last_piece=9, piece_length=1024 * 1024, buffer_mb=3, tail_mb=0)

    assert handle.cleared is True
    assert (0, 7) in handle.priorities
    assert (1, 7) in handle.priorities
    assert (2, 7) in handle.priorities
    assert all(piece < 3 for piece, _ in handle.priorities)


def test_prioritize_stream_can_mark_tail_when_explicitly_requested():
    handle = FakeHandle([False] * 10)

    prioritize_stream(handle, first_piece=0, last_piece=9, piece_length=1024 * 1024, buffer_mb=3, tail_mb=2)

    assert (8, 5) in handle.priorities
    assert (9, 5) in handle.priorities


def test_prepare_torrent_stream_uses_injected_runtime_and_cleans_cache(tmp_path):
    class FakeLt:
        class storage_mode_t:
            storage_mode_sparse = 1

        class torrent_flags:
            auto_managed = 1
            sequential_download = 2

        def __init__(self):
            self.handle = FakeTorrentHandle()

        def session(self, settings):
            return FakeSession(self.handle)

        def parse_magnet_uri(self, magnet):
            class Params:
                save_path = ""
                storage_mode = None
                flags = 0

            return Params()

    class FakeSession:
        def __init__(self, handle):
            self.handle = handle

        def add_torrent(self, params):
            self.params = params
            return self.handle

    class FakeTorrentHandle(FakeHandle):
        def __init__(self):
            super().__init__([True, True])
            self.file_priorities = []
            self.sequential = False

        def has_metadata(self):
            return True

        def torrent_file(self):
            return FakeInfo([("sample.mkv", 2 * 1024 * 1024)])

        def file_priority(self, index, priority):
            self.file_priorities.append((index, priority))

        def set_sequential_download(self, value):
            self.sequential = bool(value)

    cfg = TorrentRuntimeConfig(
        cache_root=tmp_path,
        metadata_timeout=1,
        buffer_mb=1,
        tail_buffer_mb=1,
        tick_seconds=0,
    )

    prepared = prepare_torrent_stream(
        {"type": "torrent", "link": "magnet:?xt=urn:btih:test"},
        config=cfg,
        lt_module=FakeLt(),
        sleep=lambda _: None,
    )

    assert prepared.path.endswith("sample.mkv")
    assert Path(prepared.path).parent.exists()

    prepared.close()

    assert not Path(prepared.path).parent.exists()


def test_prepare_torrent_stream_reports_progress(tmp_path):
    class FakeLt:
        class storage_mode_t:
            storage_mode_sparse = 1

        class torrent_flags:
            auto_managed = 1
            sequential_download = 2

        def __init__(self):
            self.handle = FakeTorrentHandle()

        def session(self, settings):
            return FakeSession(self.handle)

        def parse_magnet_uri(self, magnet):
            class Params:
                save_path = ""
                storage_mode = None
                flags = 0

            return Params()

    class FakeSession:
        def __init__(self, handle):
            self.handle = handle

        def add_torrent(self, params):
            return self.handle

    class FakeTorrentHandle(FakeHandle):
        def __init__(self):
            super().__init__([False, True])
            self.file_priorities = []
            self.sequential = False

        def has_metadata(self):
            return True

        def torrent_file(self):
            return FakeInfo([("sample.mkv", 2 * 1024 * 1024)])

        def file_priority(self, index, priority):
            self.file_priorities.append((index, priority))

        def set_sequential_download(self, value):
            self.sequential = bool(value)

    messages = []

    def fake_sleep(_):
        FakeLtInstance.handle._status.pieces[0] = True
        FakeLtInstance.handle._status.total_done = 1024 * 1024
        FakeLtInstance.handle._status.download_rate = 2 * 1024 * 1024
        FakeLtInstance.handle._status.num_peers = 5
        FakeLtInstance.handle._status.num_seeds = 2

    FakeLtInstance = FakeLt()
    prepared = prepare_torrent_stream(
        {"type": "torrent", "link": "magnet:?xt=urn:btih:test"},
        config=TorrentRuntimeConfig(cache_root=tmp_path, buffer_mb=1, tick_seconds=0),
        lt_module=FakeLtInstance,
        sleep=fake_sleep,
        progress_callback=messages.append,
    )

    prepared.close()

    assert any("Torrent: metadata ready" in message for message in messages)
    assert any("Torrent: buffering" in message for message in messages)
    assert any("Torrent: buffer ready" in message for message in messages)


def test_prepare_torrent_stream_can_be_cancelled_and_cleans_cache(tmp_path):
    class FakeLt:
        class storage_mode_t:
            storage_mode_sparse = 1

        class torrent_flags:
            auto_managed = 1
            sequential_download = 2

        def __init__(self):
            self.handle = FakeTorrentHandle()

        def session(self, settings):
            return FakeSession(self.handle)

        def parse_magnet_uri(self, magnet):
            class Params:
                save_path = ""
                storage_mode = None
                flags = 0

            return Params()

    class FakeSession:
        def __init__(self, handle):
            self.handle = handle

        def add_torrent(self, params):
            return self.handle

    class FakeTorrentHandle(FakeHandle):
        def __init__(self):
            super().__init__([False, False])

        def has_metadata(self):
            return True

        def torrent_file(self):
            return FakeInfo([("sample.mkv", 2 * 1024 * 1024)])

        def file_priority(self, index, priority):
            pass

        def set_sequential_download(self, value):
            pass

    fake_lt = FakeLt()

    with pytest.raises(TorrentCancelled):
        prepare_torrent_stream(
            {"type": "torrent", "link": "magnet:?xt=urn:btih:test"},
            config=TorrentRuntimeConfig(cache_root=tmp_path, buffer_mb=1, tick_seconds=0),
            lt_module=fake_lt,
            sleep=lambda _: None,
            cancel_check=lambda: True,
        )

    assert not any(tmp_path.iterdir())


def test_torrent_start_buffer_sleep_is_interruptible():
    from media_cli.torrents.runtime import _wait_for_start_buffer

    handle = FakeHandle([False, False, False, False])
    sleep_calls = []

    def fake_sleep(duration):
        sleep_calls.append(duration)

    def cancel_check():
        return bool(sleep_calls)

    with pytest.raises(TorrentCancelled):
        _wait_for_start_buffer(
            handle,
            first_piece=0,
            last_piece=3,
            piece_length=1024 * 1024,
            config=TorrentRuntimeConfig(buffer_mb=2, tick_seconds=2),
            sleep=fake_sleep,
            cancel_check=cancel_check,
        )

    assert sleep_calls == [0.1]


def test_torrent_runtime_does_not_start_on_stalled_partial_buffer_by_default():
    from media_cli.torrents.runtime import _wait_for_start_buffer

    handle = FakeHandle([True, False, False, False])
    calls = 0

    def fake_sleep(_):
        nonlocal calls
        calls += 1
        if calls >= 3:
            handle._status.pieces[1] = True

    _wait_for_start_buffer(
        handle,
        first_piece=0,
        last_piece=3,
        piece_length=1024 * 1024,
        config=TorrentRuntimeConfig(buffer_mb=2, tick_seconds=0, stalled_ticks=1, stalled_min_ready_mb=1),
        sleep=fake_sleep,
    )

    assert calls >= 3


def test_torrent_runtime_fails_when_buffering_has_no_active_swarm():
    from media_cli.torrents.runtime import _wait_for_start_buffer

    handle = FakeHandle([False, False, False, False])
    current_time = 0.0

    def fake_now():
        return current_time

    def fake_sleep(_):
        nonlocal current_time
        current_time += 2.0

    with pytest.raises(RuntimeError, match="no active peers"):
        _wait_for_start_buffer(
            handle,
            first_piece=0,
            last_piece=3,
            piece_length=1024 * 1024,
            config=TorrentRuntimeConfig(buffer_mb=2, tick_seconds=0, no_swarm_timeout=5),
            sleep=fake_sleep,
            now=fake_now,
        )


def test_desktop_playback_prepares_torrent_before_loading_mpv(monkeypatch):
    class FakePrepared:
        path = "/tmp/anyplay-prepared-video.mkv"

        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class FakeIpc:
        props = {"percent-pos": 80, "playback-time": 120, "duration": 150}

        def __init__(self):
            self.loaded = None

        def load(self, *args, **kwargs):
            self.loaded = (args, kwargs)

        def wait_for_playback(self, *args, **kwargs):
            return "EOF", 120

    prepared = FakePrepared()

    progress_messages = []

    def fake_prepare(stream, *, progress_callback=None, cancel_check=None):
        assert stream["type"] == "torrent"
        assert stream["link"].startswith("magnet:?")
        assert progress_callback is not None
        progress_callback("Torrent: buffer ready")
        return prepared

    monkeypatch.setattr("media_cli.torrents.runtime.prepare_torrent_stream", fake_prepare)

    ipc = FakeIpc()
    result = play_desktop(
        ipc,
        "Slime",
        "23",
        {"type": "torrent", "link": "magnet:?xt=urn:btih:test", "resolution": "1080p"},
        get_resume_time=lambda *args: 0,
        get_preferred_mirror=lambda *args: {},
        update_stream_info=lambda info: None,
        torrent_progress_callback=progress_messages.append,
    )

    assert ipc.loaded[0][0] == "/tmp/anyplay-prepared-video.mkv"
    assert prepared.closed is True
    assert progress_messages == ["Torrent: buffer ready"]
    assert result == ("EOF", 80, 120, 150, 120)


def test_desktop_torrent_movie_title_does_not_show_episode_file(monkeypatch):
    class FakePrepared:
        path = "/tmp/avatar.mkv"

        def close(self):
            pass

    class FakeIpc:
        props = {"percent-pos": 0, "playback-time": 0, "duration": 0}

        def __init__(self):
            self.loaded = None

        def load(self, *args, **kwargs):
            self.loaded = (args, kwargs)

        def wait_for_playback(self, *args, **kwargs):
            return "EOF", 0

    monkeypatch.setattr(
        "media_cli.torrents.runtime.prepare_torrent_stream",
        lambda stream, **kwargs: FakePrepared(),
    )

    ipc = FakeIpc()
    play_desktop(
        ipc,
        "Avatar (2009) [2160p] [4K] [BluRay] [5.1] [YTS.MX]",
        "File",
        {"type": "torrent", "link": "magnet:?xt=urn:btih:test", "resolution": "2160p"},
        get_resume_time=lambda *args: 0,
        get_preferred_mirror=lambda *args: {},
        update_stream_info=lambda info: None,
    )

    assert ipc.loaded[0][1] == "Avatar (2009) [2160p] [4K] [BluRay] [5.1] [YTS.MX] (2160p)"
    assert ipc.loaded[1]["suppress_chapters"] is True


def test_desktop_playback_returns_cancelled_when_torrent_loading_is_cancelled(monkeypatch):
    class FakeIpc:
        props = {}

        def load(self, *args, **kwargs):
            raise AssertionError("mpv should not load after torrent loading is cancelled")

    progress_messages = []

    def fake_prepare(stream, *, progress_callback=None, cancel_check=None):
        assert cancel_check is not None
        raise TorrentCancelled("Torrent loading cancelled.")

    monkeypatch.setattr("media_cli.torrents.runtime.prepare_torrent_stream", fake_prepare)

    result = play_desktop(
        FakeIpc(),
        "Slime",
        "23",
        {"type": "torrent", "link": "magnet:?xt=urn:btih:test", "resolution": "1080p"},
        get_resume_time=lambda *args: 0,
        get_preferred_mirror=lambda *args: {},
        update_stream_info=lambda info: None,
        torrent_progress_callback=progress_messages.append,
        cancel_check=lambda: True,
    )

    assert progress_messages == ["Torrent loading cancelled."]
    assert result == ("CANCELLED", 0, 0, 0, 0)


def test_desktop_playback_swallows_cancel_raised_by_progress_callback(monkeypatch):
    class FakeIpc:
        props = {}

        def load(self, *args, **kwargs):
            raise AssertionError("mpv should not load after torrent loading is cancelled")

    def fake_prepare(stream, *, progress_callback=None, cancel_check=None):
        raise TorrentCancelled("Torrent loading cancelled.")

    def raising_progress(message):
        raise TorrentCancelled("Torrent loading cancelled.")

    monkeypatch.setattr("media_cli.torrents.runtime.prepare_torrent_stream", fake_prepare)

    result = play_desktop(
        FakeIpc(),
        "Slime",
        "23",
        {"type": "torrent", "link": "magnet:?xt=urn:btih:test", "resolution": "1080p"},
        get_resume_time=lambda *args: 0,
        get_preferred_mirror=lambda *args: {},
        update_stream_info=lambda info: None,
        torrent_progress_callback=raising_progress,
        cancel_check=lambda: True,
    )

    assert result == ("CANCELLED", 0, 0, 0, 0)


def test_desktop_playback_reports_error_when_torrent_has_no_video(monkeypatch):
    class FakeIpc:
        props = {}

        def load(self, *args, **kwargs):
            raise AssertionError("mpv should not be loaded for a non-video torrent")

    progress_messages = []

    def fake_prepare(stream, *, progress_callback=None, cancel_check=None):
        raise RuntimeError("No playable video file found in this torrent.")

    monkeypatch.setattr("media_cli.torrents.runtime.prepare_torrent_stream", fake_prepare)

    result = play_desktop(
        FakeIpc(),
        "Slime",
        "23",
        {"type": "torrent", "link": "magnet:?xt=urn:btih:test", "resolution": "1080p"},
        get_resume_time=lambda *args: 0,
        get_preferred_mirror=lambda *args: {},
        update_stream_info=lambda info: None,
        torrent_progress_callback=progress_messages.append,
    )

    assert progress_messages == ["No playable video file found in this torrent."]
    assert result == ("ERROR", 0, 0, 0, 0)


def test_mpv_ipc_disables_autoload_files(monkeypatch, tmp_path):
    commands = []

    class FakeProcess:
        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    class FakeSocket:
        def connect(self, path):
            self.path = path

        def setblocking(self, value):
            self.blocking = value

        def sendall(self, data):
            pass

        def close(self):
            pass

    def fake_popen(cmd, **kwargs):
        commands.append(cmd)
        return FakeProcess()

    runtime_dir = tmp_path / "mpv-runtime"
    runtime_dir.mkdir()
    socket_path = runtime_dir / "mpv.sock"
    conf_path = runtime_dir / "input.conf"
    chapters_path = runtime_dir / "chapters.txt"
    lua_path = runtime_dir / "aniskip.lua"

    monkeypatch.setattr(
        "media_cli.playback.mpv.create_mpv_runtime",
        lambda: (str(runtime_dir), str(socket_path), str(conf_path), str(chapters_path), str(lua_path)),
    )
    monkeypatch.setattr("media_cli.playback.mpv.cleanup_mpv_runtime", lambda runtime_dir: None)
    monkeypatch.setattr("media_cli.playback.mpv.subprocess.Popen", fake_popen)
    monkeypatch.setattr("media_cli.playback.mpv.os.path.exists", lambda path: True)
    monkeypatch.setattr("media_cli.playback.mpv.socket.socket", lambda *args, **kwargs: FakeSocket())

    ipc = MpvIpc()
    ipc.start()

    assert "--autoload-files=no" in commands[0]


def test_mpv_ipc_enables_osd_shadow(monkeypatch, tmp_path):
    commands = []

    class FakeProcess:
        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    class FakeSocket:
        def connect(self, path):
            self.path = path

        def setblocking(self, value):
            self.blocking = value

        def sendall(self, data):
            pass

        def close(self):
            pass

    def fake_popen(cmd, **kwargs):
        commands.append(cmd)
        return FakeProcess()

    runtime_dir = tmp_path / "mpv-runtime"
    runtime_dir.mkdir()
    socket_path = runtime_dir / "mpv.sock"
    conf_path = runtime_dir / "input.conf"
    chapters_path = runtime_dir / "chapters.txt"
    lua_path = runtime_dir / "aniskip.lua"

    monkeypatch.setattr(
        "media_cli.playback.mpv.create_mpv_runtime",
        lambda: (str(runtime_dir), str(socket_path), str(conf_path), str(chapters_path), str(lua_path)),
    )
    monkeypatch.setattr("media_cli.playback.mpv.cleanup_mpv_runtime", lambda runtime_dir: None)
    monkeypatch.setattr("media_cli.playback.mpv.subprocess.Popen", fake_popen)
    monkeypatch.setattr("media_cli.playback.mpv.os.path.exists", lambda path: True)
    monkeypatch.setattr("media_cli.playback.mpv.socket.socket", lambda *args, **kwargs: FakeSocket())

    ipc = MpvIpc()
    ipc.start()

    assert "--osd-shadow-offset=2" in commands[0]
    assert "--osd-shadow-color=#B0000000" in commands[0]


def test_mpv_ipc_can_suppress_embedded_chapters(monkeypatch, tmp_path):
    sent_commands = []

    class FakeProcess:
        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

    class FakeSocket:
        def connect(self, path):
            self.path = path

        def setblocking(self, value):
            self.blocking = value

        def sendall(self, data):
            sent_commands.append(json.loads(data.decode("utf-8"))["command"])

        def recv(self, size):
            raise BlockingIOError()

        def close(self):
            pass

    runtime_dir = tmp_path / "mpv-runtime"
    runtime_dir.mkdir()
    socket_path = runtime_dir / "mpv.sock"
    conf_path = runtime_dir / "input.conf"
    chapters_path = runtime_dir / "chapters.txt"
    lua_path = runtime_dir / "aniskip.lua"

    monkeypatch.setattr(
        "media_cli.playback.mpv.create_mpv_runtime",
        lambda: (str(runtime_dir), str(socket_path), str(conf_path), str(chapters_path), str(lua_path)),
    )
    monkeypatch.setattr("media_cli.playback.mpv.cleanup_mpv_runtime", lambda runtime_dir: None)
    monkeypatch.setattr("media_cli.playback.mpv.subprocess.Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr("media_cli.playback.mpv.os.path.exists", lambda path: True)
    monkeypatch.setattr("media_cli.playback.mpv.socket.socket", lambda *args, **kwargs: FakeSocket())

    ipc = MpvIpc()
    ipc.load(
        "/tmp/avatar.mkv",
        "Avatar",
        {},
        "",
        suppress_chapters=True,
    )

    assert ["script-message", "set_skip_intervals", "[]", "yes", "clear"] in sent_commands


def test_mpv_runtime_reapplies_open_ended_chapters_when_duration_arrives():
    from media_cli.playback import mpv_runtime

    script = mpv_runtime.ANISKIP_LUA_SCRIPT

    assert "mp.observe_property(\"duration\", \"number\", on_duration)" in script
    assert "interval_end(item)" in script


def test_mpv_runtime_input_config_combines_user_bindings(monkeypatch, tmp_path):
    from media_cli.playback import mpv_runtime

    user_conf = tmp_path / "user-input.conf"
    user_conf.write_text(
        "a cycle audio\n"
        "n cycle video-unscaled\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mpv_runtime, "_candidate_user_input_confs", lambda: [str(user_conf)])

    runtime_dir, _, config_path, _, _ = mpv_runtime.create_mpv_runtime()
    try:
        text = Path(config_path).read_text(encoding="utf-8")
    finally:
        mpv_runtime.cleanup_mpv_runtime(runtime_dir)

    assert "a cycle audio" in text
    assert "n cycle video-unscaled" in text
    assert "# ---- AnyPlay playback controls ----" in text
    assert text.index("n cycle video-unscaled") < text.index("SHIFT+RIGHT script-message next_ep")
    assert "SHIFT+RIGHT script-message next_ep" in text
    assert "SHIFT+LEFT script-message prev_ep" in text
    assert "TAB script-message skip_interval" in text
    assert "s script-message skip_interval" not in text
    assert "n script-message next_mirror" not in text


def test_mpv_runtime_input_config_has_anyplay_defaults_without_user_config(monkeypatch):
    from media_cli.playback import mpv_runtime

    monkeypatch.setattr(mpv_runtime, "_candidate_user_input_confs", lambda: [])

    runtime_dir, _, config_path, _, _ = mpv_runtime.create_mpv_runtime()
    try:
        text = Path(config_path).read_text(encoding="utf-8")
    finally:
        mpv_runtime.cleanup_mpv_runtime(runtime_dir)

    assert "SHIFT+RIGHT script-message next_ep" in text
    assert "SHIFT+LEFT script-message prev_ep" in text
    assert "TAB script-message skip_interval" in text
    assert "s script-message skip_interval" not in text
    assert "n script-message next_mirror" not in text


def test_loading_cancel_accepts_q_and_escape_keys():
    from media_cli.ui import player_screen

    player_screen.reset_loading_cancel()
    assert player_screen.loading_cancel_requested() is False

    assert player_screen._handle_loading_cancel_bytes(b"xq") is True
    assert player_screen.loading_cancel_requested() is True

    player_screen.reset_loading_cancel()
    assert player_screen._handle_loading_cancel_bytes(b"\x1b") is True
    assert player_screen.loading_cancel_requested() is True


def test_player_screen_drops_blocking_redraw_without_crashing(monkeypatch):
    from media_cli.context import FLAGS
    from media_cli.ui import player_screen

    class BlockingStdout:
        def isatty(self):
            return True

        def write(self, value):
            raise BlockingIOError(11, "write could not complete without blocking")

        def flush(self):
            pass

    old_plain_mode = getattr(FLAGS, "plain_mode", False)
    FLAGS.plain_mode = False
    monkeypatch.setattr(player_screen.sys, "stdout", BlockingStdout())
    monkeypatch.setattr(player_screen.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(player_screen.os, "get_terminal_size", lambda fd=None: os.terminal_size((100, 30)))

    try:
        player_screen.activate({"name": "Show", "type": "TV"}, "1", 12)
        assert player_screen.add_status_line("Loading source") is True
    finally:
        player_screen._player_ui_state["active"] = False
        FLAGS.plain_mode = old_plain_mode


def test_loading_ticker_starts_cancel_listener_when_ticker_already_alive(monkeypatch):
    from media_cli.context import FLAGS
    from media_cli.ui import player_screen

    class AliveThread:
        def is_alive(self):
            return True

    started = []
    old_plain_mode = getattr(FLAGS, "plain_mode", False)
    monkeypatch.setattr(player_screen.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(player_screen, "_ticker_thread", AliveThread())
    monkeypatch.setattr(player_screen, "_start_loading_input_thread", lambda: started.append(True))
    FLAGS.plain_mode = False
    try:
        player_screen.start_loading_ticker()
    finally:
        FLAGS.plain_mode = old_plain_mode

    assert started == [True]


def test_loading_cancel_poll_prefers_stdin_over_tty_fd(monkeypatch):
    from media_cli.ui import player_screen

    selected = []
    player_screen.reset_loading_cancel()
    monkeypatch.setattr(player_screen, "_loading_tty_fd", 42)
    monkeypatch.setattr(player_screen.sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(player_screen, "_open_loading_stdin_input", lambda: 43)
    monkeypatch.setattr(player_screen.select, "select", lambda fds, *args: (selected.extend(fds) or [], [], []))

    player_screen._poll_loading_cancel_input()

    assert selected == [43]


def test_loading_stdin_input_sets_cbreak_noecho_and_restores(monkeypatch):
    from media_cli.ui import player_screen

    fd = 55
    old_attrs = [0, 0, 0, 0b1111, 0, 0, [0] * 32]
    new_attrs_seen = []
    blocking_state = []

    monkeypatch.setattr(player_screen.sys.stdin, "fileno", lambda: fd)
    monkeypatch.setattr(player_screen.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(player_screen.os, "get_blocking", lambda value: True)
    monkeypatch.setattr(player_screen.os, "set_blocking", lambda value, state: blocking_state.append((value, state)))

    class FakeTermios:
        ECHO = 0b1000
        ICANON = 0b0100
        VMIN = 6
        VTIME = 5
        TCSANOW = 0

        @staticmethod
        def tcgetattr(value):
            assert value == fd
            return [old_attrs[0], old_attrs[1], old_attrs[2], old_attrs[3], old_attrs[4], old_attrs[5], list(old_attrs[6])]

        @staticmethod
        def tcsetattr(value, when, attrs):
            assert value == fd
            new_attrs_seen.append(attrs)

    monkeypatch.setitem(__import__("sys").modules, "termios", FakeTermios)
    player_screen._loading_stdin_fd = None
    player_screen._loading_stdin_old_term = None
    player_screen._loading_stdin_old_blocking = None

    try:
        assert player_screen._open_loading_stdin_input() == fd
        assert new_attrs_seen[0][3] & FakeTermios.ECHO == 0
        assert new_attrs_seen[0][3] & FakeTermios.ICANON == 0
        assert blocking_state[0] == (fd, False)

        player_screen._close_loading_stdin_input()
        assert new_attrs_seen[-1][3] == old_attrs[3]
        assert blocking_state[-1] == (fd, True)
    finally:
        player_screen._loading_stdin_fd = None
        player_screen._loading_stdin_old_term = None
        player_screen._loading_stdin_old_blocking = None


def test_loading_stdin_input_reapplies_noecho_if_terminal_is_restored(monkeypatch):
    from media_cli.ui import player_screen

    fd = 56
    echo_attrs = [0, 0, 0, 0b1111, 0, 0, [0] * 32]
    attrs_seen = []

    monkeypatch.setattr(player_screen.sys.stdin, "fileno", lambda: fd)
    monkeypatch.setattr(player_screen.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(player_screen.os, "get_blocking", lambda value: True)
    monkeypatch.setattr(player_screen.os, "set_blocking", lambda value, state: None)

    class FakeTermios:
        ECHO = 0b1000
        ICANON = 0b0100
        VMIN = 6
        VTIME = 5
        TCSANOW = 0

        @staticmethod
        def tcgetattr(value):
            assert value == fd
            return [echo_attrs[0], echo_attrs[1], echo_attrs[2], echo_attrs[3], echo_attrs[4], echo_attrs[5], list(echo_attrs[6])]

        @staticmethod
        def tcsetattr(value, when, attrs):
            assert value == fd
            attrs_seen.append(attrs)

    monkeypatch.setitem(__import__("sys").modules, "termios", FakeTermios)
    player_screen._loading_stdin_fd = None
    player_screen._loading_stdin_old_term = None
    player_screen._loading_stdin_old_blocking = None

    try:
        assert player_screen._open_loading_stdin_input() == fd
        assert player_screen._open_loading_stdin_input() == fd
        assert len(attrs_seen) >= 2
        assert attrs_seen[-1][3] & FakeTermios.ECHO == 0
        assert attrs_seen[-1][3] & FakeTermios.ICANON == 0
    finally:
        player_screen._loading_stdin_fd = None
        player_screen._loading_stdin_old_term = None
        player_screen._loading_stdin_old_blocking = None


def test_loading_tty_input_sets_noecho_when_stdin_unavailable(monkeypatch):
    from media_cli.ui import player_screen

    fd = 77
    old_attrs = [0, 0, 0, 0b1111, 0, 0, [0] * 32]
    attrs_seen = []
    blocking_state = []
    closed = []

    class FakeFile:
        def close(self):
            closed.append(True)

    monkeypatch.setattr(
        player_screen,
        "os",
        types.SimpleNamespace(
            O_RDWR=0,
            open=lambda *args, **kwargs: fd,
            fdopen=lambda *args, **kwargs: FakeFile(),
            get_blocking=lambda value: True,
            set_blocking=lambda value, state: blocking_state.append((value, state)),
        ),
    )

    class FakeTermios:
        ECHO = 0b1000
        ICANON = 0b0100
        VMIN = 6
        VTIME = 5
        TCSANOW = 0
        TCSADRAIN = 1

        @staticmethod
        def tcgetattr(value):
            assert value == fd
            return [old_attrs[0], old_attrs[1], old_attrs[2], old_attrs[3], old_attrs[4], old_attrs[5], list(old_attrs[6])]

        @staticmethod
        def tcsetattr(value, when, attrs):
            assert value == fd
            attrs_seen.append(attrs)

    monkeypatch.setitem(__import__("sys").modules, "termios", FakeTermios)
    player_screen._loading_tty_file = None
    player_screen._loading_tty_fd = None
    player_screen._loading_tty_old_term = None
    player_screen._loading_tty_old_blocking = None

    try:
        player_screen._open_loading_input()
        assert attrs_seen[0][3] & FakeTermios.ECHO == 0
        assert attrs_seen[0][3] & FakeTermios.ICANON == 0
        assert blocking_state[0] == (fd, False)

        player_screen._close_loading_input()
        assert attrs_seen[-1][3] == old_attrs[3]
        assert blocking_state[-1] == (fd, True)
        assert closed == [True]
    finally:
        player_screen._loading_tty_file = None
        player_screen._loading_tty_fd = None
        player_screen._loading_tty_old_term = None
        player_screen._loading_tty_old_blocking = None
