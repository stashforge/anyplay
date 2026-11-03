import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from media_cli.app import playback_player
from media_cli.ui import player_screen
from media_cli.ui.transition import PreparationCancelled

KEY = ("show", "1", "sub", "example", "best")


def test_probe_runs_on_worker_but_commits_on_ui_owner(monkeypatch):
    owner = threading.current_thread()
    candidates = ({"link": "dead"}, {"link": "alive"})
    pruned = []

    def probe(candidate, *, timeout):
        assert threading.current_thread() is not owner
        assert timeout == 0.8
        return candidate["link"] == "alive"

    def prune(key, link):
        assert threading.current_thread() is owner
        pruned.append((key, link))

    def pump(work, **kwargs):
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(work).result(timeout=1)

    monkeypatch.setattr(player_screen, "run_loading_work", pump)
    monkeypatch.setattr(playback_player.app_core, "ping_stream_liveness", probe)
    monkeypatch.setattr(playback_player.app_core, "_prune_dead_stream", prune)
    assert playback_player._probe_player_mirrors(candidates, KEY) is candidates[1]
    assert candidates[1]["validated_at"] > 0
    assert pruned == [(KEY, "dead")]


def test_probe_keeps_three_candidate_limit_and_first_candidate_fallback(monkeypatch):
    candidates = tuple({"link": str(i)} for i in range(4))
    probes, pruned = [], []
    monkeypatch.setattr(player_screen, "run_loading_work", lambda work, **kw: work())
    monkeypatch.setattr(playback_player.app_core, "ping_stream_liveness", lambda c, **kw: probes.append(c["link"]) or False)
    monkeypatch.setattr(playback_player.app_core, "_prune_dead_stream", lambda k, link: pruned.append(link))
    assert playback_player._probe_player_mirrors(candidates, KEY) is candidates[0]
    assert probes == pruned == ["0", "1", "2"]
    assert all("validated_at" not in c for c in candidates)


def test_recently_validated_mirror_skips_network_probe(monkeypatch):
    candidate = {"link": "recent", "validated_at": playback_player.time.time()}
    monkeypatch.setattr(player_screen, "run_loading_work", lambda work, **kw: work())
    monkeypatch.setattr(playback_player.app_core, "ping_stream_liveness", lambda *a, **kw: pytest.fail("Recent mirror probed"))
    assert playback_player._probe_player_mirrors((candidate,), KEY) is candidate


@pytest.mark.parametrize("recovery", [False, True])
def test_cancelled_probe_cannot_commit_late_result_and_retry_is_fresh(monkeypatch, recovery):
    candidate = {"link": "alive"}
    started, release = threading.Event(), threading.Event()
    threads = []
    pruned = []

    def probe(*args, **kwargs):
        started.set()
        assert release.wait(2)
        return True

    def cancel(work, **kwargs):
        thread = threading.Thread(target=work)
        threads.append(thread)
        thread.start()
        if recovery:
            thread.join(1)
            assert not thread.is_alive()
        else:
            assert started.wait(1)
        raise PreparationCancelled

    monkeypatch.setattr(player_screen, "run_loading_work", cancel)
    monkeypatch.setattr(playback_player.app_core, "ping_stream_liveness", probe)
    monkeypatch.setattr(playback_player.app_core, "_prune_dead_stream", lambda *a: pruned.append(a))
    try:
        with pytest.raises(PreparationCancelled):
            playback_player._probe_player_mirrors((candidate,), KEY, recovery=recovery)
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=1)
            assert not thread.is_alive()
    assert candidate == {"link": "alive"}
    assert pruned == []
    monkeypatch.setattr(player_screen, "run_loading_work", lambda work, **kw: work())
    assert playback_player._probe_player_mirrors((candidate,), KEY, recovery=recovery) is candidate
    assert ("validated_at" in candidate) is (not recovery)


def test_download_probe_bypasses_player_ui(monkeypatch):
    candidate = {"link": "file"}
    monkeypatch.setattr(player_screen, "run_loading_work", lambda *a, **kw: pytest.fail("Download entered player UI"))
    monkeypatch.setattr(playback_player.app_core, "ping_stream_liveness", lambda *a, **kw: True)
    assert playback_player._probe_player_mirrors((candidate,), KEY, download=True) is candidate


def test_recovery_skips_probes_without_updating_validation(monkeypatch):
    now = playback_player.time.time()
    candidates = tuple({"link": str(i), "validated_at": now} for i in range(4))
    probes = []
    monkeypatch.setattr(player_screen, "run_loading_work", lambda work, **kw: work())
    monkeypatch.setattr(playback_player.app_core, "ping_stream_liveness", lambda c, **kw: probes.append(c["link"]) or c["link"] == "3")
    monkeypatch.setattr(playback_player.app_core, "_prune_dead_stream", lambda *a: pytest.fail("Recovery must not prune"))
    assert playback_player._probe_player_mirrors(candidates, KEY, recovery=True) is candidates[0]
    assert probes == []
    assert all(c["validated_at"] == now for c in candidates)


def test_recovery_keeps_first_candidate_fallback_when_every_probe_fails(monkeypatch):
    candidates = ({"link": "first"}, {"link": "second"})
    monkeypatch.setattr(player_screen, "run_loading_work", lambda work, **kw: work())
    monkeypatch.setattr(playback_player.app_core, "ping_stream_liveness", lambda *a, **kw: False)
    monkeypatch.setattr(playback_player.app_core, "_prune_dead_stream", lambda *a: pytest.fail("Recovery must not prune"))
    assert playback_player._probe_player_mirrors(candidates, KEY, recovery=True) is candidates[0]
    assert candidates == ({"link": "first"}, {"link": "second"})


def test_recovery_selects_without_extra_network_roundtrip(monkeypatch, caplog):
    import logging

    candidates = ({'link': 'https://cdn.example/video?secret=token', 'source_name': 'Ok.ru'},)
    monkeypatch.setattr(player_screen, 'run_loading_work', lambda work, **kw: work())
    monkeypatch.setattr(playback_player.app_core, 'ping_stream_liveness',
                        lambda *a, **kw: pytest.fail('Recovery performed another HTTP probe'))
    with caplog.at_level(logging.DEBUG, logger=playback_player.__name__):
        assert playback_player._probe_player_mirrors(candidates, KEY, recovery=True) is candidates[0]
    assert 'probe=skipped' in caplog.text
    assert 'secret=token' not in caplog.text
