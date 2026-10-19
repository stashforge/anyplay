import threading

import pytest


def test_daemon_executor_returns_results_and_errors():
    from media_cli.core.workers import DaemonExecutor

    pool = DaemonExecutor(max_workers=1)
    try:
        assert pool.submit(lambda: 42).result(timeout=1) == 42

        def fail():
            raise ValueError("worker failed")

        with pytest.raises(ValueError, match="worker failed"):
            pool.submit(fail).result(timeout=1)
    finally:
        pool.shutdown()


def test_daemon_executor_has_bounded_pending_work_and_cancels_it():
    from media_cli.core.workers import DaemonExecutor

    pool = DaemonExecutor(max_workers=1, max_pending=1)
    started, release = threading.Event(), threading.Event()

    def blocked():
        started.set()
        release.wait(3)
        return "done"

    try:
        first = pool.submit(blocked)
        assert started.wait(1)
        queued = pool.submit(lambda: "never")
        overflow = pool.submit(lambda: "overflow")
        with pytest.raises(RuntimeError, match="capacity"):
            overflow.result(timeout=1)
        pool.shutdown(wait=False, cancel_futures=True)
        assert queued.cancelled()
        release.set()
        assert first.result(timeout=1) == "done"
        with pytest.raises(RuntimeError, match="shutdown"):
            pool.submit(lambda: None)
    finally:
        release.set()
        pool.shutdown()


def test_daemon_executor_map_streams_a_batch_larger_than_its_queue():
    from media_cli.core.workers import DaemonExecutor

    with DaemonExecutor(max_workers=2, max_pending=2) as pool:
        assert list(pool.map(lambda value: value * 2, range(20))) == list(range(0, 40, 2))
