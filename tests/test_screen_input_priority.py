from contextlib import contextmanager
from types import SimpleNamespace


def test_pending_navigation_precedes_next_frame(monkeypatch):
    from media_cli.ui import screen_chrome as screen, frame_surface, picker, display
    from media_cli.ui.list_screen import ScreenFrame
    events = []
    @contextmanager
    def borrow():
        yield SimpleNamespace(fileno=lambda: 7)
    monkeypatch.setattr(screen, 'borrow_terminal', borrow)
    monkeypatch.setattr(display, 'enter_alt_screen', lambda: None)
    monkeypatch.setattr(frame_surface, 'invalidate_text', lambda: None)
    monkeypatch.setattr(frame_surface, 'compose', lambda *a: '')
    monkeypatch.setattr(picker, 'remember_screen_frame', lambda *a: None)
    monkeypatch.setattr(screen, 'write_terminal', lambda *a: None)
    monkeypatch.setattr(screen.os, 'get_terminal_size', lambda *a: SimpleNamespace(columns=80, lines=24))
    # UP arrives after first frame. DOWN arrives immediately after the first
    # repeat-drain emptiness check, before the next refresh/render.
    ready = iter([True, False, True])
    monkeypatch.setattr(screen.select, 'select', lambda *a: ([7] if next(ready) else [], [], []))
    keys = iter(['UP', 'DOWN'])
    monkeypatch.setattr(screen, 'get_key', lambda *a: next(keys))
    def render(*a):
        events.append('paint')
        return ScreenFrame([''])
    def key(value):
        events.append(value)
        return 'done' if value == 'DOWN' else None
    assert screen.run_framed_screen(render, key, batch_navigation=True) == 'done'
    assert events == ['paint', 'UP', 'DOWN']
