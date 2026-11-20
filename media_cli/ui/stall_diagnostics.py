"""Debug-only sampling of a stalled UI; records code locations, never locals/input."""
import logging
import sys
import threading
import time
import traceback

_log = logging.getLogger(__name__)


class ListStallDiagnostics:
    def __init__(self, *, logger=_log, clock=time.monotonic, threshold=.5):
        self.logger, self.clock, self.threshold = logger, clock, threshold
        self.enabled = logger.isEnabledFor(logging.DEBUG)
        self.owner = threading.get_ident()
        self.state = ('starting', clock())
        self.reported = None
        self.stop = threading.Event()
        self.thread = None

    def stage(self, name):
        if self.enabled:
            self.state = (name, self.clock())

    def sample(self):
        state = self.state
        elapsed = self.clock() - state[1]
        if not self.enabled or elapsed < self.threshold or state == self.reported:
            return
        self.reported = state
        frames = sys._current_frames()
        locations = []
        for thread_id, frame in frames.items():
            if thread_id == threading.get_ident() and thread_id != self.owner:
                continue
            stack = traceback.extract_stack(frame, limit=8)
            label = 'UI' if thread_id == self.owner else 'worker'
            locations.append(label + ': ' + ' <- '.join(
                f'{entry.filename}:{entry.lineno}:{entry.name}' for entry in stack))
        self.logger.debug('UI stall snapshot: stage=%s elapsed_ms=%.1f stacks=%s',
                          state[0], elapsed*1000, ' | '.join(locations))

    def __enter__(self):
        if self.enabled:
            def watch():
                while not self.stop.wait(.1):
                    self.sample()
            self.thread = threading.Thread(target=watch, name='anyplay-ui-stall-watch', daemon=True)
            self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=.2)
