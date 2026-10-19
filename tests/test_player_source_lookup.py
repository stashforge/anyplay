import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from media_cli.app import playback_player
from media_cli.ui import player_screen
from media_cli.ui.transition import PreparationCancelled


def test_source_lookup_uses_player_pump_with_detached_inputs(monkeypatch):
    data = {"episode": {"sourceUrls": ["provider"]}}
    show = {"name": "Example", "nested": {"value": 1}}
    owner = threading.current_thread()
    seen = []

    def augment(episode, title, episode_id, ttype, *, cancel_check):
        assert threading.current_thread() is not owner
        assert not cancel_check()
        assert (episode_id, ttype) == ("1", "sub")
        episode["episode"]["sourceUrls"].append("torrent")
        title["nested"]["value"] = 2
        seen.append(cancel_check)
        return episode

    def pump(work, **kwargs):
        assert kwargs == {"poster_manager": "poster", "ui": "ui"}
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(work).result(timeout=1)

    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    monkeypatch.setattr(playback_player.playback_mod, "_augment_episode_sources_with_torrents", augment)
    result = playback_player._lookup_player_sources(data, show, "1", "sub", poster_manager="poster", ui="ui")
    assert result["episode"]["sourceUrls"] == ["provider", "torrent"]
    assert data["episode"]["sourceUrls"] == ["provider"]
    assert show["nested"]["value"] == 1
    assert seen[0]()  # Cooperative work is retired when its pump returns.


def test_cancelled_source_lookup_cannot_mutate_inputs_and_retry_is_fresh(monkeypatch):
    data = {"sources": []}
    show = {"name": "Example"}
    started, release = threading.Event(), threading.Event()
    checks = []
    threads = []

    def augment(episode, title, episode_id, ttype, *, cancel_check):
        checks.append(cancel_check)
        if len(checks) == 1:
            started.set()
            assert release.wait(2)
            assert cancel_check()
            episode["sources"].append("late")
            title["name"] = "Late"
        else:
            assert not cancel_check()
            episode["sources"].append("retry")
        return episode

    def cancel(work, **kwargs):
        thread = threading.Thread(target=work)
        threads.append(thread)
        thread.start()
        assert started.wait(1)
        raise PreparationCancelled

    monkeypatch.setattr(playback_player.playback_mod, "_augment_episode_sources_with_torrents", augment)
    monkeypatch.setattr(player_screen, "run_loading_work", cancel)
    try:
        with pytest.raises(PreparationCancelled):
            playback_player._lookup_player_sources(data, show, "1", "sub")
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=1)
            assert not thread.is_alive()
    assert data == {"sources": []}
    assert show == {"name": "Example"}
    monkeypatch.setattr(player_screen, "run_loading_work", lambda work, **kw: work())
    assert playback_player._lookup_player_sources(data, show, "1", "sub") == {"sources": ["retry"]}
