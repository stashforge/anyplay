from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from media_cli.media import local_proxy
from media_cli.playback import desktop


@pytest.mark.parametrize("stage", ["startup", "load", "preference", "update", "wait"])
@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt, desktop.PlaybackPreparationCancelled])
def test_startup_failure_releases_accepted_proxy_and_torrent(monkeypatch, stage, failure):
    closed = []
    torrent = SimpleNamespace(path="/tmp/episode.mkv", close=lambda: closed.append("torrent"))
    server = object()

    def step(name, value=None):
        if stage == name:
            raise failure("Interrupted startup")
        return value

    monkeypatch.setattr(local_proxy, "replace_active_local_proxy", lambda value: value)
    monkeypatch.setattr(local_proxy, "cleanup_active_local_proxy", lambda: closed.append("proxy"))
    ipc = SimpleNamespace(
        props={}, load=lambda *a, **kw: step("load"),
        wait_for_playback=lambda *a: step("wait", ("QUIT", 0)),
    )
    expected = nullcontext() if failure is desktop.PlaybackPreparationCancelled else pytest.raises(failure, match="Interrupted startup")
    with expected:
        result = desktop.play_desktop(ipc, "Example", "1", {
            "type": "torrent", "link": "magnet:?xt=urn:btih:test", "requires_proxy": True,
        }, show_id="show", get_resume_time=lambda *a: 0,
            get_preferred_mirror=lambda *a: step("preference", {}),
            update_stream_info=lambda info: step("update"),
            start_player_fn=lambda player: step("startup"),
            prepare_torrent_fn=lambda *a, **kw: torrent,
            prepare_proxy_fn=lambda *a, **kw: ("http://127.0.0.1/video", server))
        assert result[0] == "CANCELLED"
    assert closed == ["proxy", "torrent"]
