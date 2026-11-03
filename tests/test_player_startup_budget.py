"""Slow startup gets a bounded grace period and truthful, single-line status."""

import json
from types import SimpleNamespace

import pytest

from media_cli.playback import mpv
from media_cli.ui import player_screen


@pytest.mark.parametrize('elapsed,expected', [
    (0, 'Loading video… (0s)'),
    (9, 'Loading video… (9s)'),
    (10, 'Taking longer than usual… (10s)'),
    (19, 'Taking longer than usual… (19s)'),
    (20, 'Taking longer than usual… (20s)'),
    (29, 'Taking longer than usual… (29s)'),
])
def test_startup_status_uses_elapsed_time_then_final_countdown(elapsed, expected):
    assert player_screen._playback_status_text({'_startup_elapsed': elapsed, '_startup_timeout': 30}) == expected


@pytest.mark.parametrize('props,expected', [
    ({}, '▶ Playing'), ({'pause': True}, '⏸ Paused'),
    ({'_startup_elapsed': None}, '▶ Playing'),
])
def test_playback_status_replaces_startup_countdown(props, expected):
    assert player_screen._playback_status_text(props) == expected


@pytest.mark.parametrize('outcome,ready_at,expected', [
    ('ready', 19, 'EOF'), ('ready', 29, 'EOF'),
    ('never', 75, 'QUIT'), ('error', 1, 'ERROR'),
])
def test_startup_wait_only_ends_on_readiness_user_exit_or_explicit_error(monkeypatch, outcome, ready_at, expected):
    clock = [0.0]
    incoming = []
    snapshots = []
    emitted = [False]
    player = mpv.MpvIpc(redraw_callback=lambda props: snapshots.append(dict(props)))
    player.process = SimpleNamespace(poll=lambda: None)
    player.running = True

    def recv(size):
        if incoming:
            return incoming.pop(0)
        raise BlockingIOError

    player.client = SimpleNamespace(recv=recv)
    monkeypatch.setattr(player, 'start', lambda: None)
    monkeypatch.setattr(player, 'send_cmd', lambda *args: None)
    monkeypatch.setattr(mpv.sys, 'stdin', SimpleNamespace(fileno=lambda: 0))
    monkeypatch.setattr(mpv.os, 'isatty', lambda fd: False)

    def tick():
        clock[0] += .25
        return clock[0]

    def select(*args):
        if not emitted[0] and ready_at is not None and clock[0] >= ready_at:
            emitted[0] = True
            events = [{'event': 'end-file', 'reason': 'quit' if outcome == 'never' else 'error'}]
            if outcome == 'ready':
                events = [{'event': 'file-loaded'},
                          {'event': 'property-change', 'name': 'duration', 'data': 100},
                          {'event': 'property-change', 'name': 'playback-time', 'data': 100},
                          {'event': 'end-file', 'reason': 'eof'}]
            incoming.append((''.join(json.dumps(event) + '\n' for event in events)).encode())
        return ([player.client] if incoming else [], [], [])

    monkeypatch.setattr(mpv.time, 'monotonic', tick)
    monkeypatch.setattr(mpv.select, 'select', select)
    player.load('https://cdn.example.com/video.m3u8', 'Example', {}, '')
    result, _ = player.wait_for_playback({}, '1', 1, None)
    assert result == expected
    if outcome == 'never':
        assert 75 <= clock[0] < 77
    elif outcome == 'error':
        assert clock[0] < 3
    else:
        assert player.props.get('_startup_elapsed') is None
        assert any(10 <= row.get('_startup_elapsed', -1) < 20 for row in snapshots if row.get('_startup_elapsed') is not None)


def test_partial_player_redraw_clears_countdown_on_ready(monkeypatch):
    state = {'_row_state': 10, '_row_bar': 11, '_row_time': 12,
             '_cached_w': 80, '_cached_h': 30}
    monkeypatch.setattr(player_screen, '_player_ui_state', state)
    monkeypatch.setattr(player_screen.os, 'get_terminal_size', lambda fd: SimpleNamespace(columns=80, lines=30))
    monkeypatch.setattr(player_screen.sys, 'stdin', SimpleNamespace(fileno=lambda: 0))
    frames = []
    monkeypatch.setattr(player_screen, '_safe_stdout_write', frames.append)
    player_screen.update_mpv_props({'_mirror_wait_message': 'Still loading… Press Enter to try the next mirror.'})
    assert 'Press Enter' in frames[-1]
    player_screen.update_mpv_props({'_startup_elapsed': None, 'playback-time': 1, 'duration': 100})
    assert '▶ Playing' in frames[-1]
    assert 'Switching' not in frames[-1]
    assert '\033[10;1H\033[2K' in frames[-1]
