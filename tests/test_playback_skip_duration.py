import json

from media_cli.playback.mpv import MpvIpc


def player(monkeypatch):
    ipc = MpvIpc()
    commands = []
    monkeypatch.setattr(ipc, "start", lambda: None)
    monkeypatch.setattr(ipc, "send_cmd", lambda *args: commands.append(args))
    return ipc, commands


def candidate():
    return {"type": "op", "label": "Opening", "start": 0, "end": 90, "episode_length": 4921}


def test_remote_skips_cannot_seek_or_make_chapters_before_duration(monkeypatch):
    ipc, commands = player(monkeypatch)
    ipc.load("/tmp/video.mp4", "Title", {}, "", skip_intervals=[candidate()], skip_lookup=(52034, 1))
    assert ipc.resume_time == 0
    assert ipc.skip_intervals == []
    assert ("script-message", "set_skip_intervals", "[]", "yes", "keep") in commands


def test_matching_runtime_activates_same_intervals_for_lua_and_python(monkeypatch):
    ipc, commands = player(monkeypatch)
    ipc.load("/tmp/video.mp4", "Title", {}, "", skip_intervals=[candidate()], skip_lookup=(52034, 1))
    ipc.props["duration"] = 4921
    ipc._refresh_remote_skips()
    assert ipc.skip_intervals == [candidate()]
    messages = [cmd for cmd in commands if cmd[:2] == ("script-message", "set_skip_intervals")]
    assert json.loads(messages[-1][2]) == ipc.skip_intervals


def test_loading_next_file_clears_old_remote_lookup(monkeypatch):
    ipc, commands = player(monkeypatch)
    ipc.load("/tmp/first.mp4", "First", {}, "", skip_intervals=[candidate()], skip_lookup=(52034, 1))
    ipc.props["duration"] = 4921
    ipc._refresh_remote_skips()
    ipc.load("/tmp/second.mp4", "Second", {}, "")
    assert ipc.skip_intervals == []
    ipc.props["duration"] = 4921
    ipc._refresh_remote_skips()
    assert ipc.skip_intervals == []


def test_custom_skips_keep_starting_seek_and_do_not_use_remote_validation(monkeypatch):
    ipc, commands = player(monkeypatch)
    custom = {"type": "op", "label": "Opening", "start": 0, "end": 90}
    ipc.load("/tmp/video.mp4", "Title", {}, "", skip_intervals=[custom])
    assert ipc.resume_time == 90
    assert ipc.skip_intervals == [custom]


def test_desktop_does_not_wait_for_network_before_loading(monkeypatch):
    from media_cli.playback import desktop

    loaded = {}

    class FakeIpc:
        props = {}

        def load(self, *args, **kwargs):
            loaded.update(kwargs)

        def wait_for_playback(self, *args):
            return "QUIT", 0

    def unexpected_network(*args, **kwargs):
        raise AssertionError("Playback must not fetch skips synchronously")

    monkeypatch.setattr("media_cli.media.aniskip.fetch_skip_times", unexpected_network)
    desktop.play_desktop(
        FakeIpc(),
        "Title",
        "1",
        {"link": "/tmp/video.mp4"},
        get_resume_time=lambda *args: 0,
        get_preferred_mirror=lambda *args: {},
        update_stream_info=lambda *args: None,
        mal_id=52034,
    )
    assert loaded["skip_lookup"] == (52034, 1)


def test_android_exports_candidates_even_when_duration_is_unknown(monkeypatch):
    from media_cli.playback import android

    written = []
    monkeypatch.setattr("media_cli.media.aniskip.generate_chapters_file", lambda skips, path: written.append(skips))
    monkeypatch.setattr("media_cli.media.aniskip.fetch_skip_times", lambda *a, **kw: [candidate()])
    monkeypatch.setattr(android.subprocess, "run", lambda *a, **kw: None)
    android.play_android("Title", 1, {"link": "https://example.org/video.mp4"}, None, player="vlc", mal_id=52034)
    assert written == [[candidate()]]


def test_android_exports_mismatched_duration_candidates_without_length_matching(monkeypatch):
    from media_cli.playback import android

    written, requested = [], []

    def fetch(*args, **kwargs):
        requested.append(kwargs)
        return [candidate()]

    monkeypatch.setattr("media_cli.media.aniskip.generate_chapters_file", lambda skips, path: written.append(skips))
    monkeypatch.setattr("media_cli.media.aniskip.fetch_skip_times", fetch)
    monkeypatch.setattr(android.subprocess, "run", lambda *a, **kw: None)
    android.play_android(
        "Title", 1, {"link": "https://example.org/video.mp4", "duration": 1465}, None, player="vlc", mal_id=52034
    )
    assert requested == [{"episode_length": 0.0}]
    assert written == [[candidate()]]


def test_duration_event_activates_skips_before_same_batch_playback_time(monkeypatch):
    from media_cli.playback import mpv as mpv_mod

    ipc, commands = player(monkeypatch)
    ipc.load("/tmp/video.mp4", "Title", {}, "", skip_intervals=[candidate()], skip_lookup=(52034, 1))

    class Client:
        def recv(self, size):
            return (
                b'{"event":"file-loaded"}\n'
                b'{"event":"property-change","name":"duration","data":4921}\n'
                b'{"event":"property-change","name":"playback-time","data":1}\n'
                b'{"event":"end-file","reason":"quit"}\n'
            )

    class Stdin:
        def fileno(self):
            return 0

    ipc.client = Client()
    monkeypatch.setattr(mpv_mod.sys, "stdin", Stdin())
    monkeypatch.setattr(mpv_mod.os, "isatty", lambda fd: False)
    monkeypatch.setattr(mpv_mod.select, "select", lambda *args: ([ipc.client], [], []))
    result, _ = ipc.wait_for_playback({"episode_index": 0}, 1, 1, None)
    assert result == "QUIT"
    assert ("seek", 90.0, "absolute") in commands


def test_runtime_rejection_clears_previously_generated_chapters(monkeypatch):
    ipc, commands = player(monkeypatch)
    ipc.load("/tmp/video.mp4", "Title", {}, "", skip_intervals=[candidate()], skip_lookup=(52034, 1))
    ipc.props["duration"] = 4921
    ipc._refresh_remote_skips()
    # Prevent a real background network request; this checks chapter invalidation.
    monkeypatch.setattr(ipc._remote_skip_lookup, "poll", lambda duration: ())
    ipc.props["duration"] = 1465
    ipc._refresh_remote_skips()
    assert commands[-1] == ("script-message", "set_skip_intervals", "[]", "yes", "clear")


def test_torrent_suppression_does_not_depend_on_unvalidated_candidates(monkeypatch):
    from types import SimpleNamespace

    from media_cli.playback import desktop

    loaded = {}

    class FakeIpc:
        props = {}

        def load(self, *args, **kwargs):
            loaded.update(kwargs)

        def wait_for_playback(self, *args):
            return "QUIT", 0

    monkeypatch.setattr(
        "media_cli.torrents.runtime.prepare_torrent_stream",
        lambda *a, **kw: SimpleNamespace(path="/tmp/video.mkv", close=lambda: None),
    )
    monkeypatch.setattr("media_cli.media.aniskip.cached_skip_times", lambda *a, **kw: [candidate()])
    desktop.play_desktop(
        FakeIpc(),
        "Title",
        "1",
        {"link": "magnet:?xt=urn:btih:abc", "type": "torrent"},
        get_resume_time=lambda *args: 0,
        get_preferred_mirror=lambda *args: {},
        update_stream_info=lambda *args: None,
        mal_id=52034,
    )
    assert loaded["suppress_chapters"] is True
