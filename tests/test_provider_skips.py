"""Provider skip ranges have precedence without pretending to be AniSkip data."""

from concurrent.futures import Future

import pytest

from media_cli.media.provider_skips import normalize_provider_skips, playback_skip_intervals
from media_cli.playback.skip_lookup import SkipLookup


def native(kind="op", start=5, end=90):
    return {"type": kind, "label": "Opening" if kind == "op" else "Ending",
            "start": float(start), "end": float(end), "source": "provider"}


def test_normalize_gateway_ranges_without_inventing_runtime():
    assert normalize_provider_skips({"op": {"start": 5, "end": 90}, "ed": None}) == [native()]


@pytest.mark.parametrize("bad", [None, [], {"op": None}, {"op": {"start": True, "end": 90}},
                                    {"op": {"start": -1, "end": 90}}, {"op": {"start": 90, "end": 5}},
                                    {"op": {"start": 0, "end": float("inf")}}])
def test_malformed_native_ranges_are_ignored(bad):
    assert normalize_provider_skips(bad) == []


def test_desktop_native_ranges_require_actual_runtime_and_fit_within_it():
    assert playback_skip_intervals([native()], 0) == []
    assert playback_skip_intervals([native()], 60) == []
    assert playback_skip_intervals([native()], 100) == [native()]


class Executor:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args, **kwargs):
        future = Future()
        self.calls.append((args, kwargs, future))
        return future

    def shutdown(self, **kwargs):
        pass


@pytest.mark.parametrize("kind", ["op", "ed"])
def test_partial_native_data_is_exclusive_and_does_not_request_aniskip(monkeypatch, kind):
    executor = Executor()
    monkeypatch.setattr("media_cli.playback.skip_lookup.DaemonExecutor", lambda **kw: executor)
    lookup = SkipLookup()
    lookup.reset(20, 1, [native(kind)])
    assert lookup.poll(1400) == (native(kind),)
    assert not executor.calls
    lookup.close()


def test_cached_aniskip_cannot_add_missing_types_to_native_data():
    remote_op = {"type": "op", "start": 0, "end": 85, "episode_length": 1400}
    remote_ed = {"type": "ed", "start": 1310, "end": 1390, "episode_length": 1400}
    lookup = SkipLookup()
    lookup.reset(20, 1, [native("ed", 1327, 1395), remote_op, remote_ed])
    assert lookup.poll(1400) == (native("ed", 1327, 1395),)
    lookup.close()


def test_complete_provider_ranges_need_no_aniskip_request(monkeypatch):
    executor = Executor()
    monkeypatch.setattr("media_cli.playback.skip_lookup.DaemonExecutor", lambda **kw: executor)
    lookup = SkipLookup()
    rows = [native(), native("ed", 1310, 1390)]
    lookup.reset(20, 1, rows)
    assert lookup.poll(1400) == tuple(rows)
    assert not executor.calls
    lookup.close()


def test_invalid_for_runtime_provider_range_allows_fallback(monkeypatch):
    executor = Executor()
    monkeypatch.setattr("media_cli.playback.skip_lookup.DaemonExecutor", lambda **kw: executor)
    lookup = SkipLookup()
    lookup.reset(20, 1, [native("ed", 1500, 1590)])
    assert lookup.poll(1400) == ()
    fallback = {"type": "ed", "start": 1310, "end": 1390, "episode_length": 1400}
    executor.calls[0][2].set_result([fallback])
    assert lookup.poll(1400) == (fallback,)
    lookup.close()


def test_native_skips_work_without_tracker_mapping():
    lookup = SkipLookup()
    lookup.reset(None, 1, [native()])
    assert lookup.poll(1400) == (native(),)
    lookup.close()


def test_extracted_provider_streams_preserve_native_skips_too(monkeypatch):
    from media_cli.media import resolver

    monkeypatch.setattr(resolver, "_resolve_source_impl", lambda *a, **kw: [{"link": "https://example.org/video"}])
    source = {"sourceName": "Any provider", "sourceUrl": "https://example.org/embed",
              "skip_intervals": [native()]}
    assert resolver.resolve_source(source, silent=True)[0]["skip_intervals"] == [native()]


def test_desktop_wires_native_candidates_but_custom_and_disabled_keep_priority(monkeypatch):
    from media_cli.playback.desktop import play_desktop

    loaded = []

    class Ipc:
        props = {}

        def load(self, *args, **kwargs):
            loaded.append(kwargs)

        def wait_for_playback(self, *args):
            return "QUIT", 0

    def play(**kwargs):
        return play_desktop(Ipc(), "Title", "1", {"link": "/tmp/test.mp4", "skip_intervals": [native()]},
                            get_resume_time=lambda *a: 0, get_preferred_mirror=lambda *a: {},
                            update_stream_info=lambda *a: None, **kwargs)

    play()
    assert loaded[-1]["skip_intervals"] == [native()]
    assert loaded[-1]["skip_lookup"] == (None, 1)
    play(aniskip_enabled=False)
    assert loaded[-1]["skip_intervals"] == []
    assert "skip_lookup" not in loaded[-1]
    custom = [{"type": "op", "start": 0, "end": 30}]
    play(custom_skip_intervals=custom)
    assert loaded[-1]["skip_intervals"] == custom
    assert "skip_lookup" not in loaded[-1]


@pytest.mark.parametrize("complete", [False, True])
def test_android_exports_native_exclusively_without_filling_missing_types(monkeypatch, complete):
    from media_cli.media import aniskip
    from media_cli.playback import android

    written, calls = [], []
    rows = [native(), native("ed", 1310, 1390)] if complete else [native()]
    remote_ed = {"type": "ed", "start": 1310, "end": 1390, "episode_length": 1400}

    def fetch(*args, **kwargs):
        calls.append(kwargs)
        return [{"type": "op", "start": 0, "end": 80, "episode_length": 1400}, remote_ed]

    monkeypatch.setattr(aniskip, "generate_chapters_file", lambda rows, path: written.append(rows))
    monkeypatch.setattr(android.subprocess, "run", lambda *a, **kw: None)
    android.play_android("Title", 1, {"link": "https://example.org/video.mp4", "skip_intervals": rows},
                         None, player="vlc", mal_id=20, fetch_skip_times_fn=fetch)
    assert written == [rows]
    assert calls == []


def test_valid_native_type_does_not_mix_with_fallback_for_invalid_native_type():
    remote_op = {"type": "op", "start": 0, "end": 85, "episode_length": 1400}
    assert playback_skip_intervals([native("op", 1500, 1590), native("ed", 1310, 1390), remote_op], 1400) == [native("ed", 1310, 1390)]


def test_inflight_aniskip_result_does_not_replace_native_when_duration_changes(monkeypatch):
    executor = Executor()
    monkeypatch.setattr("media_cli.playback.skip_lookup.DaemonExecutor", lambda **kw: executor)
    lookup = SkipLookup()
    rows = [native("ed", 1400, 1490)]
    lookup.reset(20, 1, rows)
    assert lookup.poll(1300) == ()
    assert len(executor.calls) == 1
    executor.calls[0][2].set_result([{"type": "op", "start": 10, "end": 90, "episode_length": 1500}])
    assert lookup.poll(1500) == tuple(rows)
    assert len(executor.calls) == 1
    lookup.close()


def test_android_native_skips_obey_disable_and_work_without_mal(monkeypatch):
    from media_cli.media import aniskip
    from media_cli.playback import android

    written = []
    monkeypatch.setattr(aniskip, "generate_chapters_file", lambda rows, path: written.append(rows))
    monkeypatch.setattr(android.subprocess, "run", lambda *a, **kw: None)
    monkeypatch.setattr(aniskip, "fetch_skip_times", lambda *a, **kw: pytest.fail("No tracker mapping"))
    for enabled in (True, False):
        android.play_android("Title", 1, {"link": "https://example.org/video.mp4", "skip_intervals": [native()]},
                             None, player="vlc", aniskip_enabled=enabled)
    assert written == [[native()], []]


def test_mpv_activates_provider_ranges_only_after_duration(monkeypatch):
    from media_cli.playback.mpv import MpvIpc

    ipc = MpvIpc()
    monkeypatch.setattr(ipc, "start", lambda: None)
    monkeypatch.setattr(ipc, "send_cmd", lambda *a: None)
    ipc.load("/tmp/video.mp4", "Title", {}, "", skip_intervals=[native()], skip_lookup=(None, 1))
    assert ipc.skip_intervals == []
    ipc.props["duration"] = 1400
    ipc._refresh_remote_skips()
    assert ipc.skip_intervals == [native()]
    ipc._remote_skip_lookup.close()
