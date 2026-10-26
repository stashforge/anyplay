"""Ready screens must wake preparation even before its result exists."""

import queue
import threading

from media_cli.ui import transition


def test_ready_screen_does_not_wait_on_the_result_only_queue(monkeypatch):
    owner = threading.get_ident()
    release = threading.Event()
    queued = threading.Event()
    real_queue = queue.Queue
    queues = []

    class ObservedQueue(real_queue):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            queues.append(self)

        def put(self, item, block=True, timeout=None):
            super().put(item, block, timeout)
            if callable(item):
                queued.set()

        def get(self, block=True, timeout=None):
            if self is queues[0] and threading.get_ident() == owner:
                # Enqueue the screen after the pump checked its UI queue.
                release.set()
                assert queued.wait(1)
                assert not (block and timeout), "Ready screen cannot wake a result-only queue wait"
            return super().get(block, timeout)

    monkeypatch.setattr(transition.queue, 'Queue', ObservedQueue)

    def prepare():
        assert release.wait(1)
        return transition.call_on_ui_thread(lambda: threading.get_ident())

    assert transition.run_preparation(prepare, tick=lambda: None) == owner
