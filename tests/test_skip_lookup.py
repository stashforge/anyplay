from concurrent.futures import Future

from media_cli.playback.skip_lookup import SkipLookup


def interval(duration=4921, start=4784, end=4873):
    return {"type": "ed", "label": "Ending", "start": start, "end": end, "episode_length": duration}


class Executor:
    def __init__(self):
        self.calls = []

    def submit(self, fn, *args, **kwargs):
        future = Future()
        future.set_running_or_notify_cancel()
        self.calls.append((fn, args, kwargs, future))
        return future

    def shutdown(self, **kwargs):
        pass


def test_candidates_never_activate_until_actual_duration_arrives():
    lookup = SkipLookup()
    lookup.reset(52034, 1, [interval()])
    assert lookup.poll(0) == ()
    assert lookup.poll(4921) == (interval(),)
    lookup.close()


def test_mismatch_retries_actual_duration_without_blocking(monkeypatch):
    executor = Executor()
    monkeypatch.setattr("media_cli.playback.skip_lookup.DaemonExecutor", lambda **kw: executor)
    lookup = SkipLookup()
    lookup.reset(52034, 1, [interval()])
    assert lookup.poll(1465) == ()
    assert executor.calls[0][2] == {"episode_length": 1465}
    assert lookup.poll(1465) == ()
    assert len(executor.calls) == 1
    matching = interval(1465, 1320, 1410)
    executor.calls[0][3].set_result([matching])
    assert lookup.poll(1465) == (matching,)
    assert len(executor.calls) == 1
    lookup.close()


def test_old_episode_completion_never_changes_new_episode(monkeypatch):
    executor = Executor()
    monkeypatch.setattr("media_cli.playback.skip_lookup.DaemonExecutor", lambda **kw: executor)
    lookup = SkipLookup()
    lookup.reset(52034, 1, [])
    lookup.poll(1465)
    lookup.reset(52034, 2, [])
    lookup.poll(1465)
    assert len(executor.calls) == 1  # No second queued/running request.
    executor.calls[0][3].set_result([interval(1465, 1320, 1410)])
    assert lookup.poll(1465) == ()
    assert executor.calls[1][1] == (52034, 2)
    lookup.close()


def test_changed_duration_immediately_removes_previously_valid_skips(monkeypatch):
    executor = Executor()
    monkeypatch.setattr("media_cli.playback.skip_lookup.DaemonExecutor", lambda **kw: executor)
    lookup = SkipLookup()
    lookup.reset(52034, 1, [interval()])
    assert lookup.poll(4921)
    assert lookup.poll(1465) == ()
    lookup.close()


def test_failed_lookup_is_logged_and_not_repeated_each_tick(monkeypatch, caplog):
    executor = Executor()
    monkeypatch.setattr("media_cli.playback.skip_lookup.DaemonExecutor", lambda **kw: executor)
    lookup = SkipLookup()
    lookup.reset(52034, 1, [])
    lookup.poll(1465)
    executor.calls[0][3].set_exception(RuntimeError("broken worker"))
    assert lookup.poll(1465) == ()
    assert lookup.poll(1465) == ()
    assert len(executor.calls) == 1
    assert "broken worker" in caplog.text
    lookup.close()


def test_small_runtime_jitter_does_not_discard_completion_or_repeat_lookup(monkeypatch):
    executor = Executor()
    monkeypatch.setattr("media_cli.playback.skip_lookup.DaemonExecutor", lambda **kw: executor)
    lookup = SkipLookup()
    lookup.reset(52034, 1, [])
    lookup.poll(1465.01)
    matching = interval(1465, 1320, 1410)
    executor.calls[0][3].set_result([matching])
    assert lookup.poll(1465.02) == (matching,)
    assert len(executor.calls) == 1
    lookup.close()


def test_partial_mismatch_retries_without_removing_compatible_interval(monkeypatch):
    executor = Executor()
    monkeypatch.setattr("media_cli.playback.skip_lookup.DaemonExecutor", lambda **kw: executor)
    lookup = SkipLookup()
    matching = interval(1465, 1320, 1410)
    lookup.reset(52034, 1, [matching, interval()])
    assert lookup.poll(1465) == (matching,)
    assert len(executor.calls) == 1
    lookup.close()


def test_fractional_runtime_ending_at_eof_uses_actual_duration(monkeypatch):
    from media_cli.media.aniskip import validated_skip_intervals

    executor = Executor()
    monkeypatch.setattr("media_cli.playback.skip_lookup.DaemonExecutor", lambda **kw: executor)
    matching = interval(1465.4, 1375.4, 1465.4)
    monkeypatch.setattr(
        "media_cli.playback.skip_lookup.fetch_skip_times",
        lambda *args, episode_length: validated_skip_intervals([matching], episode_length),
    )
    lookup = SkipLookup()
    lookup.reset(52034, 1)
    lookup.poll(1465.4)
    fn, args, kwargs, future = executor.calls[0]
    future.set_result(fn(*args, **kwargs))
    assert lookup.poll(1465.4) == (matching,)
    lookup.close()
