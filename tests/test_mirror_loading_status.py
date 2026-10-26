import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest

from media_cli.context import CliFlags, MachineState, UiState
from media_cli.core.terminal import strip_ansi
from media_cli.ui.background_loading import BackgroundLoadingStatus
from media_cli.ui.loading_model import LoadingProgress


@pytest.mark.parametrize("next_state", [
    "complete", "progress", "back", "error", "counted-back", "counted-error",
    "torrent-back", "torrent-error", "torrent-complete", "torrent-handoff", "torrent-combined",
    "hint-complete", "hint-fresh", "hint-added", "hint-cleared",
    "dependency-error", "dependency-alternative",
])
def test_mirror_loading_states_use_shared_status_and_retire(monkeypatch, next_state):
    from media_cli.app import playback_menu as menu

    reads = []
    checks = []
    torrents = []
    alive = [True]
    stats = {"resolved": 0, "failed": 0, "total": 0}
    counted_at_entry = next_state.startswith("counted-")
    if counted_at_entry:
        stats["total"] = 2
    torrent_at_entry = next_state.startswith("torrent-")
    if torrent_at_entry:
        alive[0] = next_state == "torrent-combined"
        if alive[0]:
            stats.update(resolved=1, total=3, status_msg="Resolving provider")
    key = ("show", "1", "sub", "anikoto", "best")
    data = {"episode": {"sourceUrls": []}}
    hint_case = next_state.startswith("hint-")
    if hint_case:
        monkeypatch.setitem(sys.modules, "m3u8", None)
        if next_state != "hint-added":
            data["dependency_hints"] = ["Adaptive playback; install m3u8 for individual qualities."]
    cache = [data]

    class ObservedStatus(BackgroundLoadingStatus):
        def read(self, active, *, progress=None):
            if self._spec.task_id == "checking-sources":
                checks.append((active, progress))
            elif self._spec.task_id == "torrent-search":
                torrents.append((active, progress))
            else:
                reads.append(active)
            return super().read(active, progress=progress)

    monkeypatch.setattr(menu, "BackgroundLoadingStatus", ObservedStatus)
    monkeypatch.setattr(menu.streams, "make_stream_key", lambda *args: key)
    monkeypatch.setattr(menu.streams, "_bg_thread", SimpleNamespace(is_alive=lambda: alive[0]))
    monkeypatch.setattr(menu.streams, "_active_stream_key", key)
    monkeypatch.setattr(menu.streams, "_bg_stats", stats)
    monkeypatch.setattr(menu.streams, "is_stream_cache_ready", lambda *args: False)
    monkeypatch.setattr(menu.streams, "start_bg_resolve", lambda *args: None)
    monkeypatch.setattr(menu, "_torrent_mirror_augmenting", {key} if torrent_at_entry else set())
    monkeypatch.setattr(menu, "_start_torrent_mirror_augmentation", lambda *args: None)
    monkeypatch.setattr(menu.app_core, "_get_cached_ep_data", lambda *args: None if next_state == "hint-fresh" else cache[0])
    if next_state == "hint-fresh":
        monkeypatch.setattr(menu.app_core, "with_loading", lambda *args, **kwargs: data)
    monkeypatch.setattr(menu.app_core, "_set_cached_ep_data", lambda *args: None)
    monkeypatch.setattr(menu.app_core, "_stream_snapshot", lambda *args: [])
    monkeypatch.setattr(menu.app_core, "make_single_show_info_fn", lambda *args: None)
    from media_cli.services.requirements import MissingPythonDependency

    failure = MissingPythonDependency(("bs4",), "Filemoon extraction")
    dependency_case = next_state.startswith("dependency-")
    monkeypatch.setattr(menu.streams, "resolution_feedback", lambda key: (
        (("Some sources unavailable; install beautifulsoup4 for native extraction.",), failure)
        if dependency_case else ((), None)
    ))
    if next_state == "dependency-alternative":
        monkeypatch.setattr(menu.app_core, "_stream_snapshot", lambda *a: [{"link": "good", "source_name": "Good", "resolution": "720p"}])

    def pick(*args, **kwargs):
        if dependency_case:
            assert "install beautifulsoup4" not in strip_ansi(kwargs["header"]), "Loading takes precedence"
            alive[0] = False
            stats.update(resolved=0, failed=1, total=1)
            if next_state == "dependency-error":
                kwargs["live_fn"]()
                pytest.fail("Missing requirements must reach the owning CLI")
            else:
                rows, header, done = kwargs["live_fn"]()
                assert rows and done
                assert "install beautifulsoup4" in strip_ansi(header)
            return -2
        if torrent_at_entry:
            header = strip_ansi(kwargs["header"])
            assert reads == [False]
            assert checks == [(False, None)]
            assert torrents == [(True, LoadingProgress(1, 3) if alive[0] else None)]
            if alive[0]:
                assert "checking (1/3)" in header
                assert "Resolving provider" in header
                stats.update(failed=1)
                assert "checking (2/3)" in strip_ansi(kwargs["live_fn"]()[1])
                assert torrents[-1] == (True, LoadingProgress(2, 3))
            else:
                assert "searching torrent mirrors..." in header
            if next_state in ("torrent-complete", "torrent-handoff"):
                menu._torrent_mirror_augmenting.clear()
                alive[0] = next_state == "torrent-handoff"
                stats.update(resolved=1, total=2)
                _, header, done = kwargs["live_fn"]()
                assert "searching torrent mirrors..." not in strip_ansi(header)
                assert done is not alive[0]
                assert torrents[-1] == (False, None)
                assert checks[-1] == (alive[0], LoadingProgress(1, 2) if alive[0] else None)
        elif counted_at_entry:
            assert "checking (0/2)" in strip_ansi(kwargs["header"])
            assert reads == [False]
            assert checks == [(True, LoadingProgress(0, 2))]
        else:
            assert "Loading sources…" in strip_ansi(kwargs["header"])
            assert reads == [True]
            assert checks == [(False, None)]
        if next_state in ("complete", "progress", "hint-complete", "hint-fresh", "hint-added", "hint-cleared"):
            alive[0] = next_state == "progress"
            stats.update(resolved=1, total=2 if alive[0] else 1)
            _, header, done = kwargs["live_fn"]()
            assert "Loading sources…" not in strip_ansi(header)
            assert ("checking (1/2)" in strip_ansi(header)) is alive[0]
            assert done is not alive[0]
            assert reads == [True, False]
            if hint_case and next_state != "hint-added":
                assert "install m3u8 for individual qualities" in strip_ansi(header)
                assert "Esc=back" in strip_ansi(header)
                assert "may fail" not in strip_ansi(header)
            if next_state in ("hint-added", "hint-cleared"):
                replacement = deepcopy(data)
                if next_state == "hint-added":
                    replacement["dependency_hints"] = ["Adaptive playback; install m3u8 for individual qualities."]
                else:
                    replacement.pop("dependency_hints", None)
                cache[0] = replacement
                _, refreshed_header, _ = kwargs["live_fn"]()
                assert ("individual qualities" in strip_ansi(refreshed_header)) is (next_state == "hint-added")
            if next_state == "progress":
                assert checks[-1] == (True, LoadingProgress(1, 2))
                stats.update(resolved=1, failed=1, total=3)
                _, header, _ = kwargs["live_fn"]()
                assert "checking (2/3)" in strip_ansi(header)
                assert checks[-1] == (True, LoadingProgress(2, 3))
                menu._torrent_mirror_augmenting.add(key)
                kwargs["live_fn"]()
                assert checks[-1] == (False, None)
        if next_state.endswith("error"):
            raise ValueError("Picker stopped")
        return -2

    monkeypatch.setattr(menu, "tui_pick", pick)

    def run():
        return menu.handle_mirrors_state(
            CliFlags(), UiState(), MachineState(show_id="show", current_ep="1"),
            {}, SimpleNamespace(provider="anipm" if hint_case else "anikoto"), "sub", lambda *args: False,
        )

    if next_state == "dependency-error":
        with pytest.raises(MissingPythonDependency) as caught:
            run()
        assert caught.value is failure
    elif next_state.endswith("error"):
        with pytest.raises(ValueError, match="Picker stopped"):
            run()
    else:
        assert run() == "DETAILS"
    assert reads[-1] is False
    assert checks[-1] == (False, None)
    assert torrents[-1] == (False, None)
